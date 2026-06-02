# OfferWise ML Training Data Sources — Comprehensive Inventory

**Last updated:** April 16, 2026
**Purpose:** Catalog every publicly available data source relevant to training OfferWise's ML models (Finding Classifier, Contradiction Detector, Repair Cost Predictor, and future models).

---

## CURRENTLY INTEGRATED

### Internal (Auto-collected from every analysis)
| Source | Data Type | Volume | Model Fed |
|--------|-----------|--------|-----------|
| Analysis findings | finding text → category + severity | ~658 labels | Finding Classifier |
| Contradiction pairs | seller claim + inspector finding → label | ~513 pairs | Contradiction Detector |
| Co-occurrence baskets | which findings appear together per analysis | ~18 baskets | Future: Association Rules |
| Analysis breakdowns | per-finding cost estimates from Claude | ~19 analyses | Repair Cost Predictor |
| BASELINE_COSTS table | 44 national avg cost ranges by category/severity | 44 entries | Repair Cost Predictor |
| ZIP_COST_DATA | 430+ regional cost multipliers | 430 entries | Repair Cost Predictor |

### External (Crawled by ML Agent)
| Source | API/Method | Data Type | Volume per Crawl |
|--------|-----------|-----------|-----------------|
| LA City Permits | Socrata API (data.lacity.org) | Permit values + descriptions | ~500 |
| SF Permits | Socrata API (data.sfgov.org) | Permit values + descriptions | ~500 |
| Seattle Permits | Socrata API (data.seattle.gov) | Permit values + descriptions | ~500 |
| Chicago Permits | Socrata API (data.cityofchicago.org) | Permit values + descriptions | ~500 |
| NYC Permits | Socrata API (data.cityofnewyork.us) | Permit values + descriptions | ~500 |
| HomeAdvisor Cost Guides | Web scrape (12 repair types) | National avg cost ranges | 12 |
| FEMA IA Housing | OpenFEMA API | Disaster repair amounts by ZIP | ~500 |

---

## NOT YET INTEGRATED — HIGH PRIORITY

### 1. HUD Physical Inspection Scores
- **URL:** https://www.huduser.gov/portal/datasets/pis.html
- **API:** CSV download (public housing + multifamily)
- **Data:** 20,000 inspections/year since 2001. Property-level inspection scores with deficiency categories (structural, plumbing, electrical, HVAC, safety). Includes location data.
- **Value for OfferWise:** Direct training data for Finding Classifier (deficiency → category + severity). Also validates our contradiction detector against government-grade inspections.
- **Access:** Free, no auth. CSV download.
- **Size:** ~500K+ historical inspection records
- **Models fed:** Finding Classifier, Contradiction Detector

### 2. American Housing Survey (AHS) Public Use File
- **URL:** https://www.census.gov/programs-surveys/ahs/data.html
- **API:** Downloadable PUF (SAS/CSV) + AHS Table Creator
- **Data:** 85,000+ housing units surveyed biennially since 1973. Includes: housing deficiency questions (sagging roofs, cracked foundations, water leaks, holes in floors, pest infestations, HVAC failures), repair costs, housing quality scores (ADEQUACY variable), resident satisfaction ratings.
- **Value for OfferWise:** The Philadelphia Fed used AHS + RSMeans to build a repair cost index. We can do the same. This is the most comprehensive public dataset on housing deficiencies in the US.
- **Access:** Free PUF download. Internal Use File (with census tract) requires Research Data Center access.
- **Size:** ~117K housing units per survey × 25+ survey years
- **Models fed:** Finding Classifier (deficiency types), Repair Cost Predictor (estimated repair costs per deficiency type), Property Risk model (composite quality scores)

