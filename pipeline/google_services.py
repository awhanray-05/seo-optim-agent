"""Google services for the dashboard's real-data panels and real sitemap
submission.

Four independent integrations, each gated on its own env vars so any
subset can be configured without the others (see pipeline/config.py):

  - PageSpeed Insights  (GOOGLE_PAGESPEED_API_KEY)       — API key only
  - Chrome UX Report     (GOOGLE_CRUX_API_KEY)            — API key only
  - Search Console       (GOOGLE_SERVICE_ACCOUNT_FILE,
                           GSC_SITE_URL)                  — service account
  - GA4 Data API          (GOOGLE_SERVICE_ACCOUNT_FILE,
                           GA4_PROPERTY_ID)                — service account

Every public function here degrades to returning None on missing
credentials, a bad/expired key, or any request failure — callers (mainly
dashboard_data.py) treat None as "leave that panel on sample data," the
same pattern pipeline/llm.py uses for the LLM stub.
"""
from __future__ import annotations

import datetime as dt

import requests

from . import config

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_URL = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_SUBMIT_SCOPE = "https://www.googleapis.com/auth/webmasters"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

_credentials_cache: dict = {}


def _sa_file_path():
    if config.GOOGLE_SERVICE_ACCOUNT_FILE:
        p = config.ROOT / config.GOOGLE_SERVICE_ACCOUNT_FILE
        if p.exists():
            return p
    for candidate in ("google-service-account.json", "credentials/google-service-account.json"):
        p = config.ROOT / candidate
        if p.exists():
            return p
    return None


def psi_is_configured() -> bool:
    return bool(config.GOOGLE_PAGESPEED_API_KEY)


def crux_is_configured() -> bool:
    return bool(config.GOOGLE_CRUX_API_KEY)


def gsc_is_configured() -> bool:
    return bool(_sa_file_path() and config.GSC_SITE_URL)


def ga4_is_configured() -> bool:
    return bool(_sa_file_path() and config.GA4_PROPERTY_ID)


def _service_account_token(scopes: list[str]) -> str | None:
    """Mint (and cache per scope-set) an access token for the service
    account at GOOGLE_SERVICE_ACCOUNT_FILE. Returns None on any failure —
    missing file, malformed key, network error — so callers can fall back
    to sample data instead of crashing the run."""
    sa_path = _sa_file_path()
    if not sa_path:
        return None
    cache_key = tuple(sorted(scopes))
    cached = _credentials_cache.get(cache_key)
    if cached and cached[1] > dt.datetime.now(dt.timezone.utc):
        return cached[0]
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError:
        print("[google_services] google-auth not installed — run `pip install -r requirements.txt`")
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            str(sa_path), scopes=scopes)
        creds.refresh(Request())
    except Exception as e:  # malformed key file, revoked key, no network, etc.
        print(f"[google_services] could not mint a token for {scopes}: {e}")
        return None
    _credentials_cache[cache_key] = (creds.token, creds.expiry.replace(tzinfo=dt.timezone.utc))
    return creds.token


# --------------------------------------------------------------------------
# PageSpeed Insights — no OAuth, just an API key.
# --------------------------------------------------------------------------

def psi_report(url: str, strategy: str = "mobile") -> dict | None:
    """Performance score (0-100) + Core Web Vitals field data for one URL,
    or None if unconfigured/unavailable (e.g. too little real-user traffic
    for field data — common for a low-traffic page)."""
    if not psi_is_configured():
        return None
    try:
        r = requests.get(PSI_URL, timeout=config.REQUEST_TIMEOUT, params={
            "url": url, "key": config.GOOGLE_PAGESPEED_API_KEY,
            "category": "performance", "strategy": strategy,
        })
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        print(f"[google_services:psi] {url}: {e}")
        return None

    lab_score = None
    try:
        lab_score = round(data["lighthouseResult"]["categories"]["performance"]["score"] * 100)
    except (KeyError, TypeError):
        pass

    audits = data.get("lighthouseResult", {}).get("audits", {})
    lab_lcp = audits.get("largest-contentful-paint", {}).get("numericValue")
    lab_cls = audits.get("cumulative-layout-shift", {}).get("numericValue")
    lab_tbt = audits.get("total-blocking-time", {}).get("numericValue")

    field = data.get("loadingExperience", {}).get("metrics", {})
    lcp_val = field.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile")
    if lcp_val is None:
        lcp_val = lab_lcp

    cls_val = field.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile")
    if cls_val is not None:
        cls_val = cls_val / 100.0 if cls_val > 1 else cls_val
    elif lab_cls is not None:
        cls_val = lab_cls

    inp_val = field.get("INTERACTION_TO_NEXT_PAINT", {}).get("percentile")
    if inp_val is None:
        inp_val = lab_tbt

    return {
        "url": url,
        "lab_performance_score": lab_score,
        "field_lcp_ms": lcp_val,
        "field_cls": cls_val,
        "field_inp_ms": inp_val,
    }


# --------------------------------------------------------------------------
# Chrome UX Report — no OAuth, just an API key. Field data only; 404s for
# an origin/URL without enough real-user traffic in the last 28 days.
# --------------------------------------------------------------------------

