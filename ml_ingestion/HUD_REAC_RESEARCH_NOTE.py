"""HUD REAC integration research note (v5.87.10).

We investigated whether HUD's Real Estate Assessment Center (REAC) inspection
narratives are accessible via a public crawl. The conclusion: **no, not in a
form that would yield training text**.

What's available
================
1. **REAC inspection scores** (numeric 0-100). Already pulled by the federal
   crawler via fetch_hud_inspection_scores() in cost_data_crawler.py. These
   are aggregate property scores, not per-finding text. Low signal for our
   labeler because they have no description — we synthesize a placeholder
   sentence ("HUD inspection score N/100 — STATE") which is repetitive.

2. **Property-level metadata** on hudgis-hud.opendata.arcgis.com (HUD's
   ArcGIS Hub). Includes:
   - Multifamily Properties - Assisted (~14K properties)
   - Public Housing Developments
   - HUD Section 202 Properties
   These describe WHICH properties exist, not what was wrong with them.

3. **Aggregate statistics** in publications, GAO reports, academic studies.
   Not row-level data, not crawlable.

What's NOT available
====================
- Per-finding inspection narratives. The text an inspector writes during a
  REAC physical assessment ("Roof shingles missing on north slope of east
  wing") is collected via the PASS (Physical Assessment Subsystem) software
  but is NOT publicly exposed via API.
- Bulk dumps of historical inspections. The Data.gov inventory was
  searched (114 datasets total), zero matched.
- The legacy endpoints data.hud.gov/resource/{8bxb-nmzg, jcdv-fn3j}.json
  that fetch_hud_inspection_scores still tries return HTTP 404 as of
  v5.87.10 — silent failure, the federal crawler logs "HUD: all endpoints
  unreachable" but doesn't surface this prominently.

How to get REAC narratives anyway (not viable for crawling)
==============================================================
- **FOIA request** to HUD Office of Public and Indian Housing — 60-90 day
  turnaround, scoped requests, can't be automated.
- **Individual PHA websites** — many of the ~3,300 Public Housing Agencies
  publish their own inspection results. Fragmented, no common API. Could
  be a long-tail crawler project if a small subset publishes well.

Better substitutes
==================
1. **AHS (American Housing Survey)** — Census Bureau biennial survey,
   ~80K housing units, includes detailed structural condition narratives.
   Distributed as downloadable CSV microdata files.
   https://www.census.gov/programs-surveys/ahs/data.html
   This is the closest substitute for "national inspection text" we have
   public access to, but it's a one-time CSV ingestion, not a recurring
   crawler.

2. **CourtListener disclosure dispute filings** — federal/state court
   opinions on real-estate disclosure cases. Rich finding-context, low
   volume (~500-1000/year nationally). Free API.

Decision (v5.87.10)
===================
Not building a HUD REAC crawler. The federal crawler already pulls REAC
scores; that's the realistic ceiling for HUD-sourced data in our setup.
For "national" coverage substitute, AHS and CourtListener are higher-value
next targets.

If reopening this question later: confirm whether any of the legacy
endpoints (8bxb-nmzg, jcdv-fn3j) have been migrated, or whether a new
HUD CDO open-data initiative has changed what's exposed.
"""
