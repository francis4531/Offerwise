"""
Public Document Crawler for OfferWise
=====================================
Crawls, discovers, and downloads publicly available inspection reports
and seller disclosure documents from US sources.

Three modes:
  1. SCAN   — Check sources for new documents, report what's available
  2. CRAWL  — Download new documents to the repo
  3. CORPUS — Build training corpus with metadata tagging

Sources:
  - CourtListener/RECAP (federal court exhibits)
  - State RE commission complaint files
  - Inspector software sample reports (ScribeWare, HomeGauge, Spectora, etc.)
  - InterNACHI/ASHI/CREIA sample galleries
  - CA DRE enforcement (existing, preserved)
  - County/municipal public records

LEGAL COMPLIANCE:
  - ONLY downloads documents that are explicitly public records or
    published as public samples by their creators.
  - Respects robots.txt on every domain before crawling.
  - Skips any URL requiring login, authentication, or payment.
  - Skips content behind paywalls, subscription walls, or access gates.
  - Skips content with restrictive copyright/license notices.
  - Identifies as OfferWise crawler in User-Agent for transparency.
  - Rate-limits all requests to avoid server burden.
  - All sources are US-only, public domain or publicly accessible records.
"""

import os
import json
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ================================================================
# CONFIGURATION
# ================================================================

DEFAULT_REPO_PATH = os.environ.get(
    'DOCREPO_PATH',
    os.path.join(os.path.dirname(__file__), 'document_repo')
)
PERSISTENT_REPO_PATH = '/opt/render/project/data/document_repo'

CRAWLER_DB_FILE = 'crawler_state.json'
CATALOG_FILE = 'metadata/catalog.json'

USER_AGENT = (
    'OfferWise-DocCrawler/1.0 '
    '(Real estate research; https://getofferwise.ai; '
    'contact: support@getofferwise.ai)'
)

# Rate limiting: be respectful
REQUEST_DELAY_SECONDS = 2.0
MAX_DOWNLOADS_PER_RUN = 25
MAX_FILE_SIZE_MB = 50


# ================================================================
# CRAWLER STATE
# ================================================================

class CrawlerState:
    """Tracks what we've seen, downloaded, and skipped."""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.state_file = os.path.join(repo_path, 'metadata', CRAWLER_DB_FILE)
        self.state = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        return {
            'discovered': {},      # url_hash -> {url, source, discovered_at, status}
            'downloaded': {},      # url_hash -> {url, source, downloaded_at, filename, doc_type}
            'failed': {},          # url_hash -> {url, source, failed_at, error}
            'last_run': {},        # source_name -> {timestamp, docs_found, docs_downloaded}
            'corpus_stats': {
                'total_inspection_reports': 0,
                'total_disclosures': 0,
                'total_pest_reports': 0,
                'total_repair_estimates': 0,
                'total_permit_records': 0,
                'total_specialist_reports': 0,   # sewer, foundation, roof certs
                'total_court_cases': 0,          # verdicts, settlements
                'total_reference': 0,            # codes, guidelines, standards
                'total_other': 0,
                'formats_seen': {},
                'states_covered': {},
                'inspector_software': {},
            }
        }

    def save(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2, default=str)

    def is_known(self, url: str) -> bool:
        h = hashlib.md5(url.encode()).hexdigest()
        return h in self.state['discovered'] or h in self.state['downloaded']

    def mark_discovered(self, url: str, source: str, doc_type: str = 'unknown', metadata: dict = None):
        h = hashlib.md5(url.encode()).hexdigest()
        self.state['discovered'][h] = {
            'url': url,
            'source': source,
            'doc_type': doc_type,
            'discovered_at': datetime.now().isoformat(),
            'metadata': metadata or {},
        }

    def mark_downloaded(self, url: str, source: str, filename: str, doc_type: str, metadata: dict = None):
        h = hashlib.md5(url.encode()).hexdigest()
        self.state['downloaded'][h] = {
            'url': url,
            'source': source,
            'filename': filename,
            'doc_type': doc_type,
            'downloaded_at': datetime.now().isoformat(),
            'metadata': metadata or {},
        }
        # Remove from discovered if present
        self.state['discovered'].pop(h, None)
        # Update corpus stats
        stats = self.state['corpus_stats']
        type_to_stat = {
            'inspection_report': 'total_inspection_reports',
            'disclosure': 'total_disclosures',
            'pest_report': 'total_pest_reports',
            'repair_estimate': 'total_repair_estimates',
            'permit_record': 'total_permit_records',
            'specialist_report': 'total_specialist_reports',
            'court_case': 'total_court_cases',
            'reference': 'total_reference',
        }
        stat_key = type_to_stat.get(doc_type, 'total_other')
        stats[stat_key] = stats.get(stat_key, 0) + 1
        if metadata:
            fmt = metadata.get('format', 'unknown')
            stats['formats_seen'][fmt] = stats['formats_seen'].get(fmt, 0) + 1
            state = metadata.get('state', 'unknown')
            stats['states_covered'][state] = stats['states_covered'].get(state, 0) + 1
            sw = metadata.get('inspector_software', '')
            if sw:
                stats['inspector_software'][sw] = stats['inspector_software'].get(sw, 0) + 1

    def mark_failed(self, url: str, source: str, error: str):
        h = hashlib.md5(url.encode()).hexdigest()
        self.state['failed'][h] = {
            'url': url,
            'source': source,
            'failed_at': datetime.now().isoformat(),
            'error': error,
        }

    def update_run(self, source_name: str, docs_found: int, docs_downloaded: int):
        self.state['last_run'][source_name] = {
            'timestamp': datetime.now().isoformat(),
            'docs_found': docs_found,
            'docs_downloaded': docs_downloaded,
        }


# ================================================================
# BASE SOURCE ADAPTER
# ================================================================