def crux_record(origin_or_url: str, *, is_origin: bool = False) -> dict | None:
    if not crux_is_configured():
        return None
    body = {"origin": origin_or_url} if is_origin else {"url": origin_or_url}
    try:
        r = requests.post(CRUX_URL, timeout=config.REQUEST_TIMEOUT,
                           params={"key": config.GOOGLE_CRUX_API_KEY}, json=body)
        if r.status_code == 404:
            return None  # not enough real-user traffic for this origin/URL
        r.raise_for_status()
        metrics = r.json().get("record", {}).get("metrics", {})
    except requests.RequestException as e:
        print(f"[google_services:crux] {origin_or_url}: {e}")
        return None

    def p75(key: str):
        v = metrics.get(key, {}).get("percentiles", {}).get("p75")
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                return v
        return None

    def histogram_pct(key: str) -> tuple[float, float, float] | None:
        """(good%, needs-improvement%, poor%) from CrUX's 3-bucket histogram,
        or None if that metric has no data for this origin/URL."""
        buckets = metrics.get(key, {}).get("histogram")
        if not buckets or len(buckets) != 3:
            return None
        return tuple(round(b.get("density", 0) * 100) for b in buckets)

    return {
        "lcp_ms": p75("largest_contentful_paint"),
        "lcp_histogram": histogram_pct("largest_contentful_paint"),
        "cls": p75("cumulative_layout_shift"),
        "cls_histogram": histogram_pct("cumulative_layout_shift"),
        "inp_ms": p75("interaction_to_next_paint"),
        "inp_histogram": histogram_pct("interaction_to_next_paint"),
    }


# --------------------------------------------------------------------------
# Search Console — service account must be added as a user on the property
# (Search Console > Settings > Users and permissions > Add user).
# --------------------------------------------------------------------------

def gsc_search_analytics(start_date: str, end_date: str, dimensions: list[str],
                          row_limit: int = 1000) -> list[dict] | None:
    if not gsc_is_configured():
        return None
    token = _service_account_token([GSC_SCOPE])
    if not token:
        return None
    site = config.GSC_SITE_URL
    import urllib.parse as up
    url = (f"https://www.googleapis.com/webmasters/v3/sites/{up.quote(site, safe='')}"
           "/searchAnalytics/query")
    try:
        r = requests.post(url, timeout=config.REQUEST_TIMEOUT,
                           headers={"Authorization": f"Bearer {token}"},
                           json={"startDate": start_date, "endDate": end_date,
                                 "dimensions": dimensions, "rowLimit": row_limit})
        r.raise_for_status()
        return r.json().get("rows", [])
    except requests.RequestException as e:
        print(f"[google_services:gsc] {e}")
        return None


def submit_sitemap() -> dict | None:
    """Actually submit https://darwix.ai/sitemap.xml to Search Console
    (sites.sitemaps.submit). Needs the GSC_SUBMIT_SCOPE, broader than the
    read-only scope used for reporting."""
    if not gsc_is_configured():
        return None
    token = _service_account_token([GSC_SUBMIT_SCOPE])
    if not token:
        return {"ok": False, "error": "could not authenticate service account"}
    import urllib.parse as up
    site = config.GSC_SITE_URL
    feedpath = up.quote(f"{config.SITE_ROOT}/sitemap.xml", safe="")
    url = f"https://www.googleapis.com/webmasters/v3/sites/{up.quote(site, safe='')}/sitemaps/{feedpath}"
    try:
        r = requests.put(url, timeout=config.REQUEST_TIMEOUT, headers={"Authorization": f"Bearer {token}"})
        return {"ok": r.status_code in (200, 204), "status_code": r.status_code}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------
# GA4 Data API — service account must be added as a Viewer on the GA4
# property (Admin > Property Access Management > Add users).
# --------------------------------------------------------------------------

def ga4_run_report(metrics: list[str], dimensions: list[str] | None,
                    start_date: str, end_date: str,
                    dimension_filter: dict | None = None) -> list[dict] | None:
    if not ga4_is_configured():
        return None
    token = _service_account_token([GA4_SCOPE])
    if not token:
        return None
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{config.GA4_PROPERTY_ID}:runReport"
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "metrics": [{"name": m} for m in metrics],
    }
    if dimensions:
        body["dimensions"] = [{"name": d} for d in dimensions]
    if dimension_filter:
        body["dimensionFilter"] = dimension_filter
    try:
        r = requests.post(url, timeout=config.REQUEST_TIMEOUT,
                           headers={"Authorization": f"Bearer {token}"}, json=body)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        print(f"[google_services:ga4] {e}")
        return None

    dim_names = [d["name"] for d in data.get("dimensionHeaders", [])]
    metric_names = [m["name"] for m in data.get("metricHeaders", [])]
    rows = []
    for row in data.get("rows", []):
        rec = {name: val["value"] for name, val in zip(dim_names, row.get("dimensionValues", []))}
        rec.update({name: val["value"] for name, val in zip(metric_names, row.get("metricValues", []))})
        rows.append(rec)
    return rows


def ga4_event_count(event_name: str, start_date: str, end_date: str) -> int | None:
    rows = ga4_run_report(
        ["eventCount"], ["eventName"], start_date, end_date,
        dimension_filter={"filter": {"fieldName": "eventName",
                                      "stringFilter": {"value": event_name}}},
    )
    if rows is None:
        return None
    return sum(int(r.get("eventCount", 0)) for r in rows)


def status() -> dict:
    """One-line-per-service summary for the run_pipeline.py startup banner."""
    return {
        "PageSpeed Insights": psi_is_configured(),
        "Chrome UX Report": crux_is_configured(),
        "Search Console": gsc_is_configured(),
        "GA4": ga4_is_configured(),
    }