### 3. NFIP Redacted Claims (2M+ claims)
- **URL:** https://www.fema.gov/openfema-data-page/fima-nfip-redacted-claims-v2
- **API:** OpenFEMA API + bulk CSV download
- **Data:** 2,000,000+ flood insurance claims since 1978. Each record includes: building damage amount, contents damage amount, ZIP code (census tract level), flood zone, occupancy type, building age, number of floors, foundation type, etc.
- **Value for OfferWise:** Massive volume of real repair costs linked to specific damage types and locations. Better than our current FEMA IA Housing endpoint (which only has ~500 records). The NFIP claims dataset has actual paid amounts, not estimates.
- **Access:** Free, API or CSV. Monthly updates.
- **Size:** 2M+ claim records
- **Models fed:** Repair Cost Predictor (water/flood damage costs by ZIP), Finding Classifier (flood damage categories)

### 4. NYC Housing Maintenance Code Violations
- **URL:** https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5
- **API:** Socrata API (JSON)
- **Data:** Millions of housing code violations with: violation type/description, severity class (A/B/C), building address, date, status (open/closed), and category (plumbing, electrical, paint, structural, pest, ventilation, etc.)
- **Value for OfferWise:** Pre-categorized housing deficiencies with severity labels. This is essentially labeled training data for the Finding Classifier. NYC HPD's violation categories map almost 1:1 to our finding categories.
- **Access:** Free, Socrata API, no auth.
- **Size:** Millions of records, daily updates
- **Models fed:** Finding Classifier (violation description → category + severity), Contradiction Detector (open vs closed violations as proxy for disclosure accuracy)

### 5. NYC DOB Violations + Complaints
- **URL:** https://data.cityofnewyork.us/Housing-Development/DOB-Violations/3h2n-5cm9
- **API:** Socrata API
- **Data:** Building code violations from Department of Buildings. More structural/construction focused than HPD (which is maintenance). Includes: violation type, description, penalty, disposition.
- **Access:** Free, Socrata API
- **Models fed:** Finding Classifier (structural/code violations)

### 6. Boston Building & Property Violations
- **URL:** https://data.boston.gov/dataset/building-and-property-violations1
- **API:** CKAN API
- **Data:** Building code violations issued by Boston Inspectional Services. Includes violation type, status, address.
- **Access:** Free, API
- **Models fed:** Finding Classifier

### 7. Montgomery County Housing Code Enforcement
- **URL:** https://data.montgomerycountymd.gov/Consumer-Housing/Housing-Code-Violations/k9nj-z35d
- **API:** Socrata API
- **Data:** Housing code violations from 2013-present with violation descriptions.
- **Access:** Free, Socrata API
- **Models fed:** Finding Classifier

### 8. InterNACHI Sample Inspection Reports
- **URL:** https://www.nachi.org/home-inspection-report-samples.htm
- **Data:** Sample reports from certified inspectors. Downloadable PDFs. "You are permitted to download the reports and use them to improve your own inspection reports."
- **Value for OfferWise:** Real inspection report text to train the document parser and finding classifier. These are professionally written reports with standard terminology.
- **Access:** Free download, explicit permission to use
- **Size:** Dozens of full reports
- **Models fed:** Finding Classifier (real inspector language), Document Parser (report format recognition)

### 9. Home Inspection Database (homeinspectiondatabase.com)
- **URL:** https://homeinspectiondatabase.com/
- **Data:** Shared inspection reports from buyers, sellers, and inspectors. Allows searching by location.
- **Value for OfferWise:** Real-world inspection data with findings. Need to verify terms of use.
- **Access:** Requires account. Check ToS for data use permissions.

### 10. Insurance Information Institute (III) Aggregate Claims Data
- **URL:** https://www.iii.org/fact-statistic/facts-statistics-homeowners-and-renters-insurance
- **Data:** Aggregate statistics on homeowners insurance claims by type (wind/hail, water damage, fire, theft, liability), average claim amounts, frequency per 100 house-years, by state.
- **Value for OfferWise:** Calibrate our repair cost estimates against what insurance actually pays out. Fire/lightning averages $83,991/claim. Water damage averages $13,954. These are ground-truth severity indicators.
- **Access:** Published tables (scrape or manual entry). ISO/Verisk is the underlying source.
- **Models fed:** Repair Cost Predictor (national/state average claim costs by damage type)

