"""Base class for web crawlers (Stream 2).

A crawler fetches pages from a public data source and extracts inspection
findings. Each site gets its own subclass that implements two methods:

  get_urls_to_crawl() -> list[str]
      Return the URLs to fetch. Typically reads from a sitemap, listing page,
      or known pattern.

  parse_page(url: str, html: str) -> list[dict]
      Given fetched HTML, extract findings. Each dict must have:
          {'finding_text': str, 'category': str, 'severity': str}
      Optional extras:
          {'confidence': float, 'geographic_region': str, 'property_age_bucket': str}

The base class handles:
  - Rate limiting (configurable delay between requests)
  - User-Agent identification (so site owners can contact us)
  - Retries with exponential backoff on transient failures
  - robots.txt compliance
  - Job tracking via BaseIngestionJob
  - Deduplication against existing data

Subclasses should NOT override run_job() — the base implementation handles
the fetch/parse/save loop correctly. They should ONLY implement get_urls_to_crawl
and parse_page.
"""
from __future__ import annotations

import time
import urllib.robotparser
from typing import Optional
from urllib.parse import urlparse

import requests

from ml_ingestion.base import BaseIngestionJob


class BaseCrawler(BaseIngestionJob):
    """Base class for Stream 2 web crawlers.

    Required class attrs for subclasses:
      SOURCE_NAME: str   — e.g. 'zillow_v1'
      USER_AGENT: str    — identifies the crawler, include contact
      REQUEST_DELAY_SECONDS: float = 2.0  — politeness delay between requests
      MAX_RETRIES: int = 3
      TIMEOUT_SECONDS: int = 30
    """

    JOB_TYPE = 'crawl'

    # Default settings — subclasses can override
    USER_AGENT = 'OfferWiseBot/1.0 (+https://getofferwise.ai/about; contact: francis@getofferwise.ai)'
    REQUEST_DELAY_SECONDS = 2.0
    MAX_RETRIES = 3
    TIMEOUT_SECONDS = 30
    MAX_URLS_PER_RUN: Optional[int] = None  # None = no cap

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': self.USER_AGENT})

    # ── Subclass hooks ────────────────────────────────────────────────
    def get_urls_to_crawl(self) -> list[str]:
        """Return URLs to fetch. Subclasses implement this."""
        raise NotImplementedError('subclass must implement get_urls_to_crawl()')

    def parse_page(self, url: str, html: str) -> list[dict]:
        """Extract findings from HTML. Subclasses implement this.

        Returns list of dicts, each:
            {'finding_text': str, 'category': str, 'severity': str,
             'confidence': float (optional),
             'geographic_region': str (optional),
             'property_age_bucket': str (optional)}
        """
        raise NotImplementedError('subclass must implement parse_page()')

    # ── Base orchestration ────────────────────────────────────────────
    def run_job(self) -> None:
        urls = self.get_urls_to_crawl()
        self._log(f'Found {len(urls)} URLs to crawl')

        if self.MAX_URLS_PER_RUN and len(urls) > self.MAX_URLS_PER_RUN:
            self._log(f'Capping to first {self.MAX_URLS_PER_RUN} URLs', 'warn')
            urls = urls[:self.MAX_URLS_PER_RUN]

        fetched = 0
        skipped_robots = 0
        failed_fetch = 0
        empty_pages = 0

        for url in urls:
            if not self._can_fetch(url):
                skipped_robots += 1
                continue

            html = self._fetch_with_retries(url)
            if html is None:
                failed_fetch += 1
                continue

            fetched += 1
            try:
                findings = self.parse_page(url, html)
                if not findings:
                    empty_pages += 1
                    continue
                for f in findings:
                    self._add_finding(
                        finding_text=f.get('finding_text', ''),
                        category=f.get('category', ''),
                        severity=f.get('severity', ''),
                        confidence=f.get('confidence', 0.85),
                        geographic_region=f.get('geographic_region'),
                        property_age_bucket=f.get('property_age_bucket'),
                    )
            except Exception as parse_err:
                self._log(f'Parse error on {url}: {parse_err}', 'warn')

            time.sleep(self.REQUEST_DELAY_SECONDS)

        self._finalize()
        self._log(f'Crawl complete: fetched={fetched} robots_skip={skipped_robots} '
                  f'fetch_fail={failed_fetch} empty={empty_pages}')

    # ── Internal helpers ──────────────────────────────────────────────
    def _can_fetch(self, url: str) -> bool:
        """Check robots.txt before fetching. Caches one parser per host."""
        try:
            parsed = urlparse(url)
            host = f'{parsed.scheme}://{parsed.netloc}'
            rp = self._robots_cache.get(host)
            if rp is None:
                rp = urllib.robotparser.RobotFileParser()
                rp.set_url(f'{host}/robots.txt')
                try:
                    rp.read()
                except Exception:
                    # If robots.txt can't be fetched, default to permissive —
                    # we're a well-behaved bot with a contact address.
                    return True
                self._robots_cache[host] = rp
            return rp.can_fetch(self.USER_AGENT, url)
        except Exception:
            return True  # fail open on parse errors

    def _fetch_with_retries(self, url: str) -> Optional[str]:
        """Fetch a URL with exponential backoff on transient failures."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._session.get(url, timeout=self.TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (429, 503):
                    # Rate limited or temporarily unavailable — back off
                    delay = (2 ** attempt) * self.REQUEST_DELAY_SECONDS
                    self._log(f'Got {resp.status_code} on {url}, waiting {delay}s', 'warn')
                    time.sleep(delay)
                    continue
                if 400 <= resp.status_code < 500:
                    # Hard client error — don't retry
                    return None
                # 5xx — retry with backoff
                time.sleep((2 ** attempt) * self.REQUEST_DELAY_SECONDS)
            except requests.RequestException as e:
                self._log(f'Fetch error on {url}: {e}', 'warn')
                time.sleep((2 ** attempt) * self.REQUEST_DELAY_SECONDS)
        return None
