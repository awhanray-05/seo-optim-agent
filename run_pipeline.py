#!/usr/bin/env python
"""CLI entry point for the Darwix AI SEO automation pipeline.

Usage:
    python run_pipeline.py                 # crawl every sitemap URL, run all 5 agents, patch the dashboard
    python run_pipeline.py --urls https://darwix.ai https://darwix.ai/sherpa
    python run_pipeline.py --no-dashboard   # run the agents but skip regenerating the dashboard HTML
    python run_pipeline.py --quiet
"""
from __future__ import annotations

import argparse
import sys

from pipeline import config, dashboard_patch, github_publisher, google_services, llm
from pipeline.orchestrator import run_pipeline


def main() -> None:
    # Real LLM output can contain characters Windows' default console
    # codepage (cp1252) can't represent (e.g. U+2011 non-breaking hyphen).
    # Without this, printing such a string crashes the whole run instead of
    # just showing a mangled character.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", nargs="*", default=None,
                         help="Specific URLs to crawl instead of the full sitemap.")
    parser.add_argument("--no-dashboard", action="store_true",
                         help="Skip regenerating dashboard/optimization-dashboard.html.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-agent progress lines.")
    args = parser.parse_args()

    if llm.is_live():
        print(f"LLM provider: {llm.provider_label()}\n")
    else:
        print("NOTE: no OPENAI_API_KEY or GROQ_API_KEY is set — every LLM-backed step (Research "
              "briefs, Page Generation copy, QA fact-checking) will run in stub mode. The pipeline "
              "will still crawl the real site and run every rule-based check for real. Set one of "
              "those keys and re-run for real agent output (a free Groq key works — see README.md).\n")

    print(f"GitHub publishing : {'configured — ' + config.GITHUB_REPO if github_publisher.is_configured() else 'not configured — Agent 4 stages locally only'}")
    gsvc = google_services.status()
    print(f"Google services    : {', '.join(k for k, v in gsvc.items() if v) or 'none configured — dashboard panels stay on sample data'}\n")

    result = run_pipeline(args.urls, verbose=not args.quiet)

    print("\n--- Summary ---")
    print(f"Pages crawled      : {result['research']['pages']}")
    print(f"QA first pass      : {result['qa_first_pass']['passed']}/{result['qa_first_pass']['total']} passed")
    print(f"Staged for publish : {result['publishing']['staged']}")
    gh = result["publishing"]["submission_result"].get("github")
    if gh and gh.get("ok") and gh.get("pr_url"):
        print(f"GitHub PR          : {gh['pr_url']}")
    elif gh and not gh.get("ok"):
        print(f"GitHub publish     : failed — {gh.get('error')}")
    print(f"Action queue       : {result['monitoring']['actions']} open item(s)")
    print(f"Elapsed            : {result['elapsed_s']}s")

    if not args.no_dashboard:
        d = dashboard_patch.build()
        print(f"\nDashboard updated  : {dashboard_patch.OUTPUT_HTML}")
        print(f"                     (as of {d['generated_at']})")


if __name__ == "__main__":
    main()
