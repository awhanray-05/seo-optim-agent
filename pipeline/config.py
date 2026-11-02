"""Shared configuration and constants for the pipeline."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load ROOT/.env (if present) into the environment before reading any key
# below, so OPENAI_API_KEY / GROQ_API_KEY only need to be set once in a
# file instead of exported in every shell session. Never overwrites a
# variable the shell already set. Safe no-op if .env doesn't exist.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
DATA_DIR = ROOT / "data"
DASHBOARD_DIR = ROOT / "dashboard"
DOCS_DIR = ROOT / "docs"

SITE_ROOT = "https://darwix.ai"
SITEMAP_URL = f"{SITE_ROOT}/sitemap.xml"
ROBOTS_URL = f"{SITE_ROOT}/robots.txt"

USER_AGENT = "DarwixSEOAutomationBot/1.0 (+https://darwix.ai; SEO audit pipeline)"
REQUEST_TIMEOUT = 20

# The single contact address the audit (M4) says the site should standardise
# on, used anywhere the pipeline needs to name one.
PRIMARY_CONTACT_EMAIL = "transform@darwix.ai"

# Entity list (shared data store): canonical names so every agent/page uses
# the same wording. Agent 1 can extend this; Agent 2 must never invent a new
# spelling for something already listed here.
ENTITY_LIST = {
    "brand": "Darwix AI",
    "legal_entity": "CUR8 Ventures",
    "products": {
        "Sherpa": "Wearable field AI for in-person sales and service calls.",
        "Voice Automation": "AI voice agents for BFSI outbound and inbound calling.",
        "Non-Voice Agents": "Workflow automation agents across LOS/CRM systems.",
        "Nova": "In-store AI assistant/kiosk.",
    },
    "solutions": {
        "Video PIVC": "/pivc",
        "Tele-Medi Assist": "/telemedi-assist",
        "Life Insurance Proposal Automation": "/life-insurance-proposal-automation",
        "Auto PD": "/auto-pd",
        "Evidence Intelligence": "/evidence-intelligence",
        "Retail Asset Evidence Intelligence": "/retail-asset-evidence-intelligence",
        "Pre-Meet Intelligence": "/pre-meet-intelligence",
        "Alert Intelligence": "/alert-intelligence",
    },
}

# The 5 agents and what each one owns — mirrors the dashboard's AGENTS/OWN
# tables so the pipeline and the dashboard never drift apart.
AGENTS = [
    {"id": "research", "n": "01", "t": "SEO Research",
     "d": "Finds keywords, clusters them, sets intent, writes the content brief."},
    {"id": "generation", "n": "02", "t": "Page Generation",
     "d": "Turns the brief and known facts into copy, metadata, schema and alt text."},
    {"id": "qa", "n": "03", "t": "Technical SEO QA",
     "d": "Checks headings, canonicals, schema, links, duplicates and facts before launch."},
    {"id": "publishing", "n": "04", "t": "Publishing & Indexing",
     "d": "Stages approved pages, updates the sitemap, prepares the submission and inspection step."},
    {"id": "monitoring", "n": "05", "t": "SEO Monitoring",
     "d": "Tracks crawl health, Core Web Vitals proxies, backlinks and conversions over time."},
]

AGENT_BY_ID = {a["id"]: a for a in AGENTS}

# OpenAI model to use for every LLM-backed step in the pipeline. Override
# with OPENAI_MODEL in .env (e.g. "gpt-5.1-mini" for a cheaper/faster run).
LLM_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Groq — a free-tier fallback LLM provider for testing when no OpenAI key
# is configured. Used only if OPENAI_API_KEY is unset; see pipeline/llm.py.
# Get a free key at https://console.groq.com/keys (no credit card required).
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# GitHub CMS publishing (Agent 4) — pushes the staged publish package as a
# PR instead of just writing it to data/publish_package/. See
# pipeline/github_publisher.py. Unset -> agent4 stays in local-staging-only
# mode, same as before this was wired up.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")  # "owner/repo"
GITHUB_DEFAULT_BRANCH = os.environ.get("GITHUB_DEFAULT_BRANCH", "main")
GITHUB_CONTENT_PREFIX = os.environ.get("GITHUB_CONTENT_PREFIX", "seo-automation/publish_package")

# Google services (dashboard's Search Console / PageSpeed / CrUX / GA4
# panels + real sitemap submission). See pipeline/google_services.py. Any
# of these left unset -> that specific panel/feature stays on sample data,
# same degrade-gracefully pattern as the LLM provider above.
GOOGLE_PAGESPEED_API_KEY = os.environ.get("GOOGLE_PAGESPEED_API_KEY")
GOOGLE_CRUX_API_KEY = os.environ.get("GOOGLE_CRUX_API_KEY")
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GSC_SITE_URL = os.environ.get("GSC_SITE_URL")  # e.g. "sc-domain:darwix.ai"
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID")

# GA4 custom event names used for the conversion funnel — darwix.ai's own
# GA4 property must already be tracking events with these names (Admin >
# Events) for the funnel panel to carry real numbers; override here if the
# site uses different event names.
GA4_EVENT_CTA_CLICK = os.environ.get("GA4_EVENT_CTA_CLICK", "cta_click")
GA4_EVENT_FORM_START = os.environ.get("GA4_EVENT_FORM_START", "form_start")
GA4_EVENT_DEMO_REQUEST = os.environ.get("GA4_EVENT_DEMO_REQUEST", "demo_request")


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
