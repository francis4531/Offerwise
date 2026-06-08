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
    "You are OfferWise, a calm, plain-English home-buying guide helping a prospective "
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
