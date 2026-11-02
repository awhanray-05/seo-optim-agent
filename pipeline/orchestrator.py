"""Runs the five agents in order, the way n8n would: 01 -> 02 -> 03 -> 04 -> 05,
with the two feedback loops from the architecture diagram:

  Loop A (QA -> Generation): a failed check goes back with the exact field
  to fix. Only that field is regenerated, then the page goes through QA
  again before publishing — capped at one retry so a page that keeps
  failing surfaces to a human instead of looping forever.

  Loop B (Monitoring -> Research / Generation / Publishing): handled inside
  agent5_monitoring.build_action_queue(), which routes each real finding to
  the agent that owns it. This run doesn't auto-apply those actions — they
  land in the action queue for the next run / human review, same as the
  dashboard's "Open issues routed to the owning agent" panel describes.
"""
from __future__ import annotations

import time

from . import agent1_research, agent2_generation, agent3_qa, agent4_publishing, agent5_monitoring
from . import config, crawler, stores


def run_pipeline(urls: list[str] | None = None, *, verbose: bool = True) -> dict:
    t0 = time.monotonic()
    config.ensure_data_dir()

    def log(msg: str):
        if verbose:
            print(f"[orchestrator] {msg}")

    log("Crawling darwix.ai ...")
    snapshots = crawler.crawl_site(urls)
    log(f"Crawled {len(snapshots)} URL(s).")

    log("Agent 01 — SEO Research")
    r1 = agent1_research.run(snapshots)
    log(f"  briefs={r1['pages']} llm_live={r1['llm_live']} content_gaps={len(r1['content_gaps'])}")

    log("Agent 02 — Page Generation")
    r2 = agent2_generation.run()
    log(f"  pages={r2['pages']} llm_live={r2['llm_live']}")

    log("Agent 03 — Technical SEO QA (first pass)")
    r3 = agent3_qa.run()
    first_pass_stats = {"total": r3["total"], "passed": r3["passed"]}
    log(f"  total={r3['total']} passed={r3['passed']} failed={r3['failed']}")

    # Loop A: regenerate only the failed fields, once, then re-QA just those pages.
    # A failure with no named field (an orphan page, a structural duplicate)
    # has nothing for Page Generation to fix — skip it instead of spending an
    # LLM call on a regeneration whose output gets merged into nothing.
    loop_a_result = None
    fixable = {u: fields for u, fields in r3["failed_urls"].items() if fields}
    skipped = len(r3["failed_urls"]) - len(fixable)
    if fixable:
        log(f"Loop A — regenerating {len(fixable)} page(s) for their failed field(s) only"
            + (f" ({skipped} structural failure(s) skipped — no fixable field)" if skipped else ""))
        agent2_generation.run(fix_only=fixable)
        r3b = agent3_qa.run(urls=list(fixable.keys()))
        loop_a_result = r3b
        log(f"  after retry: passed={r3b['passed']} still_failed={r3b['failed']}")
    elif skipped:
        log(f"Loop A — {skipped} page(s) failed with no fixable field (structural); skipping regeneration")

    log("Agent 04 — Publishing & Indexing (staged)")
    r4 = agent4_publishing.run()
    log(f"  staged={r4['staged']} sitemap_urls={r4['sitemap_urls']} redirects={r4['redirects']}")

    log("Agent 05 — SEO Monitoring")
    r5 = agent5_monitoring.run(first_pass_qa=first_pass_stats)
    log(f"  action_queue={r5['actions']} qa_first_pass_rate={r5['stats']['qa_first_pass_rate']}%")

    elapsed = round(time.monotonic() - t0, 1)
    stores.append_audit("orchestrator", f"Full pipeline run completed in {elapsed}s", f"{len(snapshots)} pages")
    log(f"Done in {elapsed}s.")

    return {
        "elapsed_s": elapsed,
        "research": r1,
        "generation": r2,
        "qa_first_pass": r3,
        "loop_a": loop_a_result,
        "publishing": r4,
        "monitoring": r5,
    }


if __name__ == "__main__":
    run_pipeline()
