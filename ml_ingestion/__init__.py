"""ML data ingestion package.

Scaffolding for the data-quality streams:
  Stream 1 (reextract): improved PDF → finding extraction via Claude
  Stream 2 (crawl):     public web sources → findings
  Stream 3 (relabel):   Claude-based re-labeling of existing corpus
  Stream 4 (synthesize, v5.86.93): generate diverse synthetic data for
                        geographic coverage gaps. Path 6 of the coverage
                        roadmap.

Four base classes handle shared plumbing:
  BaseIngestionJob        — job tracking, logging, dedup
  BaseCrawler             — HTTP+HTML crawling with robots.txt compliance
  BaseLabeler             — synchronous Claude API calls
  BaseBatchLabeler        — asynchronous Claude Batch API (50% cheaper)

v5.86.94 additions (scaffold only, not runnable yet):
  SocrataCrawler          — base class for Socrata-backed municipal portals
                            (Chicago, LA, Philadelphia). Inherits
                            BaseIngestionJob directly (API pagination, not
                            HTML crawling). Subclasses must set STATUS='active'.
  BaseDisclosureExtractor — base class for extracting findings from public
                            disclosure documents. Scaffold — no concrete
                            subclasses until disclosure_investigator.py
                            flags a source viable.

Concrete implementations:
  RelabelerV1                — Stream 3, ~$7/full pass
  StateDiverseSynthesizerV1  — Stream 4, ~$5 for 5,000 rows across 10 states

Scaffold implementations (NOT YET RUNNABLE):
  ChicagoBuildingViolationsCrawler — Stream 2 stub for data.cityofchicago.org
"""
from ml_ingestion.base import BaseIngestionJob
from ml_ingestion.crawler import BaseCrawler
from ml_ingestion.labeler import BaseLabeler
from ml_ingestion.batch_labeler import BaseBatchLabeler
from ml_ingestion.relabel_v1 import RelabelerV1
from ml_ingestion.synthesize_statediverse import StateDiverseSynthesizerV1
from ml_ingestion.socrata_crawler import (
    SocrataCrawler,
    ChicagoBuildingViolationsCrawler,
    FederalDataCrawler,
    PhiladelphiaLIViolationsCrawler,
    SanFranciscoDBIComplaintsCrawler,
    LosAngelesCodeEnforcementCrawler,
    SeattleCodeComplianceCrawler,
    AustinCodeCasesCrawler,
    BaltimoreHousingViolationsCrawler,
    DCHousingViolationsCrawler,
    DallasCodeViolationsCrawler,
    DetroitBlightViolationsCrawler,
    NewOrleansCodeViolationsCrawler,
    CincinnatiCodeEnforcementCrawler,
    CRAWLER_REGISTRY,
    get_crawler_class,
    list_active_crawlers,
)
from ml_ingestion.arcgis_crawler import (
    ArcGISCrawler,
    MiamiDadeCodeViolationsCrawler,
    PhoenixCodeEnforcementCrawler,
    AtlantaCodeEnforcementCrawler,
    DetroitBlightTicketsCrawler,
    ColumbusCodeEnforcementCrawler,
    AdamsCountyCodeEnforcementCrawler,
    AugustaGACodeViolationsCrawler,
    IndianapolisCodeEnforcementCrawler,
)
from ml_ingestion.disclosure_extractor import BaseDisclosureExtractor

__all__ = [
    # Base classes
    'BaseIngestionJob',
    'BaseCrawler',
    'BaseLabeler',
    'BaseBatchLabeler',
    # Stream 2 (crawlers)
    'SocrataCrawler',
    'ChicagoBuildingViolationsCrawler',
    # Stream 3 (re-label)
    'RelabelerV1',
    # Stream 4 (synthesize)
    'StateDiverseSynthesizerV1',
    # Path 5 (disclosure extraction — scaffold)
    'BaseDisclosureExtractor',
]
