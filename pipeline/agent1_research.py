"""Agent 01 — SEO Research.

Reads: PRD/known facts (seeded from the audit doc), keyword themes, the live
       crawl of darwix.ai.
Produces: keyword clusters, search intent, a keyword-to-URL map, the sitemap
          tree position, a content-gap list, and one content brief per page.

The eight starting keyword themes and the recommended nav/footer structure
in docs/Darwix AI – Website SEO Audit & Remediation Plan.docx are used as
seed knowledge (KNOWN_THEMES / NAV_STRUCTURE below) — this agent's job is to
turn that into a per-page brief and to extend it to any page the audit
didn't already cover.
"""
from __future__ import annotations

from . import config, llm, stores
from .crawler import PageSnapshot

AGENT = config.AGENT_BY_ID["research"]

# Seed keyword themes (from the audit's section 8). Agent 1 trusts these
# over whatever an LLM would guess, and only falls back to inference for
# pages the audit never covered.
KNOWN_THEMES: dict[str, dict] = {
    "https://darwix.ai": {
        "primary": "AI platform for BFSI",
        "secondary": ["conversational intelligence for banks and NBFCs", "agentic AI for lending and insurance"],
    },
    "https://darwix.ai/sherpa": {
        "primary": "wearable AI for field sales",
        "secondary": ["AI assistant for field officers", "real-time call coaching for BFSI"],
    },
    "https://darwix.ai/voiceAgent": {
        "primary": "AI voice agent for BFSI",
        "secondary": ["AI outbound calling in Hindi", "collections voice bot", "AI dialer for lenders"],
    },
    "https://darwix.ai/voiceAgent/library": {
        "primary": "voice agent sample library",
        "secondary": ["BFSI call demos", "AI voice sample transcripts"],
    },
    "https://darwix.ai/nonvoice-agent": {
        "primary": "BFSI workflow automation",
        "secondary": ["LOS and CRM automation", "AI agents for loan operations"],
    },
    "https://darwix.ai/nova": {
        "primary": "in-store AI assistant",
        "secondary": ["AI kiosk for retail and BFSI branches"],
    },
    "https://darwix.ai/compliance": {
        "primary": "RBI-compliant AI",
        "secondary": ["DPDP-compliant voice AI", "IRDAI MITC recording", "TRAI DLT compliance"],
    },
    "https://darwix.ai/pivc": {
        "primary": "video PIVC for insurance",
        "secondary": ["pre-issuance verification call", "mis-selling prevention"],
    },
    "https://darwix.ai/telemedi-assist": {
        "primary": "tele-medical examination AI",
        "secondary": ["insurance medical underwriting assistant"],
    },
    "https://darwix.ai/life-insurance-proposal-automation": {
        "primary": "life insurance proposal automation",
        "secondary": ["voice-driven proposal capture"],
    },
    "https://darwix.ai/auto-pd": {
        "primary": "automated personal discussion",
        "secondary": ["housing finance PD report automation"],
    },
    "https://darwix.ai/evidence-intelligence": {
        "primary": "document discrepancy detection",
        "secondary": ["KYC and claims document reconciliation"],
    },
    "https://darwix.ai/retail-asset-evidence-intelligence": {
        "primary": "asset verification for ARCs",
        "secondary": ["EARC due diligence"],
    },
    "https://darwix.ai/pre-meet-intelligence": {
        "primary": "client briefs for wealth managers",
        "secondary": ["RM meeting preparation AI"],
    },
    "https://darwix.ai/alert-intelligence": {
        "primary": "proactive client alerts",
        "secondary": ["wealth management risk alerts"],
    },
    "https://darwix.ai/about": {"primary": "about Darwix AI", "secondary": ["Darwix AI leadership", "Darwix AI company"]},
}

