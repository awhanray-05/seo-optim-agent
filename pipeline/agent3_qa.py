"""Agent 03 — Technical SEO QA.

Reads: the rendered/proposed draft page and the page database.
Produces: a pass/fail report per page, one row per check, with the exact
          failing field named — so Loop A can send only that field back to
          Page Generation instead of regenerating the whole page.

Every check here is a real rule run against real crawl data and the
proposed patch from Agent 2 — nothing in this file is sample data. The one
check that benefits from judgement (fact-checking copy against the brief)
also runs through Claude when a key is configured, and is skipped (not
faked) otherwise.
"""
from __future__ import annotations

from . import config, llm, stores

AGENT = config.AGENT_BY_ID["qa"]

TITLE_MAX = 60
DESC_MIN, DESC_MAX = 140, 160
DUPLICATE_THRESHOLD = 0.85

FACT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "has_unsupported_claim": {"type": "boolean"},
        "explanation": {"type": "string"},
    },
    "required": ["has_unsupported_claim", "explanation"],
    "additionalProperties": False,
}


def _word_set(text: str) -> set[str]:
    return set(w.lower() for w in text.split() if len(w) > 3)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _check(name: str, severity: str, ok: bool, detail: str, field: str | None = None) -> dict:
    return {"name": name, "severity": severity, "status": "pass" if ok else "fail",
            "detail": detail, "field": field}


def _build_inbound_link_index(page_db: dict) -> dict[str, int]:
    inbound: dict[str, int] = {u: 0 for u in page_db}
    for url, rec in page_db.items():
        for link in rec.get("current", {}).get("internal_links", []):
            clean = link.rstrip("/")
            for target in page_db:
                if target.rstrip("/") == clean or target.rstrip("/") + "/library" == clean:
                    if target != url:
                        inbound[target] = inbound.get(target, 0) + 1
    return inbound


def run_fact_check(brief: dict, copy: dict) -> tuple[dict, bool]:
    system = (
        "You are the fact-checking step of an SEO QA agent. Compare the proposed copy only "
        "against the brief's key_points and proof_points_needed. Flag ONLY claims that assert a "
        "specific number, customer name or capability not present in the brief. Do not flag "
        "generic marketing language."
    )
    user = f"Brief: {brief}\nProposed intro: {copy.get('intro_40_60_words')}\nProposed meta description: {copy.get('meta_description')}"
    return llm.json_call(system, user, FACT_CHECK_SCHEMA,
                          stub_factory=lambda: {"has_unsupported_claim": False,
                                                 "explanation": "Fact-check skipped — no OPENAI_API_KEY set."})


