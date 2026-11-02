"""Agent 05 — SEO Monitoring.

Reads: the live site (re-crawled), the page database, and every other
       agent's stores.
Produces: a real technical-health scorecard, a real action queue (Loop B —
          each finding routed to the agent that owns the fix), and the
          numbers the dashboard's pipeline/QA/audit panels show.

Search Console, CrUX, PageSpeed Insights, a backlink API and analytics are
not connected (no credentials yet — see the audit's "information needed to
proceed"), so this agent does not fabricate numbers for those. Everything
it *does* produce — crawl health, orphans, duplicates, schema, QA pass
rate, the action queue — comes from real crawl data and the other agents'
real output.
"""
from __future__ import annotations

from . import config, stores

AGENT = config.AGENT_BY_ID["monitoring"]

# Colours reused from the dashboard's own palette so the patched-in data
# renders with the same semantics (green=good, orange=warn, red=bad, grey=neutral).
GREEN, AMBER, RED, GREY = "#17AA2E", "#E87E22", "#D93A3A", "#949395"

# Term -> owning agent, matching the dashboard's own OWN/AGENT_OF mapping
# (docs/Optimization Guide (2).html), so Loop B routes findings exactly the
# way the Guide/Tracker tabs already describe.
ROUTE = {
    "orphan_pages": "Technical SEO QA",
    "duplicate_content": "Technical SEO QA",
    "js_only": "Technical SEO QA",
    "schema_errors": "Page Generation",
    "image_alt": "Page Generation",
    "meta_keywords": "Page Generation",
    "content_gap": "SEO Research",
    "redirect_or_broken": "Publishing & Indexing",
    "noindex": "Publishing & Indexing",
    "http_error": "Publishing & Indexing",
}


def technical_health() -> list[dict]:
    page_db = stores.load("page_db", {})
    qa_reports = stores.load("qa_reports", {})

    pages = list(page_db.values())
    ok_200 = sum(1 for r in pages if r.get("current", {}).get("status") == 200)
    errors_4xx = sum(1 for r in pages if (r.get("current", {}).get("status") or 0) in range(400, 500))
    errors_5xx = sum(1 for r in pages if (r.get("current", {}).get("status") or 0) >= 500)
    orphan_fails = sum(1 for r in qa_reports.values() for c in r.get("checks", []) if c["name"] == "Orphan page" and c["status"] == "fail")
    dup_fails = sum(1 for r in qa_reports.values() for c in r.get("checks", []) if c["name"].startswith("Duplicate content") and c["status"] == "fail")
    schema_fails = sum(1 for r in qa_reports.values() for c in r.get("checks", []) if c["name"] == "Schema markup valid" and c["status"] == "fail")
    js_only = sum(1 for r in qa_reports.values() for c in r.get("checks", []) if "JS-only" in c["name"] and c["status"] == "fail")
    meta_kw = sum(1 for r in page_db.values() if r.get("current", {}).get("has_meta_keywords"))
    noindex_fails = sum(1 for r in qa_reports.values() for c in r.get("checks", []) if "noindex" in c["name"].lower() and c["status"] == "fail")
    missing_alt_total = sum(
        len([i for i in r.get("current", {}).get("images", []) if not (i.get("alt") or "").strip()])
        for r in page_db.values()
    )
    schema_present = sum(1 for r in page_db.values() if r.get("current", {}).get("json_ld"))

    def row(k, v, c):
        return {"k": k, "v": str(v), "c": c}

    return [
        row("200 OK pages", ok_200, GREEN if ok_200 == len(pages) else AMBER),
        row("4xx errors", errors_4xx, GREEN if errors_4xx == 0 else RED),
        row("5xx server errors", errors_5xx, GREEN if errors_5xx == 0 else RED),
        row("Orphan pages (zero inbound links)", orphan_fails, GREEN if orphan_fails == 0 else RED),
        row("Duplicate-content pairs (>85% overlap)", dup_fails, GREEN if dup_fails == 0 else AMBER),
        row("Pages with no server-rendered schema", len(pages) - schema_present, GREEN if schema_present == len(pages) else AMBER),
        row("Schema blocks failing validation (proposed)", schema_fails, GREEN if schema_fails == 0 else RED),
        row("Pages with <50 words server HTML (JS-only risk)", js_only, GREEN if js_only == 0 else AMBER),
        row("Pages still carrying meta-keywords tag", meta_kw, GREEN if meta_kw == 0 else GREY),
        row("Images missing alt text (current HTML)", missing_alt_total, GREEN if missing_alt_total == 0 else AMBER),
        row("Accidentally noindexed pages", noindex_fails, GREEN if noindex_fails == 0 else RED),
        row("Pages crawled this run", len(pages), GREEN),
    ]


