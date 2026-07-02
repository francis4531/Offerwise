"""
LLM-based disclosure extractor — the other half of the cross-reference moat.

The inspection extractor reads what the INSPECTOR found. This reads what the
SELLER said in the disclosure packet (TDS / SPQ / NHD / addenda) and maps it to
the checklist's controlled vocabulary — so the reasoning engine can compare the
two sides and produce the moat sentence: "the seller disclosed X / answered
clean / said nothing, and the inspection found Y."

Output feeds the pipeline's field_readings path (map_field_to_claim), which tags
these claims with a disclosure source_form (TDS/SPQ) — NOT 'INSPECTION' — so the
disclosure_status derivation treats them as the seller's side. That is what
yields corroborated / contradiction / undisclosed:

  seller disclosed a concern (value 'yes')  + inspection found it   -> corroborated
  seller answered CLEAN     (value 'no')     + inspection found it   -> contradiction
  seller silent (item omitted)               + inspection found it   -> undisclosed

Because 'no' (an affirmative clean answer) is what creates contradictions — the
highest-leverage status — this extractor captures BOTH disclosed concerns and
affirmative clean answers, not just disclosed defects.

Vocabulary is constrained to the DISCLOSURE-addressable items (the form-field
map ∩ the resolved checklist): a TDS/SPQ can only speak to items it actually
covers. Anything the model can't map is dropped, never invented. Never raises.
"""
from __future__ import annotations
from model_config import HAIKU

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DISCLOSURE_EXTRACT_MODEL = os.environ.get("DISCLOSURE_EXTRACT_MODEL", HAIKU)


def _disclosure_addressable_ids(checklist_ids: List[str]) -> List[str]:
    """Items a TDS/SPQ can actually speak to: form-field-map ∩ resolved checklist.
    Falls back to the full checklist if the map can't be loaded."""
    try:
        from .form_field_map import load_form_field_map
        mapped = set(load_form_field_map().item_ids())
        addressable = [i for i in checklist_ids if i in mapped]
        return addressable or list(checklist_ids)
    except Exception as e:
        logger.warning("disclosure extract: form map unavailable (%s); using full checklist", e)
        return list(checklist_ids)


def _build_prompt(addressable_ids: List[str], disclosure_text: str) -> str:
    ids_block = "\n".join(f"  - {i}" for i in addressable_ids)
    text = disclosure_text[:60000]
    return f"""You are extracting what a home SELLER disclosed, from a California-style \
real-estate disclosure packet (Transfer Disclosure Statement, Seller Property \
Questionnaire, Natural Hazard Disclosure, and addenda). The packet mixes \
checkbox answers (Yes/No) with handwritten/typed explanations. Map what the \
seller stated to the SINGLE best-matching checklist item id from the controlled \
list below.

CONTROLLED CHECKLIST ITEM IDS (map ONLY to these — never invent an id):
{ids_block}

For each item the seller actually addressed, output an object with:
  - item_id: exact id from the list above
  - value: "yes" if the seller DISCLOSED a concern/defect/problem/known issue for
    this item (checked Yes, or wrote an explanation describing a problem);
    "no" if the seller AFFIRMATIVELY answered clean/none/not-aware for this item
    (explicitly checked No / "not aware of").
  - evidence: a SHORT quote or paraphrase (<=15 words) of what the seller stated.

Rules:
- Capture BOTH disclosed concerns ("yes") AND affirmative clean answers ("no").
  The clean answers matter: they are what an inspection can later contradict.
- Map only what the seller actually stated. Do not infer. If the packet does not
  address an item, omit it entirely.
- One object per checklist item (the strongest/most specific statement).
- Match the seller's words to the item's meaning: e.g. "water leak from shower /
  master bath" -> a bath water item; "kitchen floor water" -> a kitchen water
  item; "no environmental hazards / not aware of asbestos" -> the environmental
  item with value "no"; a disclosed roof leak -> the roof leak item.
- Be honest: if uncertain, omit rather than guess.

Respond with ONLY a JSON array of these objects. No preamble, no markdown fences.

DISCLOSURE PACKET TEXT:
{text}
"""


def extract_disclosure_findings_llm(
    disclosure_text: str,
    checklist_ids: List[str],
    *,
    client: Any = None,
    model: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Read any disclosure packet text -> disclosure field_readings via one LLM call.

    Returns field_readings the pipeline consumes on the disclosure side:
      [{item_id, value, raw_text}]  (value 'yes' = disclosed concern, 'no' = clean)
    Constrained to disclosure-addressable ids. Never raises — returns [] on any
    failure (the cross-reference then treats those items as undisclosed).
    """
    if not disclosure_text or not checklist_ids:
        return []

    addressable = _disclosure_addressable_ids(checklist_ids)
    allowed = set(addressable)
    prompt = _build_prompt(addressable, disclosure_text)

    from ai_json import call_ai_json
    parsed = call_ai_json(
        prompt,
        max_tokens=6000,
        temperature=0,
        model=model or DISCLOSURE_EXTRACT_MODEL,
        ai_client=client,
        endpoint='disclosure-extract',
        retry_on_truncation=True,
        max_tokens_ceiling=12000,
    )
    if not parsed.ok or not isinstance(parsed.data, list):
        logger.warning(
            "disclosure LLM extract: unparseable or not a list "
            "(stop_reason=%s truncated=%s chars=%s err=%s)",
            parsed.stop_reason, parsed.truncated, parsed.output_chars, parsed.error,
        )
        return []

    seen: Dict[str, Dict[str, str]] = {}
    for obj in parsed.data:
        if not isinstance(obj, dict):
            continue
        iid = (obj.get("item_id") or "").strip()
        if iid not in allowed:
            continue
        value = "yes" if str(obj.get("value", "")).lower() == "yes" else "no"
        reading = {
            "item_id": iid,
            "value": value,
            "raw_text": str(obj.get("evidence", iid))[:300],
        }
        # prefer a disclosed concern ('yes') over a clean answer ('no') for the
        # same item — the concern is the more consequential statement.
        prev = seen.get(iid)
        if prev is None or (value == "yes" and prev.get("value") == "no"):
            seen[iid] = reading
    return list(seen.values())