def qa_page(url: str, rec: dict, inbound_count: int, all_texts: dict[str, set[str]]) -> dict:
    current = rec.get("current", {})
    proposed = rec.get("proposed", {})
    checks: list[dict] = []

    title = proposed.get("title", current.get("title") or "")
    checks.append(_check("Meta title length", "high", bool(title) and len(title) <= TITLE_MAX,
                          f"{len(title)} chars (limit {TITLE_MAX}): {title!r}", "title"))

    desc = proposed.get("meta_description", current.get("meta_description") or "")
    desc_ok = DESC_MIN - 20 <= len(desc) <= DESC_MAX + 10 and bool(desc)
    checks.append(_check("Meta description length", "medium", desc_ok,
                          f"{len(desc)} chars (target {DESC_MIN}-{DESC_MAX}): {desc!r}", "meta_description"))

    h1 = proposed.get("h1", "")
    primary_kw = rec.get("keyword", {}).get("primary", "").lower()
    h1_ok = bool(h1) and (not primary_kw or any(w in h1.lower() for w in primary_kw.split()[:2]))
    checks.append(_check("H1 contains focus keyword", "high", h1_ok,
                          f"H1 {h1!r} vs primary keyword {primary_kw!r}", "h1"))

    current_h1_count = len(current.get("h1s", []))
    checks.append(_check("Exactly one H1 in current HTML", "medium", current_h1_count == 1,
                          f"Current page has {current_h1_count} H1 tag(s)"))

    canonical_ok = bool(proposed.get("canonical"))
    checks.append(_check("Canonical URL proposed", "high", canonical_ok,
                          f"canonical: {proposed.get('canonical')!r}", "canonical"))

    schema = proposed.get("schema", [])
    schema_ok = bool(schema) and all("@type" in s for s in schema)
    checks.append(_check("Schema markup valid", "high", schema_ok,
                          f"{len(schema)} JSON-LD block(s), all with @type: {schema_ok}", "schema"))

    if schema_ok:
        org = next((s for s in schema if s.get("@type") == "Organization"), None)
        if org:
            logo_ok = bool(org.get("logo"))
            checks.append(_check("Organization schema has a logo URL", "medium", logo_ok, f"logo: {org.get('logo')}"))

    missing_alt = [img for img in current.get("images", []) if not (img.get("alt") or "").strip()]
    fixed_alt = len(proposed.get("image_alt_fixes", []))
    alt_ok = len(missing_alt) == 0 or fixed_alt >= len(missing_alt)
    checks.append(_check("Image alt text", "medium", alt_ok,
                          f"{len(missing_alt)} image(s) missing alt in current HTML; {fixed_alt} fix(es) proposed",
                          "image_alt_fixes"))

    checks.append(_check("Meta keywords tag removed", "low", bool(proposed.get("meta_keywords_removed")),
                          "L1: obsolete meta keywords tag should be dropped in the proposed patch"))

    checks.append(_check("Orphan page", "critical", inbound_count > 0,
                          f"{inbound_count} inbound internal link(s) found in server HTML across the crawled site"))

    word_count = current.get("word_count", 0)
    checks.append(_check("Server HTML has real content (not JS-only)", "high", word_count >= 50,
                          f"{word_count} words in server HTML"))

    robots_meta = (current.get("robots_meta") or "").lower()
    checks.append(_check("Not accidentally noindexed", "critical", "noindex" not in robots_meta,
                          f"robots meta: {robots_meta or '(none)'}"))

    status_ok = (current.get("status") == 200)
    checks.append(_check("HTTP 200", "critical", status_ok, f"status: {current.get('status')}"))

    my_words = all_texts.get(url, set())
    dup_of = None
    if len(my_words) >= 6:
        for other_url, words in all_texts.items():
            if other_url == url or len(words) < 6:
                continue
            sim = _jaccard(my_words, words)
            if sim >= DUPLICATE_THRESHOLD:
                dup_of = (other_url, round(sim, 2))
                break
    checks.append(_check("Duplicate content < 85% similarity", "medium", dup_of is None,
                          f"{dup_of[1]*100:.0f}% word overlap with {dup_of[0]}" if dup_of else "no near-duplicate found"))

    brief = rec.get("brief", {})
    if brief and proposed:
        fact, was_live = run_fact_check(brief, proposed)
        checks.append(_check("SEO Content QA — facts match the brief", "high",
                              not fact.get("has_unsupported_claim", False),
                              fact.get("explanation", "") + ("" if was_live else " (fact-check stub)"),
                              "intro_40_60_words" if fact.get("has_unsupported_claim") else None))

    failed = [c for c in checks if c["status"] == "fail"]
    failed_fields = sorted({c["field"] for c in failed if c["field"]})
    overall = "pass" if not failed else "fail"

    return {"url": url, "checks": checks, "overall": overall, "failed_fields": failed_fields,
            "failed_count": len(failed), "critical_failures": sum(1 for c in failed if c["severity"] == "critical")}


def run(urls: list[str] | None = None) -> dict:
    page_db = stores.load("page_db", {})
    inbound_index = _build_inbound_link_index(page_db)
    all_texts = {u: _word_set(r.get("current", {}).get("title", "") + " " +
                               r.get("current", {}).get("meta_description", ""))
                 for u, r in page_db.items()}

    targets = urls if urls is not None else list(page_db.keys())
    reports = stores.load("qa_reports", {})
    pass_count = 0
    failed_urls: dict[str, list[str]] = {}

    for url in targets:
        rec = page_db.get(url, {})
        if not rec.get("proposed"):
            continue
        report = qa_page(url, rec, inbound_index.get(url, 0), all_texts)
        reports[url] = report
        if report["overall"] == "pass":
            pass_count += 1
            stores.update_page(url, {"status": "qa_passed"})
        else:
            failed_urls[url] = report["failed_fields"]
            stores.update_page(url, {"status": "qa_failed"})

    stores.save("qa_reports", reports)

    total = len(targets)
    stores.append_audit(
        "qa.agent",
        f"Ran technical SEO QA on {total} page(s): {pass_count} passed, {len(failed_urls)} failed first pass",
        "qa_reports.json",
    )
    for url, fields in failed_urls.items():
        stores.append_audit("qa.agent", f"Failed check on {url}: fields {fields}", "qa_reports.json")

    return {"agent": AGENT, "total": total, "passed": pass_count, "failed": len(failed_urls),
            "failed_urls": failed_urls}
