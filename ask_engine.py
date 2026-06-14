"""
ask_engine.py — shared "Ask your report" grounding engine (v5.89.154)

One place that turns a buyer's question + a context bundle into a grounded,
plain-English answer. Used by the no-login on-ramp (/api/try/chat), the full
report (/api/report/chat), and the shared view (/api/share/<token>/chat) so the
grounding rules, tone, and formatting are identical on every surface.
"""
import json
import logging

logger = logging.getLogger(__name__)

# Hard ceiling on context handed to the model (keeps latency/cost bounded).
MAX_CONTEXT_CHARS = 60_000
_MAX_ANALYSIS_JSON = 30_000

# The single source of truth for how OfferWise answers in chat — grounding,
# tone, and formatting. Mirrors the on-ramp prompt so every surface matches.
SYSTEM_RULES = (
    "You are Scout, OfferWise's calm, plain-English home-buying guide helping a prospective "
    "buyer understand their property analysis. Answer ONLY from the context provided "
    "below. When you reference something, point to where it appears (the inspection, "
    "the disclosure, or our analysis). If the context does not address the question, "
    "say so plainly and suggest the relevant next step rather than guessing. Never "
    "invent findings, costs, or facts that are not in the context. You are not a lawyer "
    "or a licensed inspector — help them understand and prepare to negotiate, but do not "
    "give legal advice. Write the way a knowledgeable friend would explain it: warm, "
    "plain, and conversational, in short paragraphs. You may use a short bullet list or "
    "bold a key term when it genuinely helps, but never use section headers or "
    "horizontal-rule separators like '---'. Keep it concise."
)


def grounded_answer(question, context_text, *, max_tokens=600):
    """Return a grounded, conversational answer to `question` from `context_text`."""
    from ai_client import get_ai_response
    ctx = (context_text or '')
    if len(ctx) > MAX_CONTEXT_CHARS:
        ctx = ctx[:MAX_CONTEXT_CHARS]
    q = (question or '').strip()
    prompt = (
        'CONTEXT:\n"""\n' + ctx + '\n"""\n\n'
        "BUYER'S QUESTION: " + q + "\n\n"
        "Answer using only the context above."
    )
    return get_ai_response(prompt, max_tokens=max_tokens, temperature=0, system=SYSTEM_RULES)


# --- AI findings extraction (on-ramp) -------------------------------------
# The keyword parser only understands inspection reports; on a seller
# disclosure it returns nothing or surfaces the form's pre-printed boilerplate.
# This reads ANY property document with the model and returns the genuinely
# buyer-relevant findings, grounded strictly in the text.

EXTRACT_RULES = (
    "You are Scout, OfferWise's plain-English home-buying analyst. You are given the full "
    "text of ONE property document a buyer uploaded — a home inspection report, a seller's "
    "disclosure (e.g. a TDS or SPQ), a natural-hazard disclosure, or similar. Identify the "
    "items that genuinely matter to a buyer deciding what to offer: defects, damage, safety or "
    "structural issues, deferred maintenance, environmental or natural hazards, and anything the "
    "seller has affirmatively disclosed as a known problem for THIS property. Follow these rules "
    "strictly:\n"
    "- Use ONLY what is actually stated in the document. Never invent, never infer beyond the "
    "text, never add generic advice. If the document is a standard form, do NOT surface its "
    "pre-printed questions, instructions, or definitions — only items the document indicates are "
    "actually present, disclosed, or flagged for this property.\n"
    "- severity is exactly one of: critical, major, moderate. critical = safety, structural, or "
    "financial dealbreaker; major = significant cost or concern; moderate = worth noting. Never "
    "include minor or informational items.\n"
    "- For each finding provide: a short title (3 to 6 words, no trailing punctuation); a single "
    "relevant emoji as the icon; an integer cost in US dollars estimating the likely repair or "
    "remediation cost for that item (a reasonable rough estimate is fine; never 0); a detail that "
    "is one complete, specific sentence describing the actual issue; and a why that is one "
    "complete sentence on why it matters and what the buyer should verify. No fragments, no enum "
    "labels, no raw form language, no ellipses.\n"
    "- Return at most 3 findings, most serious first. If the document genuinely raises nothing "
    "significant, return an empty findings list — never pad it.\n"
    "- No-disclosure / as-is case (seller disclosures only): if the document is a completed seller "
    "disclosure form (such as a California TDS or SPQ) in which the seller marks NO known defects "
    "across all or nearly all categories, and/or states the property is sold 'as-is' or in its "
    "'present' or 'current' condition or that there are 'no material defects known,' then this "
    "near-total absence of disclosure is itself the finding the buyer needs. Return exactly one "
    "finding for it: severity moderate; a short title such as 'Seller Disclosed No Defects' or "
    "'Sold As-Is, Little Disclosed'; cost 0 (there is no repair to price here); a detail stating in "
    "one sentence what the form actually shows; and a why explaining in one sentence that a blanket "
    "no-defects or as-is disclosure shifts repair risk onto the buyer, so the absence of disclosures "
    "must not be read as the absence of problems and an independent inspection is essential. Set "
    "grade to C. Never apply this to inspection reports or non-disclosure documents.\n"
    "- summary: one sentence (18 words or fewer) the buyer reads first, framing what the document "
    "shows; if the seller disclosed little or sells as-is, say that plainly and do not call it "
    "clean; otherwise if nothing significant, say so honestly.\n"
    "- grade: a single letter A to F for the overall repair burden (A = clean, F = severe).\n"
    "Output ONLY a JSON object, no prose and no markdown fences:\n"
    '{"summary":"...","grade":"A","findings":[{"severity":"critical|major|moderate",'
    '"title":"...","icon":"\U0001f527","cost":12000,"detail":"...","why":"..."}]}'
)

