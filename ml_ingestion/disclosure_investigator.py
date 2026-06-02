"""Path 5 — Disclosure source investigation framework.

Background (v5.86.94):
  Path 5 of the geographic coverage roadmap: extract finding-like content
  from publicly-available seller disclosure documents. Harder than Path 2
  (crawlers) because most disclosures are private between buyer/seller.

  Before building extractors we need to confirm which disclosure sources
  are actually public AND yield free-text finding content (not just
  binary flags).

  This module is NOT code-to-execute. It's a structured investigation
  framework — one class per candidate source, filled in as we research.
  When a source is verified viable, we graduate it to a real extractor
  that subclasses BaseDisclosureExtractor.

  Status of each source is one of:
    - 'unverified'     : haven't investigated yet
    - 'investigating'  : actively researching
    - 'viable'         : confirmed public + free-text available; ready to build
    - 'rejected'       : investigation showed no usable data
    - 'built'          : extractor implemented and running

Target: 3-4 viable sources delivering ~500-1000 high-quality real inspection
rows over Weeks 2-6 of the coverage roadmap.

Research protocol per source (do this OUT of band, then fill in this file):
  1. Is the record public? Cite statute or policy.
  2. Is individual record text accessible online? Or only aggregate/statistical?
  3. Does it contain free-text finding descriptions (what we want) or only
     checkbox/binary disclosure flags (not useful as training data)?
  4. What's the volume? (10s, 100s, or 1000s of records per year?)
  5. What's the format? (HTML search, PDF downloads, spreadsheet exports, API?)
  6. Any TOS or rate-limiting concerns?
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DisclosureSource:
    """One candidate source for public disclosure/inspection document extraction."""

    name: str
    state: str                           # 2-letter code
    status: str = 'unverified'           # unverified | investigating | viable | rejected | built
    url_starting_point: str = ''         # where to begin research

    # Findings from investigation (fill these in as research proceeds)
    legality_notes: str = ''             # statute citation, legal basis
    data_shape: str = ''                 # 'free-text' | 'flags-only' | 'mixed' | 'unknown'
    estimated_annual_volume: Optional[int] = None
    record_format: str = ''              # 'html' | 'pdf' | 'json' | 'scanned-pdf' | 'unknown'
    tos_concerns: str = ''
    sample_url: str = ''                 # URL of a sample record we inspected

    # Decision
    go_nogo_reasoning: str = ''
    next_action: str = ''                # what needs to happen next for this source

    def summary(self) -> str:
        return (f'{self.state} · {self.name} [{self.status}] '
                f'vol={self.estimated_annual_volume or "?"} '
                f'shape={self.data_shape or "?"} '
                f'fmt={self.record_format or "?"}')


# Candidate sources to investigate. Filled in iteratively. Starting set was
# scoped from research-backed priorities but needs field verification before
# any extractor is built against a source.
CANDIDATE_SOURCES: list[DisclosureSource] = [

    DisclosureSource(
        name='Chicago Residential Property Disclosure',
        state='IL',
        status='unverified',
        url_starting_point='https://www.illinois.gov/idfpr/DRE/',
        next_action=(
            'Investigate whether filled-out Residential Real Property Disclosure '
            '(IL Rev Stat 765 ILCS 77) forms are publicly recorded or remain '
            'private between parties. Cook County recorder may or may not archive.'
        ),
    ),

    DisclosureSource(
        name='NJ Township Certificate of Occupancy Records',
        state='NJ',
        status='unverified',
        url_starting_point='https://www.nj.gov/dca/codes/',
        next_action=(
            'Some NJ townships require pre-sale inspections that get filed with '
            'the township clerk. Investigate Trenton, Newark, Jersey City, Paterson. '
            'Records may be OPRA-requestable but not online.'
        ),
    ),

    DisclosureSource(
        name='MA Title 5 Septic Inspection Records',
        state='MA',
        status='unverified',
        url_starting_point='https://www.mass.gov/info-details/title-5-onsite-sewage-treatment-system-regulation',
        next_action=(
            'MA requires Title 5 inspections before sale of properties with '
            'septic systems. Inspection reports filed with local board of health. '
            'Investigate whether these are online or only paper in township files.'
        ),
    ),

    DisclosureSource(
        name='FL Sunshine Law Property Records',
        state='FL',
        status='unverified',
        url_starting_point='https://www.flsenate.gov/Laws/Statutes/2023/Chapter119/',
        next_action=(
            'FL has strong public records law. County property appraisers '
            'sometimes publish inspection-related records. Investigate Miami-Dade, '
            'Broward, Palm Beach property appraiser sites for inspection data '
            'beyond just assessed-value records.'
        ),
    ),

    DisclosureSource(
        name='Federal Court Filings — Disclosure Disputes',
        state='US',
        status='unverified',
        url_starting_point='https://pacer.uscourts.gov/',
        next_action=(
            'When buyers sue sellers for failure-to-disclose, complaints often '
            'quote the actual disclosure form verbatim. PACER records are public '
            'but access has per-page fees ($0.10/page). CourtListener (free) '
            'mirrors some filings. Volume is low (maybe 500 relevant cases/year '
            'nationally) but quality is high.'
        ),
    ),

    DisclosureSource(
        name='CA TDS — Transfer Disclosure Statement via County Recorders',
        state='CA',
        status='unverified',
        url_starting_point='https://www.dre.ca.gov/',
        next_action=(
            'CA TDS (Civil Code §1102) is required for most residential sales. '
            'Not generally publicly recorded — it goes to buyer. But some counties '
            'may attach to deed filings. LOW PRIORITY: investigation likely confirms '
            'these are private records.'
        ),
    ),
]


def summarize_all() -> str:
    """Generate a human-readable summary of all candidate sources."""
    lines = [f'{len(CANDIDATE_SOURCES)} candidate disclosure sources:\n']
    for s in CANDIDATE_SOURCES:
        lines.append(f'  • {s.summary()}')
    return '\n'.join(lines)


def sources_by_status(status: str) -> list[DisclosureSource]:
    return [s for s in CANDIDATE_SOURCES if s.status == status]