# Recommended nav/footer structure from the audit (section 7), used to build
# the sitemap tree store so breadcrumbs and internal-link rules are derived
# from one source of truth rather than invented per page.
NAV_STRUCTURE: dict[str, str] = {
    "https://darwix.ai": "Home",
    "https://darwix.ai/sherpa": "Products",
    "https://darwix.ai/voiceAgent": "Products",
    "https://darwix.ai/voiceAgent/library": "Products / Voice Automation",
    "https://darwix.ai/nonvoice-agent": "Products",
    "https://darwix.ai/nova": "Products",
    "https://darwix.ai/pivc": "Solutions / Insurance",
    "https://darwix.ai/telemedi-assist": "Solutions / Insurance",
    "https://darwix.ai/life-insurance-proposal-automation": "Solutions / Insurance",
    "https://darwix.ai/auto-pd": "Solutions / Lending",
    "https://darwix.ai/evidence-intelligence": "Solutions / Lending",
    "https://darwix.ai/retail-asset-evidence-intelligence": "Solutions / Lending",
    "https://darwix.ai/pre-meet-intelligence": "Solutions / Wealth management",
    "https://darwix.ai/alert-intelligence": "Solutions / Wealth management",
    "https://darwix.ai/compliance": "Compliance",
    "https://darwix.ai/about": "About",
    "https://darwix.ai/privacy-policy": "Legal",
    "https://darwix.ai/terms-and-conditions": "Legal",
}

# Related-solution links each solution page should carry (the audit's
# "each solution page should link to its parent product and two related
# solutions" rule), used by Agent 2 when generating internal links.
RELATED_SOLUTIONS: dict[str, list[str]] = {
    "https://darwix.ai/pivc": ["https://darwix.ai/telemedi-assist", "https://darwix.ai/life-insurance-proposal-automation"],
    "https://darwix.ai/telemedi-assist": ["https://darwix.ai/pivc", "https://darwix.ai/life-insurance-proposal-automation"],
    "https://darwix.ai/life-insurance-proposal-automation": ["https://darwix.ai/pivc", "https://darwix.ai/telemedi-assist"],
    "https://darwix.ai/auto-pd": ["https://darwix.ai/evidence-intelligence", "https://darwix.ai/retail-asset-evidence-intelligence"],
    "https://darwix.ai/evidence-intelligence": ["https://darwix.ai/auto-pd", "https://darwix.ai/retail-asset-evidence-intelligence"],
    "https://darwix.ai/retail-asset-evidence-intelligence": ["https://darwix.ai/auto-pd", "https://darwix.ai/evidence-intelligence"],
    "https://darwix.ai/pre-meet-intelligence": ["https://darwix.ai/alert-intelligence"],
    "https://darwix.ai/alert-intelligence": ["https://darwix.ai/pre-meet-intelligence"],
}

PARENT_PRODUCT: dict[str, str] = {
    "https://darwix.ai/pivc": "https://darwix.ai/voiceAgent",
    "https://darwix.ai/telemedi-assist": "https://darwix.ai/voiceAgent",
    "https://darwix.ai/life-insurance-proposal-automation": "https://darwix.ai/voiceAgent",
    "https://darwix.ai/auto-pd": "https://darwix.ai/nonvoice-agent",
    "https://darwix.ai/evidence-intelligence": "https://darwix.ai/nonvoice-agent",
    "https://darwix.ai/retail-asset-evidence-intelligence": "https://darwix.ai/nonvoice-agent",
    "https://darwix.ai/pre-meet-intelligence": "https://darwix.ai/nonvoice-agent",
    "https://darwix.ai/alert-intelligence": "https://darwix.ai/nonvoice-agent",
}

# Content gap analysis (H6 in the audit) — the site-wide list of missing
# page types, re-checked each run against the live crawl.
CONTENT_GAP_CANDIDATES = [
    {"type": "blog", "example": "A plain-language guide to RBI-compliant AI voice agents for lenders"},
    {"type": "blog", "example": "The DPDP Act and call recording: what BFSI teams must capture and mask"},
    {"type": "blog", "example": "How AI captures IRDAI MITC verbal disclosures"},
    {"type": "comparison", "example": "AI voice agents compared with human agents for early-bucket collections"},
    {"type": "glossary", "example": "PIVC, MITC, PD, LTV, NPA, FOIR, LAP, NACH, DLT"},
    {"type": "case-study", "example": "Case studies built from the customer logos already shown"},
    {"type": "faq", "example": "Visible FAQs per product/solution page"},
    {"type": "contact", "example": "/contact page"},
    {"type": "catalogue", "example": "Agent catalogue listing all 42 non-voice agents"},
]

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_keyword": {"type": "string"},
        "secondary_keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
        "search_intent": {"type": "string", "enum": ["informational", "commercial", "transactional", "navigational"]},
        "outline_h2s": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
        "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
        "proof_points_needed": {"type": "array", "items": {"type": "string"}},
        "notes_for_generation": {"type": "string"},
    },
    "required": ["primary_keyword", "secondary_keywords", "search_intent", "outline_h2s",
                 "key_points", "proof_points_needed", "notes_for_generation"],
    "additionalProperties": False,
}


