"""Real crawler for darwix.ai.

Fetches the live sitemap and every page's server HTML (no JS rendering —
deliberately, since that's exactly what Google's first pass and most AI
crawlers see too) and extracts the SEO signals every agent needs:
title, meta description, canonical, robots meta, headings, word count,
internal links, images/alt text, and JSON-LD schema blocks.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config


@dataclass
class PageSnapshot:
    url: str
    status: int | None = None
    error: str | None = None
    fetch_ms: int = 0
    bytes: int = 0
    title: str | None = None
    meta_description: str | None = None
    canonical: str | None = None
    robots_meta: str | None = None
    og: dict = field(default_factory=dict)
    twitter: dict = field(default_factory=dict)
    h1s: list[str] = field(default_factory=list)
    headings: list[tuple[str, str]] = field(default_factory=list)  # (tag, text) in doc order
    word_count: int = 0
    internal_links: list[str] = field(default_factory=list)  # absolute, de-duped
    external_links: list[str] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)  # {src, alt}
    json_ld: list[dict] = field(default_factory=list)
    has_meta_keywords: bool = False
    lang: str | None = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["headings"] = [{"tag": t, "text": x} for t, x in self.headings]
        return d


def fetch_sitemap() -> list[dict]:
    """Returns [{loc, lastmod, changefreq, priority}, ...] from the live sitemap."""
    r = requests.get(config.SITEMAP_URL, headers={"User-Agent": config.USER_AGENT},
                      timeout=config.REQUEST_TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    # The sitemap namespace varies by host; strip it rather than guess it.
    ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
    tag = lambda name: f"{{{ns}}}{name}" if ns else name
    out = []
    for url_tag in root.findall(tag("url")):
        def text_of(child):
            el = url_tag.find(tag(child))
            return el.text.strip() if el is not None and el.text else None
        out.append({
            "loc": text_of("loc"),
            "lastmod": text_of("lastmod"),
            "changefreq": text_of("changefreq"),
            "priority": text_of("priority"),
        })
    return out


def fetch_robots() -> str | None:
    try:
        r = requests.get(config.ROBOTS_URL, headers={"User-Agent": config.USER_AGENT},
                          timeout=config.REQUEST_TIMEOUT)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _abs(base: str, href: str) -> str:
    return urljoin(base, href)


def fetch_page(url: str) -> PageSnapshot:
    snap = PageSnapshot(url=url)
    t0 = time.monotonic()
    try:
        r = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                          timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        snap.error = str(e)
        snap.fetch_ms = int((time.monotonic() - t0) * 1000)
        return snap

    snap.fetch_ms = int((time.monotonic() - t0) * 1000)
    snap.status = r.status_code
    snap.bytes = len(r.content)
    if r.status_code >= 400:
        return snap

    soup = BeautifulSoup(r.text, "html.parser")

    html_tag = soup.find("html")
    snap.lang = html_tag.get("lang") if html_tag else None

    if soup.title and soup.title.string:
        snap.title = soup.title.string.strip()

    for m in soup.find_all("meta"):
        name = (m.get("name") or "").lower()
        prop = (m.get("property") or "").lower()
        content = m.get("content")
        if name == "description" and content:
            snap.meta_description = content.strip()
        elif name == "keywords" and content:
            snap.has_meta_keywords = True
        elif name == "robots" and content:
            snap.robots_meta = content.strip()
        elif prop.startswith("og:") and content:
            snap.og[prop[3:]] = content
        elif name.startswith("twitter:") and content:
            snap.twitter[name[8:]] = content

    link_canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    if link_canonical and link_canonical.get("href"):
        snap.canonical = _abs(url, link_canonical["href"])

    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        text = tag.get_text(strip=True)
        snap.headings.append((tag.name, text))
        if tag.name == "h1":
            snap.h1s.append(text)

    body = soup.body or soup
    text = body.get_text(" ", strip=True)
    snap.word_count = len(text.split())

    seen_int, seen_ext = set(), set()
    parsed_base = urlparse(url)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = _abs(url, href)
        p = urlparse(absu)
        if p.netloc == parsed_base.netloc or p.netloc == "":
            clean = absu.split("#")[0]
            if clean not in seen_int:
                seen_int.add(clean)
                snap.internal_links.append(clean)
        else:
            if absu not in seen_ext:
                seen_ext.add(absu)
                snap.external_links.append(absu)

    for img in soup.find_all("img"):
        snap.images.append({"src": img.get("src", ""), "alt": img.get("alt")})

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "{}")
            snap.json_ld.append(payload)
        except (json.JSONDecodeError, TypeError):
            pass

    return snap


def crawl_site(urls: list[str] | None = None, *, polite_delay: float = 0.3) -> dict[str, PageSnapshot]:
    """Crawl the given URLs (or every sitemap URL if None). Real HTTP requests."""
    if urls is None:
        urls = [e["loc"] for e in fetch_sitemap() if e.get("loc")]
    snapshots: dict[str, PageSnapshot] = {}
    for u in urls:
        snapshots[u] = fetch_page(u)
        time.sleep(polite_delay)
    return snapshots
