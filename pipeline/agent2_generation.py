"""Agent 02 — Page Generation.

Reads: the content brief and entity list from Agent 1, the current crawled
       page, and (on a re-run triggered by Loop A) the exact QA failure to
       fix.
Produces: a proposed patch per page — title, meta description, H1, Open
          Graph / Twitter tags, canonical, JSON-LD schema, alt-text fixes
          for images missing one, and internal links to add — written to
          page_db[url]["proposed"]. Nothing here touches the live site;
          Agent 4 is the only one that stages anything for publishing.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from . import config, llm, stores
from .agent1_research import PARENT_PRODUCT, RELATED_SOLUTIONS

AGENT = config.AGENT_BY_ID["generation"]

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Under 60 characters, primary keyword first."},
        "meta_description": {"type": "string", "description": "140-160 characters."},
        "h1": {"type": "string", "description": "Contains the primary keyword; not a slogan alone."},
        "intro_40_60_words": {"type": "string", "description": "A direct, quotable 40-60 word answer (AEO style)."},
        "og_description": {"type": "string"},
        "cta_label": {"type": "string"},
        "link_anchor_text": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "href": {"type": "string"}, "anchor": {"type": "string"}
            }, "required": ["href", "anchor"], "additionalProperties": False},
        },
    },
    "required": ["title", "meta_description", "h1", "intro_40_60_words", "og_description",
                 "cta_label", "link_anchor_text"],
    "additionalProperties": False,
}


def _stub_meta_description(primary_kw: str) -> str:
    """Deterministic placeholder sized to land in the 140-160 char target
    regardless of keyword length, so Agent 3's length check can actually
    pass on stub output (a real Claude call controls length on its own)."""
    base = f"Darwix AI's {primary_kw} helps BFSI teams — banks, NBFCs, insurers and wealth managers — move faster, stay compliant and close more. See how it works and book a demo today."
    if len(base) <= 160:
        return base
    # Too long for this keyword: fall back to a shorter frame and pad to 140+.
    short = f"Darwix AI's {primary_kw} helps BFSI teams move faster and stay compliant."
    pad = " Built for banks, NBFCs, insurers and wealth managers. Book a demo to see it live."
    out = short
    while len(out) < 140 and pad:
        out += pad
        pad = ""
    return out[:160]


def _stub_copy(primary_kw: str, link_candidates: list[str]) -> dict:
    return {
        "title": f"{primary_kw.title()} | Darwix AI"[:59],
        "meta_description": _stub_meta_description(primary_kw),
        "h1": primary_kw.title(),
        "intro_40_60_words": (
            f"{primary_kw.title()} from Darwix AI gives BFSI teams a faster, compliant way to "
            "handle this workflow end to end, with every step recorded for audit and review. "
            "It replaces manual, inconsistent handling with a consistent AI-run process."
        ),
        "og_description": f"{primary_kw.title()} — Darwix AI for BFSI.",
        "cta_label": "Book a demo",
        "link_anchor_text": [{"href": h, "anchor": h.rsplit("/", 1)[-1].replace("-", " ")} for h in link_candidates[:3]],
    }


def _normalize_slug(url: str) -> str:
    """M2 fix: lowercase, hyphenated URL — the audit flags /voiceAgent as the
    camelCase outlier. This doesn't rename the live page; it's the canonical
    Agent 4 will propose a redirect map for."""
    p = urlparse(url)
    path = p.path
    if path in ("", "/"):
        return f"{p.scheme}://{p.netloc}/"
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", path).lower()
    slug = re.sub(r"-+", "-", slug)
    return f"{p.scheme}://{p.netloc}{slug}"


def _page_type(url: str) -> str:
    if url.rstrip("/") == "https://darwix.ai":
        return "home"
    if url in RELATED_SOLUTIONS or url in PARENT_PRODUCT:
        return "solution"
    if any(seg in url for seg in ("/sherpa", "/voiceAgent", "/nonvoice-agent", "/nova")):
        return "product"
    if "/about" in url:
        return "about"
    return "other"


def _breadcrumb_schema(url: str, title: str, sitemap_rec: dict) -> dict:
    parent_label = sitemap_rec.get("parent", "")
    items = [{"@type": "ListItem", "position": 1, "name": "Home", "item": "https://darwix.ai/"}]
    pos = 2
    for seg in [s for s in parent_label.split(" / ") if s and s != "Home"]:
        items.append({"@type": "ListItem", "position": pos, "name": seg})
        pos += 1
    items.append({"@type": "ListItem", "position": pos, "name": title, "item": url})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def _organization_schema(entities: dict) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": entities["brand"],
        "url": "https://darwix.ai/",
        "logo": "https://darwix.ai/logo.png",  # H1 fix: must resolve to 200 before publish — flagged to QA
        "sameAs": ["https://www.linkedin.com/company/darwix-ai"],
        "description": "Darwix AI is an agentic AI platform for BFSI sales and operations.",
    }


def _software_application_schema(url: str, title: str, description: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": title,
        "url": url,
        "description": description,
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
    }


def _website_schema() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Darwix AI",
        "url": "https://darwix.ai/",
    }


def build_schema(url: str, title: str, description: str, sitemap_rec: dict, entities: dict) -> list[dict]:
    schema = [_organization_schema(entities), _breadcrumb_schema(url, title, sitemap_rec)]
    ptype = _page_type(url)
    if ptype == "home":
        schema.append(_website_schema())
    elif ptype in ("product", "solution"):
        schema.append(_software_application_schema(url, title, description))
    return schema


def image_alt_fixes(images: list[dict], primary_kw: str) -> list[dict]:
    fixes = []
    for img in images:
        alt = (img.get("alt") or "").strip()
        src = img.get("src", "")
        if not src:
            continue
        if alt == "" or alt.lower() in ("image", "logo", "poonawalla"):
            # Can't see the actual pixels — name it from the filename plus
            # page context, and flag it for a human to confirm in review.
            base = src.rsplit("/", 1)[-1].split("?")[0]
            base = re.sub(r"\.(png|jpe?g|webp|svg)$", "", base, flags=re.I)
            guess = base.replace("-", " ").replace("_", " ").strip() or primary_kw
            fixes.append({"src": src, "old_alt": img.get("alt"), "new_alt": f"{guess} — {primary_kw}".strip(" —"),
                          "needs_human_confirmation": True})
    return fixes


def link_candidates_for(url: str, sitemap_rec: dict) -> list[str]:
    cands = list(sitemap_rec.get("related_solutions", []))
    if sitemap_rec.get("parent_product"):
        cands.append(sitemap_rec["parent_product"])
    cands.append("https://darwix.ai/")
    cands.append("https://darwix.ai/compliance")
    return cands


def run(fix_only: dict[str, list[str]] | None = None) -> dict:
    """fix_only: {url: [failed_field, ...]} from Loop A — when given, only
    those fields are regenerated for those URLs (the exact Loop A promise:
    'only that part is regenerated')."""
    page_db = stores.load("page_db", {})
    entities = stores.load("entity_list", config.ENTITY_LIST)
    live_count = 0
    generated = 0

    targets = fix_only.keys() if fix_only else page_db.keys()
    for url in targets:
        rec = page_db.get(url, {})
        brief = rec.get("brief", {})
        current = rec.get("current", {})
        sitemap_rec = rec.get("sitemap", {})
        if not brief:
            continue

        link_cands = link_candidates_for(url, sitemap_rec)
        primary_kw = brief.get("primary_keyword", url)

        system = (
            "You are the Page Generation agent in Darwix AI's automated SEO pipeline. "
            "Write page metadata and intro copy strictly from the given content brief. "
            "Never invent a statistic, customer name or figure that isn't in the brief. "
            "Only link to the href values given as candidates — do not invent URLs."
        )
        user = (
            f"URL: {url}\n"
            f"Brief: {brief}\n"
            f"Link candidates (href only, pick up to 3): {link_cands}\n"
            "Write the page copy fields now."
        )
        copy, was_live = llm.json_call(
            system, user, COPY_SCHEMA,
            stub_factory=lambda: _stub_copy(primary_kw, link_cands),
        )
        live_count += int(was_live)
        generated += 1

        canonical = _normalize_slug(url)
        schema = build_schema(url, copy.get("title", primary_kw), copy.get("meta_description", ""), sitemap_rec, entities)
        alt_fixes = image_alt_fixes(current.get("images", []), primary_kw)

        proposed = rec.get("proposed", {})
        if fix_only and url in fix_only:
            # Loop A: merge in only the regenerated fields.
            for field in fix_only[url]:
                if field in copy:
                    proposed[field] = copy[field]
        else:
            proposed = dict(copy)
        proposed["canonical"] = canonical
        proposed["schema"] = schema
        proposed["image_alt_fixes"] = alt_fixes
        proposed["twitter_card"] = "summary_large_image"
        proposed["meta_keywords_removed"] = True  # L1 fix: drop the obsolete tag

        stores.update_page(url, {"proposed": proposed, "status": "generated"})

    stores.append_audit(
        "generation.agent",
        f"{'Regenerated fields for ' + str(len(fix_only)) + ' page(s) (Loop A)' if fix_only else f'Generated proposed copy for {generated} pages'} "
        f"({'LLM-written' if live_count else 'stub — no API key'})",
        f"page_db.json · {live_count}/{generated} live",
    )

    return {"agent": AGENT, "pages": generated, "llm_live": live_count, "loop_a": bool(fix_only)}