def _stub_brief(url: str, theme: dict) -> dict:
    return {
        "primary_keyword": theme["primary"],
        "secondary_keywords": theme["secondary"],
        "search_intent": "commercial",
        "outline_h2s": ["What it does", "How it works", "Why it matters for BFSI", "Proof and case studies"],
        "key_points": [f"Covers {theme['primary']}"] + theme["secondary"],
        "proof_points_needed": ["a sourced figure or customer result"],
        "notes_for_generation": "Stub brief — set OPENAI_API_KEY and re-run for an LLM-written brief.",
    }


def _theme_for(url: str, snap: PageSnapshot) -> dict:
    if url in KNOWN_THEMES:
        return KNOWN_THEMES[url]
    # Unknown page (new product/solution not in the audit): derive a rough
    # theme from the current title so the pipeline still produces a brief.
    base = (snap.title or url.rstrip("/").rsplit("/", 1)[-1]).split("|")[0].strip()
    return {"primary": base.lower() or url, "secondary": []}


def build_brief(url: str, snap: PageSnapshot, theme: dict) -> tuple[dict, bool]:
    system = (
        "You are the SEO Research agent in Darwix AI's automated SEO pipeline. "
        "Darwix AI is a BFSI (banking, financial services, insurance) agentic AI platform. "
        "Write a content brief for one page that a Page Generation agent will turn into copy. "
        "Keep the primary keyword as given — do not change it. Ground everything in BFSI use "
        "cases (lenders, insurers, wealth managers, banks, NBFCs). Be concrete, not generic."
    )
    user = (
        f"URL: {url}\n"
        f"Primary keyword (fixed): {theme['primary']}\n"
        f"Secondary keyword seeds: {theme.get('secondary', [])}\n"
        f"Current page title: {snap.title}\n"
        f"Current H1(s): {snap.h1s}\n"
        f"Current word count: {snap.word_count}\n"
        "Write the content brief now."
    )
    return llm.json_call(system, user, BRIEF_SCHEMA, stub_factory=lambda: _stub_brief(url, theme))


def content_gap_analysis(crawled_urls: set[str]) -> list[dict]:
    """Site-wide check: which of the known content gaps still have no live page."""
    gaps = []
    for cand in CONTENT_GAP_CANDIDATES:
        # None of these page types exist yet on darwix.ai (confirmed by the
        # audit and by the sitemap crawl having no /blog, /glossary, /contact, etc.)
        has_type = any(cand["type"] in u for u in crawled_urls)
        if not has_type:
            gaps.append(cand)
    return gaps


def run(snapshots: dict[str, PageSnapshot]) -> dict:
    stores.init_entity_list()
    keyword_map = stores.load("keyword_map", {})
    sitemap_tree = stores.load("sitemap_tree", {})
    live_count = 0
    briefs_written = 0

    for url, snap in snapshots.items():
        theme = _theme_for(url, snap)
        brief, was_live = build_brief(url, snap, theme)
        live_count += int(was_live)
        briefs_written += 1

        keyword_map[url] = {
            "primary": theme["primary"],
            "secondary": theme.get("secondary", []),
        }
        sitemap_tree[url] = {
            "parent": NAV_STRUCTURE.get(url, "Unclassified — orphaned from nav" if url not in NAV_STRUCTURE else "Unknown"),
            "related_solutions": RELATED_SOLUTIONS.get(url, []),
            "parent_product": PARENT_PRODUCT.get(url),
        }

        stores.update_page(url, {
            "current": snap.to_dict(),
            "brief": brief,
            "keyword": keyword_map[url],
            "sitemap": sitemap_tree[url],
            "status": "research_done",
        })

    stores.save("keyword_map", keyword_map)
    stores.save("sitemap_tree", sitemap_tree)

    gaps = content_gap_analysis(set(snapshots.keys()))
    results = stores.load("results_store", {})
    results["content_gaps"] = gaps
    results["pages_researched"] = list(snapshots.keys())
    stores.save("results_store", results)

    stores.append_audit(
        "research.agent",
        f"Built keyword clusters and content briefs for {briefs_written} pages "
        f"({'LLM-written' if live_count else 'stub — no API key'}); "
        f"found {len(gaps)} content-type gaps",
        f"keyword_map.json, sitemap_tree.json · {live_count}/{briefs_written} live",
    )

    return {
        "agent": AGENT,
        "pages": briefs_written,
        "llm_live": live_count,
        "content_gaps": gaps,
    }
