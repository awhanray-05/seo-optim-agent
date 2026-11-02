"""Agent 04 — Publishing & Indexing.

Reads: approved pages (QA status == qa_passed).
Produces: a staged publish package per page (diff of current vs proposed
          fields), a regenerated sitemap.xml, a redirect map, and a
          simulated sitemap-submission / URL-inspection result.

Writes everything a real integration needs, either way:
  data/publish_package/<slugged-url>.json   — the exact field-level diff
  data/publish_package/sitemap.xml          — the sitemap it would submit
  data/publish_package/redirect_map.json    — old-URL -> new-URL 301 rules

If GITHUB_TOKEN/GITHUB_REPO are set (see github_publisher.py), the same
files are also pushed to a review branch in GITHUB_REPO and opened as a PR
against GITHUB_DEFAULT_BRANCH — this agent still never pushes straight to
the live branch, since it has no way to know the target repo's real
page-template schema. If GSC credentials are set (see google_services.py),
the sitemap is also actually submitted to Search Console. Either or both
missing -> this agent falls back to local-staging-only, same as before.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from urllib.parse import urlparse

from . import config, github_publisher, google_services, stores

AGENT = config.AGENT_BY_ID["publishing"]
PACKAGE_DIR = config.DATA_DIR / "publish_package"


def _slug_filename(url: str) -> str:
    p = urlparse(url)
    slug = (p.path.strip("/") or "home").replace("/", "__")
    return re.sub(r"[^a-zA-Z0-9_-]", "-", slug) + ".json"


def _diff_page(url: str, rec: dict) -> dict:
    current = rec.get("current", {})
    proposed = rec.get("proposed", {})
    diff = {}
    diff["title"] = {"from": current.get("title"), "to": proposed.get("title")}
    diff["meta_description"] = {"from": current.get("meta_description"), "to": proposed.get("meta_description")}
    diff["h1"] = {"from": (current.get("h1s") or [None])[0], "to": proposed.get("h1")}
    diff["canonical"] = {"from": current.get("canonical"), "to": proposed.get("canonical")}
    diff["schema_blocks_added"] = len(proposed.get("schema", []))
    diff["image_alt_fixes"] = proposed.get("image_alt_fixes", [])
    diff["meta_keywords_removed"] = bool(proposed.get("meta_keywords_removed"))
    diff["internal_links_added"] = proposed.get("link_anchor_text", [])
    return diff


def _needs_redirect(url: str, proposed_canonical: str | None) -> dict | None:
    """M2: any URL whose normalized form differs — including a pure case
    change like /voiceAgent vs /voice-agent — needs a 301, since darwix.ai
    is case-sensitive (/voiceagent 404s today)."""
    if not proposed_canonical:
        return None
    old = url.rstrip("/")
    new = proposed_canonical.rstrip("/")
    if old != new:
        return {"from": url, "to": proposed_canonical, "type": 301}
    return None


def build_sitemap_xml(entries: list[dict]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for e in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{e['loc']}</loc>")
        lines.append(f"    <lastmod>{e['lastmod']}</lastmod>")
        if e.get("changefreq"):
            lines.append(f"    <changefreq>{e['changefreq']}</changefreq>")
        if e.get("priority"):
            lines.append(f"    <priority>{e['priority']}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines)


def run() -> dict:
    page_db = stores.load("page_db", {})
    qa_reports = stores.load("qa_reports", {})
    publish_log = stores.load("publish_log", {})

    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()

    approved = [u for u, r in qa_reports.items() if r.get("overall") == "pass"]
    redirects = []
    sitemap_entries = []
    staged = 0

    for url, rec in page_db.items():
        current = rec.get("current", {})
        # H4 fix: never list a redirecting or non-200 URL in the sitemap.
        if current.get("status") != 200:
            continue
        # H4 fix: never list a URL whose rendered path is a dead end
        # (the audit's /idn/* pages redirect to the homepage).
        if "/idn/" in url:
            continue
        proposed = rec.get("proposed", {})
        lastmod = today if url in approved else (current.get("lastmod") or today)
        sitemap_entries.append({
            "loc": proposed.get("canonical", url),
            "lastmod": lastmod,
            "changefreq": "weekly" if rec.get("sitemap", {}).get("parent") == "Home" else "monthly",
            "priority": "1.0" if url.rstrip("/") == "https://darwix.ai" else "0.7",
        })
        rmap = _needs_redirect(url, proposed.get("canonical"))
        if rmap:
            redirects.append(rmap)

        if url not in approved:
            continue

        diff = _diff_page(url, rec)
        (PACKAGE_DIR / _slug_filename(url)).write_text(
            json.dumps({"url": url, "diff": diff}, indent=2, ensure_ascii=False), encoding="utf-8")
        publish_log[url] = {"published_at": today, "status": "staged", "diff_file": _slug_filename(url)}
        stores.update_page(url, {"status": "staged_for_publish"})
        staged += 1

    sitemap_xml = build_sitemap_xml(sitemap_entries)
    (PACKAGE_DIR / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")
    (PACKAGE_DIR / "redirect_map.json").write_text(
        json.dumps(redirects, indent=2), encoding="utf-8")

    stores.save("publish_log", publish_log)

    github_result = None
    if github_publisher.is_configured():
        branch = f"seo-automation/{today}"
        diff_files_today = [e["diff_file"] for e in publish_log.values() if e.get("published_at") == today]
        files = {name: (PACKAGE_DIR / name).read_text(encoding="utf-8")
                 for name in [*diff_files_today, "sitemap.xml", "redirect_map.json"]}
        github_result = github_publisher.publish_package(
            branch, files,
            pr_title=f"SEO automation: {staged} page(s) staged, {len(redirects)} redirect(s) — {today}",
            pr_body=(f"Automated run of the SEO pipeline staged {staged} approved page(s) and "
                     f"proposed {len(redirects)} redirect rule(s). Files under "
                     f"`{config.GITHUB_CONTENT_PREFIX}/` are the exact field-level diffs — review "
                     f"before merging; nothing here has touched the live branch."),
        )
        if github_result.get("ok"):
            stores.append_audit("publish.agent",
                                 f"Opened/updated GitHub PR #{github_result.get('pr_number')} on "
                                 f"{config.GITHUB_REPO}", github_result.get("pr_url") or branch)
        else:
            stores.append_audit("publish.agent", f"GitHub publish failed: {github_result.get('error')}", branch)

    gsc_result = google_services.submit_sitemap() if google_services.gsc_is_configured() else None

    mode_bits = []
    mode_bits.append("GitHub PR opened" if github_result and github_result.get("ok")
                      else ("GitHub publish failed" if github_result else "no GitHub credentials configured"))
    mode_bits.append("sitemap submitted to Search Console" if gsc_result and gsc_result.get("ok")
                      else "no Search Console credentials configured")
    submission_result = {
        "sitemap_url_would_submit": "https://darwix.ai/sitemap.xml",
        "urls_in_sitemap": len(sitemap_entries),
        "redirect_rules_proposed": len(redirects),
        "pages_staged_this_run": staged,
        "mode": "; ".join(mode_bits),
        "github": github_result,
        "search_console": gsc_result,
    }

    stores.append_audit(
        "publish.agent",
        f"Staged {staged} approved page(s); rebuilt sitemap ({len(sitemap_entries)} URLs); "
        f"proposed {len(redirects)} redirect rule(s)",
        f"publish_package/ · {len(redirects)} redirects, {staged} pages",
    )

    return {"agent": AGENT, "staged": staged, "sitemap_urls": len(sitemap_entries),
            "redirects": len(redirects), "submission_result": submission_result}