_EXTRACT_SEVERITIES = ('critical', 'major', 'moderate')


def _parse_findings_json(raw):
    """Tolerant parse of the model's JSON object. Returns a normalized dict
    {'summary', 'grade', 'findings':[{severity,title,icon,cost,detail,why}]} or
    None if it can't be parsed. Every field has a safe fallback so a partial
    response still renders."""
    if not raw or not isinstance(raw, str):
        return None
    a, b = raw.find('{'), raw.rfind('}')
    if a == -1 or b <= a:
        return None
    try:
        obj = json.loads(raw[a:b + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    summary = obj.get('summary')
    summary = summary.strip() if isinstance(summary, str) else ''
    out = []
    for f in (obj.get('findings') or []):
        if not isinstance(f, dict):
            continue
        sev = str(f.get('severity', '')).strip().lower()
        sev = {'high': 'major', 'severe': 'critical', 'medium': 'moderate',
               'low': 'moderate'}.get(sev, sev)
        detail = f.get('detail') or f.get('text') or ''
        detail = detail.strip() if isinstance(detail, str) else ''
        if sev not in _EXTRACT_SEVERITIES or len(detail) < 12:
            continue
        title = f.get('title')
        title = title.strip() if isinstance(title, str) else ''
        if not title:
            title = ' '.join(detail.split()[:5])
        icon = f.get('icon')
        icon = icon.strip() if isinstance(icon, str) else ''
        if not icon or len(icon) > 6:
            icon = '\u26a0\ufe0f'
        why = f.get('why')
        why = why.strip() if isinstance(why, str) else ''
        cost = f.get('cost')
        try:
            cost = int(round(float(cost))) if cost is not None else 0
        except (TypeError, ValueError):
            cost = 0
        if cost < 0:
            cost = 0
        out.append({'severity': sev, 'title': title, 'icon': icon,
                    'cost': cost, 'detail': detail, 'why': why})
        if len(out) >= 3:
            break
    grade = str(obj.get('grade', '')).strip().upper()[:1]
    if grade not in ('A', 'B', 'C', 'D', 'F'):
        worst = out[0]['severity'] if out else None
        grade = {'critical': 'D', 'major': 'C', 'moderate': 'B'}.get(worst, 'A')
    return {'summary': summary, 'grade': grade, 'findings': out}


def extract_findings(text):
    """AI-extract the buyer-relevant findings from ANY property document.

    Returns {'summary', 'grade', 'findings':[{severity,title,icon,cost,detail,
    why}]} on a successful model call (findings may be an empty list when the
    document genuinely raises nothing significant), or None when the model is
    unavailable or its response can't be parsed — callers should fall back to the
    keyword parser in that case.
    """
    from ai_client import get_ai_response
    doc = (text or '')
    if len(doc.strip()) < 80:
        return None
    if len(doc) > MAX_CONTEXT_CHARS:
        doc = doc[:MAX_CONTEXT_CHARS]
    prompt = (
        'DOCUMENT:\n"""\n' + doc + '\n"""\n\n'
        'Extract the findings exactly as specified in your instructions. '
        'Return only the JSON object.'
    )
    try:
        raw = get_ai_response(prompt, max_tokens=1100, temperature=0, system=EXTRACT_RULES)
    except Exception as e:
        logging.warning("extract_findings: model unavailable (%s)", e)
        return None
    return _parse_findings_json(raw)


def context_from_document(text):
    """On-ramp: a single uploaded/pasted document."""
    return "DOCUMENT THE BUYER UPLOADED:\n" + (text or '')


def context_from_analysis(analysis, documents=None):
    """Full report: the OfferWise analysis JSON plus the property's document text."""
    parts = []
    try:
        result = json.loads(analysis.result_json or '{}')
    except Exception:
        result = {}
    if result:
        parts.append("OFFERWISE ANALYSIS (our findings, risk, and offer reasoning):")
        parts.append(json.dumps(result, ensure_ascii=False)[:_MAX_ANALYSIS_JSON])
    for d in (documents or []):
        txt = getattr(d, 'extracted_text', None)
        if txt:
            label = (getattr(d, 'document_type', '') or 'document').replace('_', ' ').upper()
            fname = getattr(d, 'filename', '') or ''
            parts.append("\n--- " + label + " (" + fname + ") ---\n" + txt)
    return "\n\n".join(parts)


def context_from_snapshot(snapshot):
    """Shared view: the captured analysis snapshot."""
    try:
        return "SHARED OFFERWISE ANALYSIS:\n" + json.dumps(snapshot, ensure_ascii=False)
    except Exception:
        return "SHARED OFFERWISE ANALYSIS:\n" + str(snapshot)


def context_from_risk_general():
    """Risk Check page, before a scan: general guidance about the tool + risks."""
    return (
        "GENERAL CONTEXT — OfferWise Risk Check.\n"
        "The buyer is on OfferWise's free Risk Check, which scans any US address across "
        "public government databases (FEMA flood maps, USGS seismic and fault data, "
        "CAL FIRE wildfire severity, EPA Superfund / toxic-release / cleanup records, "
        "radon zones, and federal disaster declarations) to surface property risks a "
        "seller does not have to disclose. It returns a risk grade, an estimated dollar "
        "exposure, and a list of specific risks. It is free and needs no signup.\n\n"
        "What it does NOT do: it does not read the seller's disclosure packet or a home "
        "inspection report. Cross-referencing those documents to catch contradictions and "
        "calculate a defensible offer price is the full OfferWise analysis (a free first "
        "analysis after creating an account).\n\n"
        "In this general mode you may draw on widely-known, general home-buying and "
        "property-risk knowledge to educate the buyer — for example explaining what a FEMA "
        "flood zone, an active fault, radon, or a Superfund site is, or what a seller "
        "generally must disclose. You must NOT invent facts about any specific property: "
        "if asked about a particular address or its results, tell the buyer to enter the "
        "address above and run the scan, since you can only speak to a property once it has "
        "been scanned. Keep answers short and practical, and point them toward scanning an "
        "address or starting the full analysis when that is the natural next step."
    )


def context_from_risk_result(result):
    """Risk Check result page: the scan's findings for one specific address."""
    if not result:
        return context_from_risk_general()
    parts = ["OFFERWISE RISK SCAN for this property (from public government databases):"]
    addr = result.get('address') or ''
    if addr:
        parts.append("Address: " + addr)
    try:
        ex = int(result.get('risk_exposure') or 0)
    except Exception:
        ex = 0
    parts.append(
        "Overall risk grade: " + str(result.get('risk_grade') or '?')
        + ". Estimated undisclosed exposure: $" + format(ex, ',')
        + " across " + str(result.get('risk_count') or 0) + " risk(s)."
    )
    for r in (result.get('risks') or []):
        line = "- " + str(r.get('title', '')) + " [" + str(r.get('level', '')) + "]"
        try:
            if r.get('cost'):
                line += ", est. $" + format(int(r.get('cost') or 0), ',')
        except Exception:
            pass
        if r.get('detail'):
            line += ": " + str(r.get('detail'))
        if r.get('seller_hide'):
            line += " (Why it is often not disclosed: " + str(r.get('seller_hide')) + ")"
        parts.append(line)
    for key in ('disaster_summary', 'earthquake_summary', 'radon', 'epa_environmental'):
        v = result.get(key)
        if v:
            parts.append(str(key).replace('_', ' ').title() + ": "
                         + (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
    parts.append(
        "\nThis scan reads PUBLIC databases only. It does NOT include the seller's "
        "disclosure or a home inspection report — cross-referencing those for "
        "contradictions and a defensible offer price is the full OfferWise analysis. "
        "Answer the buyer's questions about these findings, explain what they mean and "
        "how serious they are, and help them prepare to negotiate. You may use general "
        "home-buying knowledge to explain a risk category, but do not invent property "
        "facts beyond what is listed above."
    )
    return "\n".join(parts)
