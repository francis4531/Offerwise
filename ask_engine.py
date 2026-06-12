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