### 11. NFIP Multiple Loss Properties
- **URL:** https://www.fema.gov/openfema-data-page/nfip-multiple-loss-properties-v1
- **API:** OpenFEMA API
- **Data:** Properties with multiple NFIP claims. Categories: Repetitive Loss, Severe Repetitive Loss. Includes mitigation status, claim counts, total paid amounts.
- **Value for OfferWise:** Identifies properties with recurring problems — directly relevant to our Predictive model (which findings predict future issues).
- **Access:** Free, API
- **Models fed:** Future: Predictive Model, Repair Cost Predictor

---

## NOT YET INTEGRATED — MEDIUM PRIORITY

### 12. State Insurance Department Rate Filings
- **States:** CA (CDI), FL (OIR), TX (TDI), NY (DFS), etc.
- **Data:** Insurers file loss cost data with state regulators. Some states publish aggregate data.
- **Value:** State-level loss costs by peril type, calibrated to regional construction costs.
- **Access:** Varies by state. Some public PDFs, some SERFF filings.
- **Models fed:** Repair Cost Predictor (regional calibration)

### 13. EPA Toxic Release Inventory (TRI)
- **URL:** https://www.epa.gov/toxics-release-inventory-tri-program
- **Data:** Facilities releasing toxic chemicals, by ZIP code. Relevant to environmental hazards near properties.
- **Access:** Free, API
- **Models fed:** Environmental risk scoring, Property Risk model

### 14. EPA Radon Zone Map Data
- **URL:** https://www.epa.gov/radon/epa-map-radon-zones
- **Data:** County-level radon risk zones (Zone 1 = highest).
- **Access:** Free, downloadable
- **Models fed:** Environmental category finding classifier, risk scoring

### 15. USGS Earthquake Hazard Data
- **URL:** https://earthquake.usgs.gov/earthquakes/
- **Data:** Seismic hazard maps, historical earthquake data by location.
- **Access:** Free, API
- **Models fed:** Foundation/structural risk scoring

### 16. FEMA Flood Map / NFHL
- **URL:** National Flood Hazard Layer (NFHL) via FEMA Map Service Center
- **Data:** Flood zone designations by parcel.
- **Access:** Free, API
- **Models fed:** Environmental risk scoring, property risk

### 17. Census Bureau — American Community Survey (ACS)
- **URL:** https://www.census.gov/programs-surveys/acs.html
- **Data:** Housing age, median home value, vacancy rates, housing conditions by census tract.
- **Access:** Free, API
- **Models fed:** Regional context features for all models

### 18. Zillow / Redfin / Realtor.com Public APIs
- **Data:** Listing prices, sale history, property characteristics (age, sq ft, beds, baths).
- **Value:** Property metadata to enrich training features (price, age, size correlate with repair costs).
- **Access:** Varies. Zillow has a free API for some data. Redfin publishes CSV data.

### 19. Gordian / RSMeans Regional Cost Factors
- **URL:** https://www.rsmeans.com/ (commercial)
- **Data:** ZIP-level construction cost multipliers used by the Philadelphia Fed AHS study.
- **Value:** The gold standard for regional cost adjustment. Our ZIP_COST_DATA is a simplified version.
- **Access:** ~$800/year subscription. Consider if/when training volume justifies it.

---

## NOT YET INTEGRATED — LOWER PRIORITY / FUTURE

### 20. State-Level Code Enforcement Databases
Many cities publish code enforcement data on their open data portals:
- New Orleans (data.nola.gov)
- Tempe, AZ
- Frederick, MD
- New York State (BSC)
- Additional cities indexed at: https://us-city.census.okfn.org/dataset/code-enforcement.html

### 21. Consumer Financial Protection Bureau (CFPB) Complaint Database
- **URL:** https://www.consumerfinance.gov/data-research/consumer-complaints/
- **Data:** Consumer complaints about mortgages, home warranties, etc.
- **Value:** Identify patterns in post-purchase disputes related to undisclosed property issues.