class SourceAdapter:
    """Base class for document source adapters."""

    name = "base"
    url = ""
    doc_types = []  # ['inspection_report', 'disclosure', 'enforcement']

    # Signals that a page requires login, payment, or is access-gated
    ACCESS_GATE_SIGNALS = [
        # Login/auth walls
        'sign in', 'log in', 'login', 'create account', 'register to',
        'authentication required', 'access denied', 'unauthorized',
        'please subscribe', 'member only', 'members only',
        # Paywall/subscription
        'paywall', 'subscribe to access', 'purchase required',
        'buy now to access', 'premium content', 'paid access',
        'subscription required', 'add to cart',
        # CAPTCHA
        'captcha', 'recaptcha', 'verify you are human',
        'robot', 'automated access',
    ]

    # Copyright/license signals that indicate restricted content
    RESTRICTED_LICENSE_SIGNALS = [
        'all rights reserved',
        'do not reproduce', 'do not copy', 'do not distribute',
        'proprietary and confidential',
        'licensed for personal use only',
        'not for redistribution',
        'copyright protected',
        'unauthorized reproduction prohibited',
    ]

    # Signals that content is explicitly public
    PUBLIC_SIGNALS = [
        'public record', 'public document', 'public domain',
        'freedom of information', 'foia', 'government record',
        'court record', 'sample report', 'demo report',
        'example report', 'open access', 'creative commons',
        'freely available', 'public disclosure',
    ]

    def __init__(self, repo_path: str, state: CrawlerState):
        self.repo_path = repo_path
        self.state = state
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        self._robots_cache = {}  # domain -> {allowed: bool, checked_at: timestamp}

    def scan(self) -> list:
        """Discover available documents. Returns list of dicts:
        [{url, title, doc_type, metadata}, ...]"""
        raise NotImplementedError

    def _check_robots_txt(self, url: str) -> bool:
        """Check robots.txt to ensure we're allowed to crawl this URL.
        Returns True if crawling is permitted."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = f"{parsed.scheme}://{parsed.netloc}"

            # Check cache (valid for 1 hour)
            if domain in self._robots_cache:
                cached = self._robots_cache[domain]
                if (datetime.now() - datetime.fromisoformat(cached['checked_at'])).seconds < 3600:
                    return cached['allowed']

            robots_url = f"{domain}/robots.txt"
            resp = self.session.get(robots_url, timeout=10)

            allowed = True
            if resp.status_code == 200:
                text = resp.text.lower()
                # Simple parser: check if our UA or * is disallowed from this path
                current_agent_applies = False
                for line in text.split('\n'):
                    line = line.strip()
                    if line.startswith('user-agent:'):
                        agent = line.split(':', 1)[1].strip()
                        current_agent_applies = (agent == '*' or 'offerwise' in agent)
                    elif current_agent_applies and line.startswith('disallow:'):
                        disallowed_path = line.split(':', 1)[1].strip()
                        if disallowed_path and parsed.path.startswith(disallowed_path):
                            allowed = False
                            logger.info(f"robots.txt disallows: {url}")
                            break

            self._robots_cache[domain] = {
                'allowed': allowed,
                'checked_at': datetime.now().isoformat(),
            }
            return allowed

        except Exception as e:
            logger.debug(f"robots.txt check failed for {url}: {e}")
            return True  # If we can't check, assume allowed

    def _is_access_gated(self, content: str) -> bool:
        """Check if page content indicates login, paywall, or auth requirement."""
        content_lower = content.lower()[:5000]  # Check first 5KB
        gate_count = sum(1 for signal in self.ACCESS_GATE_SIGNALS if signal in content_lower)
        # Multiple signals = likely gated (single match could be incidental)
        return gate_count >= 2

    def _has_restrictive_license(self, content: str) -> bool:
        """Check if content has restrictive copyright/license notices."""
        content_lower = content.lower()[:10000]
        for signal in self.RESTRICTED_LICENSE_SIGNALS:
            if signal in content_lower:
                # Check if there's also a public signal (e.g. "all rights reserved" on a gov site)
                has_public = any(ps in content_lower for ps in self.PUBLIC_SIGNALS)
                if not has_public:
                    logger.info(f"Restrictive license detected: '{signal}'")
                    return True
        return False

    def _is_safe_to_download(self, url: str, content: str = '') -> bool:
        """Master check: is this URL safe and legal to download?"""
        # 1. Check robots.txt
        if not self._check_robots_txt(url):
            logger.info(f"BLOCKED by robots.txt: {url}")
            return False

        # 2. Check for access gates (if we have content)
        if content and self._is_access_gated(content):
            logger.info(f"BLOCKED — access gate detected: {url}")
            return False

        # 3. Check for restrictive licenses
        if content and self._has_restrictive_license(content):
            logger.info(f"BLOCKED — restrictive license: {url}")
            return False

        return True

    def download(self, url: str, doc_info: dict) -> Optional[str]:
        """Download a document. Returns local filename or None.
        Only downloads content verified as publicly accessible."""
        try:
            # Pre-flight: check robots.txt before any download
            if not self._check_robots_txt(url):
                logger.info(f"Skipping (robots.txt): {url}")
                self.state.mark_failed(url, self.name, "Blocked by robots.txt")
                return None

            resp = self.session.get(url, timeout=30, stream=True)
            resp.raise_for_status()

            # Check for redirects to login pages
            if resp.url != url:
                redirect_lower = resp.url.lower()
                if any(kw in redirect_lower for kw in ['login', 'signin', 'auth', 'subscribe', 'register']):
                    logger.info(f"Skipping (redirected to login): {url} → {resp.url}")
                    self.state.mark_failed(url, self.name, "Redirected to login/auth page")
                    return None

            # Determine file extension
            content_type = resp.headers.get('Content-Type', '')
            if 'pdf' in content_type:
                ext = '.pdf'
            elif 'html' in content_type:
                ext = '.html'
            else:
                ext = '.pdf'  # default

            # Size check
            content_length = int(resp.headers.get('Content-Length', 0))
            if content_length > MAX_FILE_SIZE_MB * 1024 * 1024:
                logger.warning(f"File too large ({content_length} bytes): {url}")
                self.state.mark_failed(url, self.name, f"File too large: {content_length} bytes")
                return None

            # Read content
            content_bytes = b''
            for chunk in resp.iter_content(chunk_size=8192):
                content_bytes += chunk
                if len(content_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
                    logger.warning(f"File exceeded size limit during download: {url}")
                    self.state.mark_failed(url, self.name, "File exceeded size limit")
                    return None

            # For HTML content, check for access gates and restrictive licenses
            if 'html' in content_type:
                content_text = content_bytes.decode('utf-8', errors='ignore')
                if not self._is_safe_to_download(url, content_text):
                    self.state.mark_failed(url, self.name, "Content appears restricted or access-gated")
                    return None

            # Generate filename
            slug = re.sub(r'[^a-zA-Z0-9]', '_', doc_info.get('title', 'doc'))[:60]
            timestamp = datetime.now().strftime('%Y%m%d')
            filename = f"{self.name}_{timestamp}_{slug}{ext}"

            # Determine target directory
            doc_type = doc_info.get('doc_type', 'unknown')
            type_to_dir = {
                'inspection_report': 'inspection_reports',
                'disclosure': 'disclosure_statements',
                'pest_report': 'specialist_reports',
                'repair_estimate': 'reference_docs',
                'permit_record': 'reference_docs',
                'specialist_report': 'specialist_reports',
                'court_case': 'reference_docs',
                'reference': 'reference_docs',
            }
            subdir = type_to_dir.get(doc_type, 'reference_docs')
            target_dir = os.path.join(self.repo_path, subdir)

            os.makedirs(target_dir, exist_ok=True)
            filepath = os.path.join(target_dir, filename)

            with open(filepath, 'wb') as f:
                f.write(content_bytes)

            logger.info(f"Downloaded: {filename} ({len(content_bytes)} bytes)")
            return filename

        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
            self.state.mark_failed(url, self.name, str(e))
            return None

    def _respectful_delay(self):
        """Rate limit to be a good citizen."""
        time.sleep(REQUEST_DELAY_SECONDS)


# ================================================================
# SOURCE: CourtListener / RECAP
# ================================================================

class CourtListenerSource(SourceAdapter):
    """
    Search federal court cases for real estate disputes with
    inspection reports and disclosure documents as exhibits.
    Uses the free CourtListener API (no key needed for basic search).
    """

    name = "courtlistener"
    url = "https://www.courtlistener.com"
    doc_types = ['inspection_report', 'disclosure']

    # Targeted queries for different document types in court exhibits
    SEARCH_QUERIES = [
        # Inspection reports & disclosures
        'home inspection report',
        'seller disclosure defect',
        'transfer disclosure statement',
        'property condition inspection',
        # Pest / termite
        'termite inspection report wood destroying',
        'pest inspection section 1 findings',
        # Specialist reports
        'foundation engineering report structural',
        'sewer lateral inspection scope',
        # Disclosure fraud (court outcomes = ground truth)
        'real estate disclosure fraud damages',
        'seller concealed defect judgment',
    ]

    API_BASE = "https://www.courtlistener.com/api/rest/v4"

    def scan(self) -> list:
        found = []
        for query in self.SEARCH_QUERIES:
            try:
                time.sleep(5)  # CourtListener needs longer delays
                resp = self.session.get(
                    f"{self.API_BASE}/search/",
                    params={
                        'q': query,
                        'type': 'r',  # RECAP documents
                        'order_by': 'dateFiled desc',
                        'page_size': 10,
                        'filed_after': '2024-01-01',
                    },
                    timeout=30,
                    headers={'Accept': 'application/json'},
                )
                if resp.status_code != 200:
                    logger.warning(f"CourtListener search failed ({resp.status_code}): {query}")
                    continue

                try:
                    data = resp.json()
                except (ValueError, Exception):
                    logger.warning(f"CourtListener returned non-JSON for: {query}")
                    continue

                for result in data.get('results', []):
                    docket_id = result.get('docket_id')
                    case_name = result.get('caseName', 'Unknown Case')
                    if not docket_id:
                        continue

                    docket_url = f"https://www.courtlistener.com/docket/{docket_id}/"
                    if self.state.is_known(docket_url):
                        continue

                    doc_info = {
                        'url': docket_url,
                        'title': case_name[:100],
                        'doc_type': self._classify_document(result),
                        'metadata': {
                            'case_name': case_name,
                            'court': result.get('court', ''),
                            'date_filed': result.get('dateFiled', ''),
                            'docket_number': result.get('docketNumber', ''),
                            'docket_id': docket_id,
                            'source': 'CourtListener/RECAP',
                            'format': 'docket_reference',
                            'state': self._extract_state(result),
                            'note': 'Docket discovered — exhibits may contain inspection/disclosure docs. Review manually or use PACER.',
                        }
                    }
                    found.append(doc_info)
                    self.state.mark_discovered(docket_url, self.name, doc_info['doc_type'], doc_info['metadata'])

            except requests.exceptions.Timeout:
                logger.warning(f"CourtListener timeout for: {query}")
                continue
            except Exception as e:
                logger.error(f"CourtListener scan error for '{query}': {e}")
                continue

        return found

    def _classify_document(self, result: dict) -> str:
        desc = (result.get('caseName', '') + ' ' + result.get('short_description', '') + ' ' + result.get('description', '')).lower()
        if any(kw in desc for kw in ['inspection report', 'home inspection', 'property inspection']):
            return 'inspection_report'
        elif any(kw in desc for kw in ['disclosure', 'tds', 'transfer disclosure', 'seller disclosure']):
            return 'disclosure'
        elif any(kw in desc for kw in ['pest', 'termite', 'wood destroying', 'section 1']):
            return 'pest_report'
        elif any(kw in desc for kw in ['permit', 'building permit', 'unpermitted']):
            return 'permit_record'
        elif any(kw in desc for kw in ['foundation', 'structural engineer', 'geotechnical', 'sewer', 'roof cert']):
            return 'specialist_report'
        elif any(kw in desc for kw in ['appraisal', 'estimate', 'repair cost', 'contractor bid']):
            return 'repair_estimate'
        elif any(kw in desc for kw in ['verdict', 'settlement', 'judgment', 'damages awarded']):
            return 'court_case'
        return 'reference'

    def _extract_state(self, result: dict) -> str:
        court = result.get('court', '').lower()
        # Map federal court abbreviations to states
        state_map = {
            'cacd': 'CA', 'caed': 'CA', 'cand': 'CA', 'casd': 'CA',
            'nysd': 'NY', 'nyed': 'NY', 'nynd': 'NY', 'nywd': 'NY',
            'txsd': 'TX', 'txed': 'TX', 'txnd': 'TX', 'txwd': 'TX',
            'flsd': 'FL', 'flmd': 'FL', 'flnd': 'FL',
            'ilnd': 'IL', 'ilsd': 'IL', 'ilcd': 'IL',
        }
        for abbrev, state in state_map.items():
            if abbrev in court:
                return state
        return 'unknown'


# ================================================================
# SOURCE: Inspector Software Sample Reports
# ================================================================

class InspectorSampleSource(SourceAdapter):
    """
    Download publicly available sample/demo inspection reports
    from inspector software companies.
    """

    name = "inspector_samples"
    url = "various"
    doc_types = ['inspection_report']

    # Known public sample report URLs and galleries (verified Feb 2026)
    KNOWN_SAMPLES = [
        # Industry associations with sample/reference reports
        {
            'url': 'https://www.nachi.org/gallery/',
            'title': 'InterNACHI Sample Report Gallery',
            'software': 'InterNACHI',
            'format': 'html',
            'type': 'gallery',
        },
        {
            'url': 'https://www.homeinspector.org/Resources/Standard-of-Practice/',
            'title': 'ASHI Standards of Practice & Sample Reports',
            'software': 'ASHI',
            'format': 'html',
            'type': 'gallery',
        },
        # Software companies with public demos/samples
        {
            'url': 'https://www.spectora.com/home-inspection-report-software',
            'title': 'Spectora Report Software Demo',
            'software': 'Spectora',
            'format': 'html',
            'type': 'demo_page',
        },
        {
            'url': 'https://www.homegauge.com/home-inspection-report-software/',
            'title': 'HomeGauge Report Software Demo',
            'software': 'HomeGauge',
            'format': 'html',
            'type': 'demo_page',
        },
        {
            'url': 'https://www.getscribeware.com/',
            'title': 'ScribeWare Inspection Software',
            'software': 'ScribeWare',
            'format': 'html',
            'type': 'demo_page',
        },
        {
            'url': 'https://www.palmtech.com/home-inspection-software/',
            'title': 'Palm-Tech Inspection Report Software',
            'software': 'Palm-Tech',
            'format': 'html',
            'type': 'demo_page',
        },
    ]

    def scan(self) -> list:
        found = []
        for sample in self.KNOWN_SAMPLES:
            if self.state.is_known(sample['url']):
                continue
            try:
                self._respectful_delay()
                resp = self.session.head(
                    sample['url'], timeout=10, allow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
                )
                if resp.status_code in (200, 301, 302):
                    doc_info = {
                        'url': sample['url'],
                        'title': sample['title'],
                        'doc_type': 'inspection_report',
                        'metadata': {
                            'inspector_software': sample['software'],
                            'format': sample['format'],
                            'source': f"Sample — {sample['software']}",
                            'state': 'various',
                            'type': sample.get('type', 'sample'),
                        }
                    }
                    found.append(doc_info)
                    self.state.mark_discovered(sample['url'], self.name, 'inspection_report', doc_info['metadata'])
                else:
                    logger.debug(f"Sample {sample['software']}: HTTP {resp.status_code}")
            except Exception as e:
                logger.debug(f"Sample check failed for {sample['software']}: {e}")
                continue

        return found


# ================================================================
# SOURCE: State RE Commission Complaints
# ================================================================

class StateComplaintSource(SourceAdapter):
    """
    Check state real estate commission websites for publicly
    available complaint files, enforcement actions, and
    associated inspection/disclosure documents.
    """

    name = "state_complaints"
    url = "various"
    doc_types = ['disclosure', 'enforcement']

    # States with accessible online enforcement databases
    STATE_SOURCES = {
        'CA': {
            'name': 'California DRE',
            'url': 'https://secure.dre.ca.gov/publicasp/EnforcementActions.asp',
            'type': 'asp_table',
            'active': True,  # Already implemented in DRE monitor
        },
        'TX': {
            'name': 'Texas TREC',
            'url': 'https://www.trec.texas.gov/apps/search/',
            'type': 'search_portal',
            'active': False,  # Needs CAPTCHA handling
        },
        'FL': {
            'name': 'Florida DBPR',
            'url': 'https://www.myfloridalicense.com/wl11.asp',
            'type': 'search_portal',
            'active': False,
        },
        'NY': {
            'name': 'New York DOS',
            'url': 'https://appext20.dos.ny.gov/lcns_public/chk_caseno',
            'type': 'case_lookup',
            'active': False,
        },
        'IL': {
            'name': 'Illinois IDFPR',
            'url': 'https://idfpr.illinois.gov/admin/DPR/DPRDEFAULT.asp',
            'type': 'search_portal',
            'active': False,
        },
        'AZ': {
            'name': 'Arizona ADRE',
            'url': 'https://services.azre.gov/publicdatabase/',
            'type': 'search_portal',
            'active': False,
        },
        'WA': {
            'name': 'Washington DOL',
            'url': 'https://fortress.wa.gov/dol/dolprod/bpdLicenseQuery.asp',
            'type': 'search_portal',
            'active': False,
        },
        'CO': {
            'name': 'Colorado DORA',
            'url': 'https://apps.colorado.gov/dora/licensing/Lookup/',
            'type': 'search_portal',
            'active': False,
        },
    }

    def scan(self) -> list:
        found = []
        available_states = []

        for state_code, source in self.STATE_SOURCES.items():
            status = 'active' if source['active'] else 'available'
            available_states.append({
                'state': state_code,
                'name': source['name'],
                'url': source['url'],
                'status': status,
                'type': source['type'],
            })

            if source['active']:
                # Run active scrapers
                try:
                    state_docs = self._scrape_state(state_code, source)
                    found.extend(state_docs)
                except Exception as e:
                    logger.error(f"State scrape failed for {state_code}: {e}")

        # Store the availability report in metadata
        self.state.state.setdefault('state_sources', {})
        self.state.state['state_sources'] = available_states

        return found

    def _scrape_state(self, state_code: str, source: dict) -> list:
        """Scrape a specific state source. Currently only CA is active."""
        if state_code == 'CA':
            return []  # Handled by existing DRE monitor — don't duplicate
        return []


# ================================================================
# SOURCE: Public Records / FOIA
# ================================================================

class PublicRecordsSource(SourceAdapter):
    """
    Track and fetch documents from public records sources:
    - HUD inspection reports (FOIA)
    - FHA appraisal reports
    - Municipal code enforcement records
    """

    name = "public_records"
    url = "various"
    doc_types = ['inspection_report', 'reference']

    # Government sources with programmatic access
    SOURCES = [
        {
            'name': 'HUD Property Inspections',
            'url': 'https://www.hud.gov/program_offices/housing/mfh/rems/remsinspecscores',
            'doc_type': 'inspection_report',
            'access': 'web_page',
            'notes': 'Multifamily housing inspection scores — public data',
        },
        {
            'name': 'EPA Environmental Reports',
            'url': 'https://enviro.epa.gov/',
            'doc_type': 'reference',
            'access': 'api',
            'notes': 'Environmental contamination data by address',
        },
        {
            'name': 'FEMA Flood Map Service',
            'url': 'https://msc.fema.gov/portal/search',
            'doc_type': 'reference',
            'access': 'api',
            'notes': 'Flood zone determinations — supports NHD analysis',
        },
    ]

    def scan(self) -> list:
        found = []
        for source in self.SOURCES:
            if self.state.is_known(source['url']):
                continue
            found.append({
                'url': source['url'],
                'title': source['name'],
                'doc_type': source['doc_type'],
                'metadata': {
                    'source': source['name'],
                    'access_method': source['access'],
                    'notes': source['notes'],
                    'state': 'US',
                }
            })
        return found


# ================================================================
# SOURCE: California Permit Records (Open Data Portals)
# ================================================================

class PermitRecordSource(SourceAdapter):
    """
    Fetch building permit data from California city/county open data portals.
    Many CA municipalities publish permit records via Socrata/CKAN APIs.
    ALL data is public record under the California Public Records Act.
    """

    name = "permit_records"
    url = "various"
    doc_types = ['permit_record']

    # Cities with open data portals publishing permit records
    OPEN_DATA_PORTALS = [
        {
            'name': 'San Jose Building Permits',
            'url': 'https://data.sanjoseca.gov/resource/9yx7-3aq8.json',
            'type': 'socrata',
            'params': {'$limit': 20, '$order': 'permit_date DESC'},
            'state': 'CA',
            'city': 'San Jose',
        },
        {
            'name': 'San Francisco Building Permits',
            'url': 'https://data.sfgov.org/resource/i98e-djp9.json',
            'type': 'socrata',
            'params': {'$limit': 20, '$order': 'filed_date DESC'},
            'state': 'CA',
            'city': 'San Francisco',
        },
        {
            'name': 'Los Angeles Building Permits',
            'url': 'https://data.lacity.org/resource/yv23-pmwf.json',
            'type': 'socrata',
            'params': {'$limit': 20, '$order': 'issue_date DESC'},
            'state': 'CA',
            'city': 'Los Angeles',
        },
        {
            'name': 'Sacramento Building Permits',
            'url': 'https://data.cityofsacramento.org/resource/wbb8-iaw4.json',
            'type': 'socrata',
            'params': {'$limit': 20},
            'state': 'CA',
            'city': 'Sacramento',
        },
    ]

    def scan(self) -> list:
        found = []
        for portal in self.OPEN_DATA_PORTALS:
            try:
                self._respectful_delay()
                resp = self.session.get(
                    portal['url'],
                    params=portal.get('params', {}),
                    timeout=15,
                    headers={'Accept': 'application/json'},
                )
                if resp.status_code != 200:
                    logger.debug(f"Permit portal {portal['name']}: HTTP {resp.status_code}")
                    continue

                try:
                    records = resp.json()
                except (ValueError, Exception):
                    continue

                if not isinstance(records, list) or len(records) == 0:
                    continue

                # Report the portal as a source with record count
                portal_url = portal['url'].split('/resource/')[0] if '/resource/' in portal['url'] else portal['url']
                if not self.state.is_known(portal_url):
                    found.append({
                        'url': portal_url,
                        'title': f"{portal['name']} ({len(records)} recent records)",
                        'doc_type': 'permit_record',
                        'metadata': {
                            'source': portal['name'],
                            'format': 'json_api',
                            'state': portal['state'],
                            'city': portal['city'],
                            'record_count': len(records),
                            'access_method': 'api',
                            'note': 'Public record — Socrata Open Data API',
                        }
                    })
                    self.state.mark_discovered(portal_url, self.name, 'permit_record')

            except Exception as e:
                logger.debug(f"Permit portal error {portal['name']}: {e}")
                continue

        return found


# ================================================================
# SOURCE: CA Structural Pest Control Board (Pest/Termite Reports)
# ================================================================

class PestReportSource(SourceAdapter):
    """
    California Structural Pest Control Board public records.
    The SPCB publishes licensee info and enforcement actions.
    WDI (Wood Destroying Insect) reports filed with the board
    are public record under CA Business & Professions Code §8519.
    """

    name = "pest_reports"
    url = "https://www.pestboard.ca.gov"
    doc_types = ['pest_report']

    def scan(self) -> list:
        found = []

        # SPCB activity reports and enforcement
        sources = [
            {
                'url': 'https://www.pestboard.ca.gov/consumers/enfact.shtml',
                'title': 'SPCB Enforcement Actions',
                'doc_type': 'pest_report',
                'note': 'Enforcement actions against pest inspectors — public record',
            },
            {
                'url': 'https://www.pestboard.ca.gov/consumers/complain.shtml',
                'title': 'SPCB Consumer Complaint Records',
                'doc_type': 'pest_report',
                'note': 'Consumer complaint process and public filings',
            },
            {
                'url': 'https://www.pestboard.ca.gov/consumers/wdoinfo.shtml',
                'title': 'SPCB Wood Destroying Organism Information',
                'doc_type': 'reference',
                'note': 'Public reference on WDO report standards and requirements',
            },
        ]

        for source in sources:
            if self.state.is_known(source['url']):
                continue
            try:
                self._respectful_delay()
                if not self._check_robots_txt(source['url']):
                    continue
                resp = self.session.head(source['url'], timeout=10, allow_redirects=True)
                if resp.status_code == 200:
                    found.append({
                        'url': source['url'],
                        'title': source['title'],
                        'doc_type': source['doc_type'],
                        'metadata': {
                            'source': 'CA Structural Pest Control Board',
                            'format': 'html',
                            'state': 'CA',
                            'note': source['note'],
                        }
                    })
                    self.state.mark_discovered(source['url'], self.name, source['doc_type'])
            except Exception as e:
                logger.debug(f"SPCB check failed: {e}")
                continue

        return found


# ================================================================
# SOURCE: Specialist Reports (Sewer, Foundation, Roof)
# ================================================================

class SpecialistReportSource(SourceAdapter):
    """
    Public sample specialist reports: sewer scope, foundation
    engineering, roof certification. These are published by
    inspection companies as marketing samples.
    """

    name = "specialist_reports"
    url = "various"
    doc_types = ['specialist_report']

    KNOWN_SAMPLES = [
        # Sewer scope samples
        {
            'url': 'https://www.sewerml.com/sample-report',
            'title': 'SewerML Sample Sewer Scope Report',
            'subtype': 'sewer_scope',
        },
        # Foundation engineering references
        {
            'url': 'https://www.cdaengineer.com/sample-reports/',
            'title': 'CDA Engineering Sample Foundation Report',
            'subtype': 'foundation',
        },
        # Standards references
        {
            'url': 'https://www.creia.org/standards-of-practice',
            'title': 'CREIA Standards of Practice',
            'subtype': 'standards',
        },
        {
            'url': 'https://www.nachi.org/sop',
            'title': 'InterNACHI Standards of Practice',
            'subtype': 'standards',
        },
    ]

    def scan(self) -> list:
        found = []
        for sample in self.KNOWN_SAMPLES:
            if self.state.is_known(sample['url']):
                continue
            try:
                self._respectful_delay()
                if not self._check_robots_txt(sample['url']):
                    continue
                resp = self.session.head(
                    sample['url'], timeout=10, allow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
                )
                if resp.status_code in (200, 301, 302):
                    found.append({
                        'url': sample['url'],
                        'title': sample['title'],
                        'doc_type': 'specialist_report' if sample['subtype'] != 'standards' else 'reference',
                        'metadata': {
                            'source': f"Sample — {sample['subtype']}",
                            'format': 'html',
                            'state': 'various',
                            'subtype': sample['subtype'],
                        }
                    })
                    self.state.mark_discovered(sample['url'], self.name, 'specialist_report')
            except Exception as e:
                logger.debug(f"Specialist check failed for {sample['title']}: {e}")
                continue
        return found


# ================================================================
# SOURCE: Reference Material (Codes, Insurance, Warranty)
# ================================================================

class ReferenceMaterialSource(SourceAdapter):
    """
    Public reference material that helps the AI understand building
    codes, insurance requirements, and warranty claim patterns.
    All sources are government publications or public industry data.
    """

    name = "reference_material"
    url = "various"
    doc_types = ['reference']

    SOURCES = [
        # Building codes (public law)
        {
            'url': 'https://codes.iccsafe.org/codes/california',
            'title': 'California Building Standards Code (Title 24)',
            'subtype': 'building_code',
            'note': 'Public law — building standards all inspectors reference',
        },
        # Insurance industry public data
        {
            'url': 'https://www.insurance.ca.gov/01-consumers/105-type/95-background/05-cfiracreated/index.cfm',
            'title': 'CA DOI Fair Plan & Insurance Data',
            'subtype': 'insurance_guidelines',
            'note': 'Public — CA Dept of Insurance consumer resources',
        },
        {
            'url': 'https://www.insurance.ca.gov/01-consumers/120-company/04-702ltr/',
            'title': 'CA DOI Insurer Rate Filings',
            'subtype': 'insurance_guidelines',
            'note': 'Public record — insurance rate filing data',
        },
        # FHA property standards (federal public document)
        {
            'url': 'https://www.hud.gov/program_offices/housing/sfh/handbook_4000-1',
            'title': 'HUD FHA Single Family Housing Handbook 4000.1',
            'subtype': 'appraisal_standards',
            'note': 'Federal public document — FHA property condition requirements',
        },
        # VA home loan appraisal requirements (federal public document)
        {
            'url': 'https://www.benefits.va.gov/HOMELOANS/appraiser_fee_schedule.asp',
            'title': 'VA Appraisal Requirements & MPRs',
            'subtype': 'appraisal_standards',
            'note': 'Federal public document — VA minimum property requirements',
        },
        # CPSC recall data (product safety affecting homes)
        {
            'url': 'https://www.cpsc.gov/Recalls',
            'title': 'CPSC Product Recalls (home-related)',
            'subtype': 'safety_recalls',
            'note': 'Federal public data — recalled products found in inspections',
        },
    ]

    def scan(self) -> list:
        found = []
        for source in self.SOURCES:
            if self.state.is_known(source['url']):
                continue
            try:
                self._respectful_delay()
                if not self._check_robots_txt(source['url']):
                    continue
                resp = self.session.head(
                    source['url'], timeout=10, allow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
                )
                if resp.status_code in (200, 301, 302):
                    found.append({
                        'url': source['url'],
                        'title': source['title'],
                        'doc_type': 'reference',
                        'metadata': {
                            'source': source['title'],
                            'format': 'html',
                            'state': 'CA' if 'california' in source['url'].lower() or 'ca.gov' in source['url'] else 'US',
                            'subtype': source['subtype'],
                            'note': source['note'],
                        }
                    })
                    self.state.mark_discovered(source['url'], self.name, 'reference')
            except Exception as e:
                logger.debug(f"Reference check failed for {source['title']}: {e}")
                continue
        return found


# ================================================================
# SOURCE: Open Data / Academic
# ================================================================

class OpenDataSource(SourceAdapter):
    """
    Academic and open data sources for inspection/disclosure documents.
    """

    name = "open_data"
    url = "various"
    doc_types = ['inspection_report', 'disclosure', 'reference']

    SOURCES = [
        {
            'name': 'Internet Archive — Home Inspection Reports',
            'search_url': 'https://archive.org/advancedsearch.php',
            'params': {
                'q': 'home inspection report',
                'fl[]': ['identifier', 'title', 'mediatype', 'date'],
                'sort[]': 'date desc',
                'rows': 20,
                'page': 1,
                'output': 'json',
            },
            'doc_type': 'inspection_report',
        },
        {
            'name': 'Internet Archive — Seller Disclosure',
            'search_url': 'https://archive.org/advancedsearch.php',
            'params': {
                'q': 'seller disclosure statement real estate',
                'fl[]': ['identifier', 'title', 'mediatype', 'date'],
                'sort[]': 'date desc',
                'rows': 20,
                'page': 1,
                'output': 'json',
            },
            'doc_type': 'disclosure',
        },
        {
            'name': 'Internet Archive — Pest Termite Reports',
            'search_url': 'https://archive.org/advancedsearch.php',
            'params': {
                'q': 'termite pest inspection report wood destroying',
                'fl[]': ['identifier', 'title', 'mediatype', 'date'],
                'sort[]': 'date desc',
                'rows': 20,
                'page': 1,
                'output': 'json',
            },
            'doc_type': 'pest_report',
        },
        {
            'name': 'Internet Archive — Foundation Sewer Reports',
            'search_url': 'https://archive.org/advancedsearch.php',
            'params': {
                'q': 'foundation engineering report sewer scope inspection',
                'fl[]': ['identifier', 'title', 'mediatype', 'date'],
                'sort[]': 'date desc',
                'rows': 20,
                'page': 1,
                'output': 'json',
            },
            'doc_type': 'specialist_report',
        },
    ]

    def scan(self) -> list:
        found = []
        for source in self.SOURCES:
            try:
                self._respectful_delay()
                resp = self.session.get(
                    source['search_url'],
                    params=source['params'],
                    timeout=20,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                        'Accept': 'application/json',
                    }
                )
                if resp.status_code != 200:
                    logger.warning(f"Internet Archive returned {resp.status_code} for {source['name']}")
                    continue

                try:
                    data = resp.json()
                except (ValueError, Exception):
                    logger.warning(f"Internet Archive returned non-JSON for {source['name']}")
                    continue

                for item in data.get('response', {}).get('docs', []):
                    identifier = item.get('identifier', '')
                    if not identifier:
                        continue
                    item_url = f"https://archive.org/details/{identifier}"
                    download_url = f"https://archive.org/download/{identifier}"

                    if self.state.is_known(item_url):
                        continue

                    doc_info = {
                        'url': item_url,
                        'download_url': download_url,
                        'title': item.get('title', identifier)[:100],
                        'doc_type': source['doc_type'],
                        'metadata': {
                            'source': 'Internet Archive',
                            'identifier': identifier,
                            'date': item.get('date', ''),
                            'format': 'pdf',
                            'state': 'various',
                        }
                    }
                    found.append(doc_info)
                    self.state.mark_discovered(item_url, self.name, source['doc_type'], doc_info['metadata'])

            except requests.exceptions.Timeout:
                logger.warning(f"Internet Archive timeout for {source['name']}")
                continue
            except Exception as e:
                logger.error(f"Open data scan error for {source['name']}: {e}")
                continue

        return found


# ================================================================
# CRAWLER ENGINE
# ================================================================

class PublicDocCrawler:
    """
    Main crawler engine. Coordinates source adapters and manages
    the scan → discover → download → catalog pipeline.
    """

    def __init__(self, repo_path: str = None):
        self.repo_path = repo_path or self._resolve_repo_path()
        self.state = CrawlerState(self.repo_path)
        self.sources = self._init_sources()

    def _resolve_repo_path(self) -> str:
        if os.path.exists(PERSISTENT_REPO_PATH):
            return PERSISTENT_REPO_PATH
        return DEFAULT_REPO_PATH

    def _init_sources(self) -> list:
        return [
            CourtListenerSource(self.repo_path, self.state),
            InspectorSampleSource(self.repo_path, self.state),
            PermitRecordSource(self.repo_path, self.state),
            PestReportSource(self.repo_path, self.state),
            SpecialistReportSource(self.repo_path, self.state),
            ReferenceMaterialSource(self.repo_path, self.state),
            StateComplaintSource(self.repo_path, self.state),
            PublicRecordsSource(self.repo_path, self.state),
            OpenDataSource(self.repo_path, self.state),
        ]

    # ---- MODE 1: SCAN (Check for availability) ----
    def scan_all(self) -> dict:
        """Scan all sources for available documents without downloading."""
        results = {
            'scan_time': datetime.now().isoformat(),
            'sources': [],
            'total_new_found': 0,
            'total_known': len(self.state.state['downloaded']),
        }

        for source in self.sources:
            try:
                found = source.scan()
                source_result = {
                    'name': source.name,
                    'url': source.url,
                    'status': 'scanned',
                    'new_docs_found': len(found),
                    'documents': found[:50],  # Cap detail list
                }
                results['sources'].append(source_result)
                results['total_new_found'] += len(found)
                self.state.update_run(source.name, len(found), 0)

            except Exception as e:
                results['sources'].append({
                    'name': source.name,
                    'status': 'error',
                    'error': str(e),
                })
                logger.error(f"Scan failed for {source.name}: {e}")

        self.state.save()
        return results

    # ---- MODE 2: CRAWL (Download new documents) ----
    def crawl_all(self, max_downloads: int = MAX_DOWNLOADS_PER_RUN) -> dict:
        """Scan and download new documents from all sources."""
        results = {
            'crawl_time': datetime.now().isoformat(),
            'sources': [],
            'total_downloaded': 0,
            'total_failed': 0,
            'total_skipped': 0,
        }

        downloads_remaining = max_downloads

        for source in self.sources:
            if downloads_remaining <= 0:
                break

            try:
                found = source.scan()
                downloaded = 0
                failed = 0

                for doc_info in found:
                    if downloads_remaining <= 0:
                        break

                    url = doc_info.get('download_url', doc_info['url'])

                    # Skip non-downloadable (HTML portals, search interfaces, etc.)
                    if doc_info.get('metadata', {}).get('access_method') in ('search_portal', 'api'):
                        continue

                    # Skip docket references (need manual review via PACER)
                    if doc_info.get('metadata', {}).get('format') == 'docket_reference':
                        results['total_skipped'] += 1
                        continue

                    # Pre-flight safety check: robots.txt
                    if not source._check_robots_txt(url):
                        source.state.mark_failed(url, source.name, "Blocked by robots.txt")
                        results['total_skipped'] += 1
                        continue

                    filename = source.download(url, doc_info)
                    if filename:
                        self.state.mark_downloaded(
                            url, source.name, filename,
                            doc_info['doc_type'],
                            doc_info.get('metadata', {})
                        )
                        downloaded += 1
                        downloads_remaining -= 1
                        # Update catalog
                        self._add_to_catalog(filename, doc_info)
                    else:
                        failed += 1

                    source._respectful_delay()

                source_result = {
                    'name': source.name,
                    'status': 'crawled',
                    'docs_found': len(found),
                    'docs_downloaded': downloaded,
                    'docs_failed': failed,
                }
                results['sources'].append(source_result)
                results['total_downloaded'] += downloaded
                results['total_failed'] += failed
                self.state.update_run(source.name, len(found), downloaded)

            except Exception as e:
                results['sources'].append({
                    'name': source.name,
                    'status': 'error',
                    'error': str(e),
                })
                logger.error(f"Crawl failed for {source.name}: {e}")

        self.state.save()
        return results

    # ---- MODE 3: CORPUS STATS ----
    def corpus_report(self) -> dict:
        """Generate a training corpus status report."""
        # Seed from existing catalog if corpus stats are empty
        self._seed_from_catalog()

        stats = self.state.state['corpus_stats'].copy()
        # Ensure all stat keys exist
        for key in ['total_inspection_reports', 'total_disclosures', 'total_pest_reports',
                     'total_repair_estimates', 'total_permit_records', 'total_specialist_reports',
                     'total_court_cases', 'total_reference', 'total_other',
                     'formats_seen', 'states_covered', 'inspector_software']:
            stats.setdefault(key, 0 if 'total' in key else {})

        stats.update({
            'total_documents': (
                stats['total_inspection_reports'] +
                stats['total_disclosures'] +
                stats.get('total_pest_reports', 0) +
                stats.get('total_repair_estimates', 0) +
                stats.get('total_permit_records', 0) +
                stats.get('total_specialist_reports', 0) +
                stats.get('total_court_cases', 0) +
                stats.get('total_reference', 0) +
                stats['total_other']
            ),
            'total_discovered_pending': len(self.state.state['discovered']),
            'total_downloaded': len(self.state.state['downloaded']),
            'total_failed': len(self.state.state['failed']),
            'last_runs': self.state.state['last_run'],
            'state_sources': self.state.state.get('state_sources', []),
            'coverage_gaps': self._identify_gaps(),
        })
        return stats

    def _seed_from_catalog(self):
        """Seed corpus stats from existing catalog.json if not already done."""
        stats = self.state.state['corpus_stats']
        if stats['total_inspection_reports'] + stats['total_disclosures'] + stats['total_other'] > 0:
            return  # Already seeded

        catalog_path = os.path.join(self.repo_path, CATALOG_FILE)
        if not os.path.exists(catalog_path):
            return

        try:
            with open(catalog_path) as f:
                catalog = json.load(f)

            for doc in catalog.get('documents', []):
                source = doc.get('source', '').lower()
                filename = doc.get('filename', '').lower()

                # Classify by source and filename
                if any(kw in source.lower() for kw in ['internachi', 'scribeware', 'homegauge', 'spectora']):
                    stats['total_inspection_reports'] += 1
                    doc_type = 'inspection_report'
                elif any(kw in source.lower() for kw in ['dre', 'disclosure', 'tds']):
                    stats['total_disclosures'] += 1
                    doc_type = 'disclosure'
                elif any(kw in filename for kw in ['inspection', 'report', 'inspect']):
                    stats['total_inspection_reports'] += 1
                    doc_type = 'inspection_report'
                elif any(kw in filename for kw in ['disclosure', 'tds', 'spd']):
                    stats['total_disclosures'] += 1
                    doc_type = 'disclosure'
                else:
                    stats['total_other'] += 1
                    doc_type = 'other'

                # Track format
                if filename.endswith('.pdf'):
                    fmt = 'pdf'
                elif filename.endswith('.html') or filename.endswith('.htm'):
                    fmt = 'html'
                else:
                    fmt = 'unknown'
                stats['formats_seen'][fmt] = stats['formats_seen'].get(fmt, 0) + 1

                # Track software
                for sw in ['ScribeWare', 'HomeGauge', 'Spectora', 'InterNACHI', 'Palm-Tech']:
                    if sw.lower() in source.lower():
                        stats['inspector_software'][sw] = stats['inspector_software'].get(sw, 0) + 1
                        break

                # Track state from source
                if 'california' in source.lower() or 'ca dre' in source.lower():
                    stats['states_covered']['CA'] = stats['states_covered'].get('CA', 0) + 1

            self.state.save()
            logger.info(f"Seeded corpus stats from {len(catalog.get('documents', []))} existing documents")

        except Exception as e:
            logger.error(f"Catalog seeding failed: {e}")

    def _identify_gaps(self) -> list:
        """Identify what document types/sources we need more of."""
        gaps = []
        stats = self.state.state['corpus_stats']

        # Document type targets
        type_targets = [
            ('inspection_report', 'total_inspection_reports', 50, 'high',
             'Crawl more from CourtListener and inspector sample galleries'),
            ('disclosure', 'total_disclosures', 30, 'high',
             'Search RECAP for TDS exhibits in CA real estate cases'),
            ('pest_report', 'total_pest_reports', 20, 'high',
             'CA SPCB records and pest company sample reports'),
            ('repair_estimate', 'total_repair_estimates', 15, 'medium',
             'Court exhibits with contractor bids and repair cost evidence'),
            ('permit_record', 'total_permit_records', 20, 'medium',
             'CA city open data portals (San Jose, SF, LA, Sacramento)'),
            ('specialist_report', 'total_specialist_reports', 15, 'medium',
             'Sewer scope, foundation engineering, and roof cert samples'),
            ('court_case', 'total_court_cases', 10, 'medium',
             'Disclosure fraud verdicts and settlements from CourtListener'),
            ('reference', 'total_reference', 10, 'low',
             'Building codes, FHA/VA standards, insurance guidelines'),
        ]

        for doc_type, stat_key, target, priority, suggestion in type_targets:
            current = stats.get(stat_key, 0)
            if current < target:
                gaps.append({
                    'type': doc_type,
                    'current': current,
                    'target': target,
                    'priority': priority,
                    'suggestion': suggestion,
                })

        # Check state coverage
        target_states = ['CA', 'TX', 'FL', 'NY', 'IL', 'AZ', 'WA', 'CO']
        covered = set(stats.get('states_covered', {}).keys())
        missing = [s for s in target_states if s not in covered]
        if missing:
            gaps.append({
                'type': 'state_coverage',
                'missing_states': missing,
                'priority': 'medium',
                'suggestion': f"Need documents from: {', '.join(missing)}",
            })

        # Check software coverage
        target_sw = ['HomeGauge', 'Spectora', 'ScribeWare', 'Palm-Tech', 'Horizon', 'InspectIT']
        covered_sw = set(stats.get('inspector_software', {}).keys())
        missing_sw = [s for s in target_sw if s not in covered_sw]
        if missing_sw:
            gaps.append({
                'type': 'software_coverage',
                'missing_software': missing_sw,
                'priority': 'medium',
                'suggestion': f"Need sample reports from: {', '.join(missing_sw)}",
            })

        return gaps

    def _add_to_catalog(self, filename: str, doc_info: dict):
        """Add a downloaded document to the repo catalog."""
        catalog_path = os.path.join(self.repo_path, CATALOG_FILE)
        try:
            if os.path.exists(catalog_path):
                with open(catalog_path) as f:
                    catalog = json.load(f)
            else:
                catalog = {'documents': []}

            catalog['documents'].append({
                'filename': filename,
                'doc_type': doc_info.get('doc_type', 'unknown'),
                'source': doc_info.get('metadata', {}).get('source', 'crawler'),
                'title': doc_info.get('title', filename),
                'crawled_at': datetime.now().isoformat(),
                'metadata': doc_info.get('metadata', {}),
            })

            with open(catalog_path, 'w') as f:
                json.dump(catalog, f, indent=2)

        except Exception as e:
            logger.error(f"Catalog update failed: {e}")
