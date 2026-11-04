"""GitHub CMS publishing for Agent 04 — REAL source-file editing.

This module reads approved page diffs from the publish_package, locates the
actual Next.js App Router source files in `teamcur8/darwix-website`, patches
metadata exports (title, description, canonical, keywords, openGraph/twitter),
injects JSON-LD schema <script> blocks via a SchemaMarkup component import,
and updates `async redirects()` in `next.config.ts`.  The result is a PR whose
merge directly deploys the SEO changes — no manual copy-paste required.

URL → file mapping (App Router, `src/app/`):
  /               → src/app/page.tsx
  /about          → src/app/about/page.tsx
  /voiceAgent     → src/app/voiceAgent/page.tsx
  /idn/privacy-policy → src/app/idn/privacy-policy/page.tsx
  etc.

Env vars consumed (from pipeline/config.py):
  GITHUB_TOKEN, GITHUB_REPO, GITHUB_DEFAULT_BRANCH, GITHUB_CONTENT_PREFIX
"""
from __future__ import annotations

import base64
import json
import re
import textwrap
from typing import Any
from urllib.parse import urlparse

import requests

from . import config

API_ROOT = "https://api.github.com"
# Root for Next.js app router pages inside the repo
APP_SRC = "src/app"


# ---------------------------------------------------------------------------
# Auth / low-level helpers
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    return bool(config.GITHUB_TOKEN and config.GITHUB_REPO)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get(path: str, **kwargs) -> requests.Response:
    return requests.get(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/{path}",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT, **kwargs
    )


def _put(path: str, **kwargs) -> requests.Response:
    return requests.put(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/{path}",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT, **kwargs
    )


def _post(path: str, **kwargs) -> requests.Response:
    return requests.post(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/{path}",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT, **kwargs
    )


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------

def _get_branch_sha(branch: str) -> str | None:
    r = _get(f"git/ref/heads/{branch}")
    return r.json()["object"]["sha"] if r.status_code == 200 else None