### 22. National Association of Home Builders (NAHB) Construction Cost Data
- **Data:** Annual surveys of construction and remodeling costs.
- **Access:** Member access, some published summaries.

### 23. State Contractor License Boards
- Most states publish contractor license databases (active, complaints, disciplinary actions).
- **Value:** Validate contractor pricing data, identify typical repair scopes.

### 24. County Assessor / Tax Records
- Property characteristics (year built, sq ft, lot size, improvements) are public record.
- **Value:** Property metadata to enrich model features.
- **Access:** Varies by county. Many have APIs or bulk downloads.

---

## DATA SOURCE PRIORITY MATRIX

| Priority | Source | Expected Rows | Effort | Impact |
|----------|--------|--------------|--------|--------|
| P0 | HUD Inspection Scores | 500K+ | Low (CSV) | HIGH — labeled deficiencies |
| P0 | NYC HPD Violations | 1M+ | Low (Socrata) | HIGH — categorized findings |
| P0 | NFIP Redacted Claims | 2M+ | Medium (large) | HIGH — real repair costs |
| P1 | AHS Public Use File | 100K+ | Medium (PUF) | HIGH — housing deficiency + costs |
| P1 | InterNACHI Sample Reports | ~50 | Low (PDF) | MEDIUM — real report text |
| P1 | III Insurance Claim Data | ~50 rows | Low (manual) | MEDIUM — calibration data |
| P1 | NYC DOB Violations | 500K+ | Low (Socrata) | MEDIUM — structural violations |
| P2 | Boston Violations | 100K+ | Low (CKAN) | MEDIUM — regional data |
| P2 | NFIP Multiple Loss | 100K+ | Low (API) | MEDIUM — recurring damage |
| P2 | EPA Radon Zones | ~3K counties | Low (CSV) | LOW — risk scoring |
| P2 | USGS Earthquake | N/A | Low (API) | LOW — risk scoring |

---

## IMPLEMENTATION PLAN

### Phase 1: Quick Wins (1-2 days)
1. **NYC HPD Violations** — Socrata API, same pattern as existing permit crawlers. Millions of pre-categorized findings. Add to `cost_data_crawler.py`.
2. **HUD Inspection Scores** — CSV download, parse and store. Labeled deficiency data.
3. **III Insurance Claim Averages** — Manual entry of ~50 aggregate statistics. High-quality calibration data for repair costs.

### Phase 2: High-Volume Sources (3-5 days)
4. **NFIP Redacted Claims** — OpenFEMA API with pagination. 2M+ records but we filter to residential, $500-$100K range. Massive cost training data.
5. **AHS Public Use File** — Download PUF, extract housing deficiency module. Need to map AHS deficiency codes to our categories.
6. **InterNACHI Sample Reports** — Download PDFs, parse with our existing document parser, extract findings + categories.

### Phase 3: Expand Coverage (1 week)
7. NYC DOB Violations, Boston violations, Montgomery County
8. EPA Radon + USGS Earthquake → environmental risk features
9. ACS housing data → regional context features

### Phase 4: Continuous Pipeline
10. ML Agent daily crawl expands to include all integrated sources
11. New analyses auto-archive to document repository (already done)
12. Inspector labeling UI (already done)
13. Monthly AHS/HUD refresh when new data published

---

## LEGAL / ETHICAL NOTES

- All sources listed above are **publicly available government data** or explicitly shared for public use.
- InterNACHI explicitly permits downloading and using their sample reports.
- We comply with robots.txt on all web scraping targets.
- No login/paywall/auth gates are bypassed.
- FEMA/Census data is public domain (US Government works).
- NYC/Boston/Chicago open data portals have public data licenses.
- HomeAdvisor cost guides are publicly published content — we extract factual data (cost ranges), not copyrighted text.
- Raw HTML/JSON archived in docrepo for audit trail.