def build_action_queue() -> list[dict]:
    """Loop B: every real finding becomes one row, routed to the agent that owns the fix."""
    page_db = stores.load("page_db", {})
    qa_reports = stores.load("qa_reports", {})
    results = stores.load("results_store", {})
    stores.clear_action_queue()
    actions: list[dict] = []

    def add(sev, color, title, page, owner, term):
        a = {"s": sev, "c": color, "t": title, "p": page, "o": owner, "term": term}
        actions.append(a)
        stores.push_action(a)

    orphans = [u for u, r in qa_reports.items() for c in r.get("checks", [])
               if c["name"] == "Orphan page" and c["status"] == "fail"]
    if orphans:
        add("High", RED, f"{len(orphans)} page(s) have zero inbound internal links in server HTML",
            ", ".join(orphans[:3]) + (f" + {len(orphans)-3} more" if len(orphans) > 3 else ""),
            ROUTE["orphan_pages"], "Orphan Pages")

    missing_schema = [u for u, r in page_db.items() if not r.get("current", {}).get("json_ld")]
    if missing_schema:
        add("High", RED, f"{len(missing_schema)} page(s) have no server-rendered schema markup",
            ", ".join(missing_schema[:3]) + (f" + {len(missing_schema)-3} more" if len(missing_schema) > 3 else ""),
            ROUTE["schema_errors"], "Schema Markup")

    alt_total = sum(len([i for i in r.get("current", {}).get("images", []) if not (i.get("alt") or "").strip()])
                     for r in page_db.values())
    if alt_total:
        add("Medium", AMBER, f"{alt_total} image(s) across the site are missing alt text",
            "site-wide", ROUTE["image_alt"], "Image Alt Text")

    dup_pairs = [u for u, r in qa_reports.items() for c in r.get("checks", [])
                 if c["name"].startswith("Duplicate content") and c["status"] == "fail"]
    if dup_pairs:
        add("Medium", AMBER, f"{len(dup_pairs)} page(s) have titles/descriptions overlapping another page by 85%+",
            ", ".join(dup_pairs[:3]), ROUTE["duplicate_content"], "Duplicate Content")

    for gap in results.get("content_gaps", [])[:3]:
        add("Low", GREY, f"No {gap['type']} page exists yet — e.g. \"{gap['example']}\"",
            "site-wide", ROUTE["content_gap"], "Content Gap Analysis")

    meta_kw_pages = [u for u, r in page_db.items() if r.get("current", {}).get("has_meta_keywords")]
    if meta_kw_pages:
        add("Low", GREY, f"{len(meta_kw_pages)} page(s) still carry the obsolete meta-keywords tag",
            ", ".join(meta_kw_pages[:3]), ROUTE["meta_keywords"], "Meta keywords")

    http_errors = [u for u, r in page_db.items() if (r.get("current", {}).get("status") or 0) >= 400]
    if http_errors:
        add("High", RED, f"{len(http_errors)} sitemap URL(s) do not return 200",
            ", ".join(http_errors[:3]), ROUTE["http_error"], "HTTP Status Codes")

    severity_rank = {"High": 0, "Medium": 1, "Low": 2}
    actions.sort(key=lambda a: severity_rank.get(a["s"], 3))
    return actions


def qa_fail_breakdown() -> list[dict]:
    qa_reports = stores.load("qa_reports", {})
    counts: dict[str, int] = {}
    for r in qa_reports.values():
        for c in r.get("checks", []):
            if c["status"] == "fail":
                counts[c["name"]] = counts.get(c["name"], 0) + 1
    rows = [{"k": k, "n": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    return rows


def pipeline_stats(first_pass_qa: dict | None = None) -> dict:
    page_db = stores.load("page_db", {})
    qa_reports = stores.load("qa_reports", {})
    publish_log = stores.load("publish_log", {})

    fp = first_pass_qa or {}
    fp_total = fp.get("total", len(qa_reports)) or 1
    fp_passed = fp.get("passed", sum(1 for r in qa_reports.values() if r.get("overall") == "pass"))
    fp_failed = fp_total - fp_passed
    first_pass_rate = round(fp_passed / fp_total * 100)

    return {
        "pages_published": len(publish_log),
        "pages_crawled": len(page_db),
        "qa_first_pass_rate": first_pass_rate,
        "qa_failed_first_pass": fp_failed,
        "qa_total_first_pass": fp_total,
    }


def run(first_pass_qa: dict | None = None) -> dict:
    tech = technical_health()
    actions = build_action_queue()
    qa_fails = qa_fail_breakdown()
    stats = pipeline_stats(first_pass_qa)

    results = stores.load("results_store", {})
    results["technical_health"] = tech
    results["qa_fail_breakdown"] = qa_fails
    results["pipeline_stats"] = stats
    results["action_queue"] = actions
    stores.save("results_store", results)

    stores.append_audit(
        "monitoring.agent",
        f"Re-checked crawl health ({len(tech)} metrics); built action queue with {len(actions)} open "
        f"item(s) routed to owning agents; QA first-pass rate {stats['qa_first_pass_rate']}%",
        "results_store.json",
    )

    return {"agent": AGENT, "actions": len(actions), "tech_rows": len(tech), "stats": stats}