def ensure_branch(branch: str) -> bool:
    """Create `branch` off GITHUB_DEFAULT_BRANCH if it doesn't exist yet."""
    if _get_branch_sha(branch):
        return True
    base_sha = _get_branch_sha(config.GITHUB_DEFAULT_BRANCH)
    if not base_sha:
        return False
    r = _post("git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
    return r.status_code == 201


# ---------------------------------------------------------------------------
# File read / write on a branch
# ---------------------------------------------------------------------------

def _read_file(repo_path: str, branch: str) -> tuple[str, str] | tuple[None, None]:
    """Return (content_str, sha) or (None, None) if not found."""
    r = _get(f"contents/{repo_path}", params={"ref": branch})
    if r.status_code != 200:
        return None, None
    data = r.json()
    # GitHub paginates large files via download_url; for page.tsx files
    # (< 100 KB) the inline base64 is always present.
    raw = data.get("content", "")
    # GitHub wraps lines at 60 chars with \n inside the base64 blob
    content = base64.b64decode(raw).decode("utf-8", errors="replace")
    return content, data["sha"]


def _write_file(repo_path: str, content: str, message: str, branch: str, sha: str | None = None) -> bool:
    """Create or update a file on `branch`."""
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = _put(f"contents/{repo_path}", json=payload)
    return r.status_code in (200, 201)


# ---------------------------------------------------------------------------
# URL → repo file path mapping
# ---------------------------------------------------------------------------

def url_to_page_file(url: str) -> str:
    """Map a full URL to its Next.js App Router page.tsx path in the repo.

    Examples:
      https://darwix.ai/              → src/app/page.tsx
      https://darwix.ai/about         → src/app/about/page.tsx
      https://darwix.ai/voiceAgent    → src/app/voiceAgent/page.tsx
      https://darwix.ai/idn/privacy-policy → src/app/idn/privacy-policy/page.tsx
      https://testing.darwix.ai/about → src/app/about/page.tsx  (strip subdomain)
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return f"{APP_SRC}/page.tsx"
    return f"{APP_SRC}/{path}/page.tsx"


# ---------------------------------------------------------------------------
# Metadata patching — Next.js App Router `export const metadata`
# ---------------------------------------------------------------------------

def _escape_ts_string(s: str) -> str:
    """Escape a string value for embedding inside a TypeScript string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("${", "\\${")


def patch_metadata_title(source: str, new_title: str) -> str:
    """Replace the `default:` value inside `title: { default: "..." }`.

    Handles both `title: { default: "...", template: "..." }` (object form)
    and `title: "..."` (string form).
    """
    esc = _escape_ts_string(new_title)

    # Object form: title: { default: "...", template: "..." }
    # We only replace the `default` key, leaving template intact.
    obj_pattern = re.compile(
        r'(title\s*:\s*\{[^}]*?default\s*:\s*)"[^"]*?"',
        re.DOTALL,
    )
    if obj_pattern.search(source):
        return obj_pattern.sub(lambda m: f'{m.group(1)}"{esc}"', source, count=1)

    # String form: title: "..."
    str_pattern = re.compile(r'(title\s*:\s*)"[^"]*?"')
    if str_pattern.search(source):
        return str_pattern.sub(lambda m: f'{m.group(1)}"{esc}"', source, count=1)

    return source  # unchanged if no match


def patch_metadata_description(source: str, new_desc: str) -> str:
    """Replace the top-level `description:` value."""
    esc = _escape_ts_string(new_desc)
    # Match:  description: "..." or description:\n    "..."
    pattern = re.compile(
        r'(\bdescription\s*:\s*)"[^"]*?"',
        re.DOTALL,
    )
    if pattern.search(source):
        return pattern.sub(lambda m: f'{m.group(1)}"{esc}"', source, count=1)
    return source


def patch_metadata_canonical(source: str, new_canonical: str) -> str:
    """Replace alternates.canonical value.  Canonical is stored as a path,
    e.g. "/about", not a full URL — strip the origin if present."""
    parsed = urlparse(new_canonical)
    # Keep only the path portion
    path = parsed.path if parsed.path else "/"
    esc = _escape_ts_string(path)

    pattern = re.compile(r'(canonical\s*:\s*)"[^"]*?"')
    if pattern.search(source):
        return pattern.sub(lambda m: f'{m.group(1)}"{esc}"', source, count=1)
    return source


def patch_metadata_keywords(source: str, new_keywords: list[str]) -> str:
    """Replace the `keywords: [...]` array in the metadata export."""
    kw_items = ",\n    ".join(f'"{_escape_ts_string(k)}"' for k in new_keywords)
    replacement = f'keywords: [\n    {kw_items},\n  ]'

    # Match existing keywords array (possibly multiline)
    pattern = re.compile(r'keywords\s*:\s*\[[^\]]*?\]', re.DOTALL)
    if pattern.search(source):
        return pattern.sub(replacement, source, count=1)
    return source


def patch_og_title(source: str, new_title: str) -> str:
    """Update openGraph.title."""
    esc = _escape_ts_string(new_title)
    # openGraph block is after the top-level title; match title inside openGraph
    # Strategy: find `openGraph:` then replace the first `title:` after it
    og_start = source.find("openGraph:")
    if og_start == -1:
        return source
    before = source[:og_start]
    og_section = source[og_start:]
    pattern = re.compile(r'(title\s*:\s*)"[^"]*?"')
    og_section = pattern.sub(lambda m: f'{m.group(1)}"{esc}"', og_section, count=1)
    return before + og_section


def patch_og_description(source: str, new_desc: str) -> str:
    """Update openGraph.description."""
    esc = _escape_ts_string(new_desc)
    og_start = source.find("openGraph:")
    if og_start == -1:
        return source
    before = source[:og_start]
    og_section = source[og_start:]
    pattern = re.compile(r'(\bdescription\s*:\s*)"[^"]*?"', re.DOTALL)
    og_section = pattern.sub(lambda m: f'{m.group(1)}"{esc}"', og_section, count=1)
    return before + og_section


def apply_metadata_patches(source: str, diff: dict) -> str:
    """Apply all metadata field patches from a publish-package diff dict.

    diff keys consumed:
      title.to, meta_description.to, canonical.to,
      og_title.to (optional), og_description.to (optional),
      keywords (optional list[str])
    """
    new_title = (diff.get("title") or {}).get("to")
    new_desc = (diff.get("meta_description") or {}).get("to")
    new_canonical = (diff.get("canonical") or {}).get("to")
    new_keywords = diff.get("keywords")  # optional

    if new_title:
        source = patch_metadata_title(source, new_title)
        # Mirror to OG title too (keep short — truncate to 60 chars)
        source = patch_og_title(source, new_title[:60])
    if new_desc:
        source = patch_metadata_description(source, new_desc)
        source = patch_og_description(source, new_desc[:155])
    if new_canonical:
        source = patch_metadata_canonical(source, new_canonical)
    if new_keywords:
        source = patch_metadata_keywords(source, new_keywords)

    return source


# ---------------------------------------------------------------------------
# H1 patching
# ---------------------------------------------------------------------------

def patch_h1(source: str, new_h1: str) -> str:
    """Replace the content of the first <h1> tag in the JSX.

    Handles:
      <h1 className="...">Old text</h1>
      <h1>Old text</h1>

    Does NOT touch h2/h3.  Returns source unchanged if no <h1> found.
    """
    # Match <h1 ...>content</h1> — content may contain nested spans/br but
    # for safety we target simple text-only h1s (no child elements).
    # Pattern: <h1 optional-attrs>TEXT ONLY</h1>
    simple = re.compile(r'(<h1(?:\s[^>]*)?>)([^<]+)(</h1>)', re.DOTALL)
    if simple.search(source):
        esc = new_h1.replace("{", "&#123;").replace("}", "&#125;")
        return simple.sub(lambda m: f'{m.group(1)}{esc}{m.group(3)}', source, count=1)
    return source


# ---------------------------------------------------------------------------
# JSON-LD schema injection
# ---------------------------------------------------------------------------

_SCHEMA_IMPORT_LINE = "import SchemaMarkup from \"@/components/SchemaMarkup\";\n"
_SCHEMA_COMPONENT_STUB = textwrap.dedent("""\
    // SchemaMarkup injects JSON-LD via next/head <script type="application/ld+json">
    // Component is auto-generated by the SEO pipeline if not already present.
""")


def _build_schema_component_tsx(schemas: list[dict]) -> str:
    """Generate a reusable SchemaMarkup component that injects JSON-LD schemas."""
    schema_json = json.dumps(schemas, indent=2, ensure_ascii=False)
    return textwrap.dedent(f"""\
        "use client";
        import {{ useEffect }} from "react";

        const schemas = {schema_json};

        export default function SchemaMarkup() {{
          useEffect(() => {{
            schemas.forEach((schema, i) => {{
              const existing = document.querySelector(`script[data-schema-index="${{i}}"]`);
              if (existing) return;
              const script = document.createElement("script");
              script.type = "application/ld+json";
              script.setAttribute("data-schema-index", String(i));
              script.textContent = JSON.stringify(schema);
              document.head.appendChild(script);
            }});
          }}, []);
          return null;
        }}
    """)


def inject_schema_into_page(source: str, schemas: list[dict]) -> str:
    """Inject a <SchemaMarkup /> component call into the page's JSX return,
    and add the import line if not already present.

    Strategy:
    1. If the file already contains <SchemaMarkup, skip injection.
    2. Add import at top of imports block.
    3. Insert <SchemaMarkup /> as first child inside the outermost return (
       right after `return (` or `return <tag`).
    """
    if not schemas:
        return source

    # Already injected?
    if "SchemaMarkup" in source:
        return source

    # Add import after the last existing import line
    import_pattern = re.compile(r'^import .+;\s*$', re.MULTILINE)
    matches = list(import_pattern.finditer(source))
    if matches:
        last_import_end = matches[-1].end()
        source = source[:last_import_end] + "\n" + _SCHEMA_IMPORT_LINE + source[last_import_end:]
    else:
        source = _SCHEMA_IMPORT_LINE + source

    # Insert <SchemaMarkup /> inside the JSX return block
    # Find `return (` or `return <` and insert after the opening paren/tag
    return_paren = re.compile(r'(\breturn\s*\()\s*(<)', re.MULTILINE)
    if return_paren.search(source):
        source = return_paren.sub(
            lambda m: f'{m.group(1)}\n      <SchemaMarkup />\n      {m.group(2)}',
            source, count=1
        )

    return source


# ---------------------------------------------------------------------------
# next.config.ts — redirects block patching
# ---------------------------------------------------------------------------

def patch_nextconfig_redirects(source: str, new_redirects: list[dict]) -> str:
    """Add new redirect entries to the existing `async redirects()` return array.

    new_redirects: list of dicts with keys `from` (source path) and `to` (destination path).
    Skips any redirect whose `source` already exists in the config.
    """
    if not new_redirects:
        return source

    # Find the redirects() return array
    # Matches: return [\n  ... existing entries ... \n]
    array_pattern = re.compile(
        r'(async\s+redirects\s*\(\s*\)\s*\{[^}]*?return\s*\[)(.*?)(\];)',
        re.DOTALL,
    )
    m = array_pattern.search(source)
    if not m:
        return source

    existing_array_text = m.group(2)

    new_entries: list[str] = []
    for rd in new_redirects:
        src_path = urlparse(rd.get("from", "")).path or rd.get("from", "")
        dst_path = urlparse(rd.get("to", "")).path or rd.get("to", "")
        permanent = rd.get("type", 301) == 301

        # Skip if source path already present
        if f'source: "{src_path}"' in existing_array_text or f"source: '{src_path}'" in existing_array_text:
            continue

        perm_str = "true" if permanent else "false"
        entry = textwrap.dedent(f"""\
            {{
              source: "{src_path}",
              destination: "{dst_path}",
              permanent: {perm_str},
            }}""")
        new_entries.append(entry)

    if not new_entries:
        return source

    # Build each redirect entry with consistent 8-space indent
    indent = "        "
    formatted_entries = []
    for entry in new_entries:
        # Re-indent each line of the entry block
        lines = entry.strip().split("\n")
        indented = ("\n" + indent).join(line.strip() for line in lines)
        formatted_entries.append(indent + indented)

    additions_str = ",\n".join(formatted_entries)

    # Strip trailing comma from existing entries before appending
    stripped_existing = existing_array_text.rstrip().rstrip(",")
    if stripped_existing.strip():
        new_array = stripped_existing + ",\n" + additions_str + "\n      "
    else:
        new_array = "\n" + additions_str + "\n      "

    return source[:m.start(2)] + new_array + source[m.start(3):]


# ---------------------------------------------------------------------------
# PR helpers
# ---------------------------------------------------------------------------

def find_open_pr(branch: str) -> dict | None:
    owner = config.GITHUB_REPO.split("/")[0]
    r = _get("pulls", params={"head": f"{owner}:{branch}", "state": "open"})
    if r.status_code != 200 or not r.json():
        return None
    return r.json()[0]


def open_pull_request(branch: str, title: str, body: str) -> dict | None:
    existing = find_open_pr(branch)
    if existing:
        # Update the PR body to reflect the latest run
        pr_number = existing["number"]
        _post(f"issues/{pr_number}", json={"body": body})
        return existing
    r = _post(
        "pulls",
        json={
            "title": title,
            "head": branch,
            "base": config.GITHUB_DEFAULT_BRANCH,
            "body": body,
        },
    )
    return r.json() if r.status_code == 201 else None


# ---------------------------------------------------------------------------
# Schema component upsert
# ---------------------------------------------------------------------------

def _ensure_schema_component(branch: str, schemas: list[dict]) -> bool:
    """Create/update src/components/SchemaMarkup.tsx on the branch."""
    path = "src/components/SchemaMarkup.tsx"
    content = _build_schema_component_tsx(schemas)
    _, sha = _read_file(path, branch)
    return _write_file(path, content, "SEO automation: add/update SchemaMarkup component", branch, sha)


# ---------------------------------------------------------------------------
# Main publish entry point
# ---------------------------------------------------------------------------

def publish_seo_changes(
    branch: str,
    page_diffs: list[dict],
    redirect_map: list[dict],
    schemas_by_url: dict[str, list[dict]] | None = None,
) -> dict:
    """Push real source-code SEO edits to `branch` and open/update a PR.

    Args:
        branch:         Target branch name (e.g. "seo-automation/2026-11-04").
        page_diffs:     List of {url, diff} dicts from publish_package JSON files.
        redirect_map:   List of {from, to, type} dicts from redirect_map.json.
        schemas_by_url: Optional map of URL → list[dict] with JSON-LD schema objects.

    Returns a result dict:
        {ok, repo, branch, files_pushed, files_failed, pr_url, pr_number, errors}
    """
    if not is_configured():
        return {"ok": False, "error": "GITHUB_TOKEN/GITHUB_REPO not set"}

    errors: list[str] = []
    files_pushed: list[str] = []
    files_failed: list[str] = []

    try:
        if not ensure_branch(branch):
            return {
                "ok": False,
                "error": f"Could not create branch {branch!r} off {config.GITHUB_DEFAULT_BRANCH!r}",
            }

        # ----------------------------------------------------------------
        # 1. Patch each approved page's page.tsx
        # ----------------------------------------------------------------
        all_schemas: list[dict] = []  # for SchemaMarkup component

        for entry in page_diffs:
            url: str = entry.get("url", "")
            diff: dict = entry.get("diff", {})

            repo_path = url_to_page_file(url)
            source, sha = _read_file(repo_path, branch)

            if source is None:
                msg = f"File not found in repo: {repo_path} (URL: {url})"
                errors.append(msg)
                files_failed.append(repo_path)
                continue

            original = source

            # Apply metadata patches
            source = apply_metadata_patches(source, diff)

            # Apply H1 patch (optional — only if h1.to is set)
            new_h1 = (diff.get("h1") or {}).get("to")
            if new_h1:
                source = patch_h1(source, new_h1)

            # Collect schemas for the SchemaMarkup component
            if schemas_by_url and url in schemas_by_url:
                page_schemas = schemas_by_url[url]
                all_schemas.extend(page_schemas)
                # Inject SchemaMarkup import + JSX into the page
                source = inject_schema_into_page(source, page_schemas)

            if source == original:
                # No changes detected — skip write
                continue

            slug = url_to_page_file(url).split("/")[-2] or "home"
            commit_msg = f"SEO automation: update metadata for /{slug}"
            if _write_file(repo_path, source, commit_msg, branch, sha):
                files_pushed.append(repo_path)
            else:
                files_failed.append(repo_path)
                errors.append(f"Failed to write {repo_path}")

        # ----------------------------------------------------------------
        # 2. Patch next.config.ts with redirect rules
        # ----------------------------------------------------------------
        redirects_to_add = [rd for rd in redirect_map if rd.get("from") != rd.get("to")]
        if redirects_to_add:
            nc_path = "next.config.ts"
            nc_source, nc_sha = _read_file(nc_path, branch)
            if nc_source is not None:
                nc_patched = patch_nextconfig_redirects(nc_source, redirects_to_add)
                if nc_patched != nc_source:
                    if _write_file(nc_path, nc_patched,
                                   f"SEO automation: add {len(redirects_to_add)} redirect rule(s)",
                                   branch, nc_sha):
                        files_pushed.append(nc_path)
                    else:
                        files_failed.append(nc_path)
                        errors.append("Failed to write next.config.ts")
            else:
                errors.append("next.config.ts not found in repo")

        # ----------------------------------------------------------------
        # 3. Create/update SchemaMarkup component if schemas were added
        # ----------------------------------------------------------------
        if all_schemas:
            if _ensure_schema_component(branch, all_schemas):
                files_pushed.append("src/components/SchemaMarkup.tsx")
            else:
                errors.append("Failed to write SchemaMarkup component")

        # ----------------------------------------------------------------
        # 4. Open / refresh PR
        # ----------------------------------------------------------------
        changed_pages = [e["url"] for e in page_diffs]
        pr_body_lines = [
            "## SEO Automation: Real Source-Code Changes",
            "",
            f"**Pages updated:** {len(files_pushed)} file(s)  ",
            f"**Redirect rules added:** {len(redirects_to_add)}  ",
            f"**Errors:** {len(errors)}  ",
            "",
            "### Changed files",
        ]
        for f in files_pushed:
            pr_body_lines.append(f"- `{f}`")
        if errors:
            pr_body_lines += ["", "### Errors", *[f"- {e}" for e in errors]]
        pr_body_lines += [
            "",
            "---",
            "*Generated by the Darwix AI SEO Automation pipeline. "
            "Review and merge to deploy SEO changes to production.*",
        ]

        pr = open_pull_request(
            branch,
            title=f"SEO automation: {len(files_pushed)} source file(s) updated ({branch.split('/')[-1]})",
            body="\n".join(pr_body_lines),
        )

        return {
            "ok": True,
            "repo": config.GITHUB_REPO,
            "branch": branch,
            "files_pushed": files_pushed,
            "files_failed": files_failed,
            "pr_url": pr.get("html_url") if pr else None,
            "pr_number": pr.get("number") if pr else None,
            "errors": errors,
        }

    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Legacy shim — keep the old `publish_package()` name working
# ---------------------------------------------------------------------------

def publish_package(branch: str, files: dict[str, str], pr_title: str, pr_body: str) -> dict:
    """Backward-compat shim: push raw file blobs (old JSON diff approach).

    New code should call `publish_seo_changes()` instead.
    """
    if not is_configured():
        return {"ok": False, "error": "GITHUB_TOKEN/GITHUB_REPO not set"}
    try:
        if not ensure_branch(branch):
            return {"ok": False, "error": f"could not create branch {branch!r}"}
        pushed = []
        for rel_path, content in files.items():
            full_path = f"{config.GITHUB_CONTENT_PREFIX}/{rel_path}"
            _, sha = _read_file(full_path, branch)
            if _write_file(full_path, content, f"SEO automation: update {rel_path}", branch, sha):
                pushed.append(full_path)
        pr = open_pull_request(branch, pr_title, pr_body)
        return {
            "ok": True,
            "repo": config.GITHUB_REPO,
            "branch": branch,
            "files_pushed": pushed,
            "pr_url": pr.get("html_url") if pr else None,
            "pr_number": pr.get("number") if pr else None,
        }
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Convenience: upsert_file (kept for backward compat)
# ---------------------------------------------------------------------------

def upsert_file(path: str, content: str, message: str, branch: str) -> bool:
    _, sha = _read_file(path, branch)
    return _write_file(path, content, message, branch, sha)
