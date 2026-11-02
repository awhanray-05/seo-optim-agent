"""GitHub CMS publishing for Agent 04.

Darwix AI's content repo and branch are configured in `.env`
(GITHUB_TOKEN / GITHUB_REPO / GITHUB_DEFAULT_BRANCH). Rather than guess the
target repo's page-template schema (frontmatter keys, file layout — not
something this pipeline can know), this opens a review PR: a new branch off
GITHUB_DEFAULT_BRANCH carrying the exact staged publish package (per-page
diffs, sitemap.xml, redirect_map.json) under GITHUB_CONTENT_PREFIX, so a
human (or a follow-up integration that *does* know the CMS schema) can
review and merge it.

Unset GITHUB_TOKEN/GITHUB_REPO -> every function here is a no-op; callers
check `is_configured()` first.
"""
from __future__ import annotations

import base64

import requests

from . import config

API_ROOT = "https://api.github.com"


def is_configured() -> bool:
    return bool(config.GITHUB_TOKEN and config.GITHUB_REPO)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_branch_sha(branch: str) -> str | None:
    r = requests.get(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/git/ref/heads/{branch}",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        return None
    return r.json()["object"]["sha"]


def ensure_branch(branch: str) -> bool:
    """Create `branch` off GITHUB_DEFAULT_BRANCH if it doesn't exist yet.
    Returns True if the branch exists (whether just created or already
    there), False if the base branch itself couldn't be found."""
    if _get_branch_sha(branch):
        return True
    base_sha = _get_branch_sha(config.GITHUB_DEFAULT_BRANCH)
    if not base_sha:
        return False
    r = requests.post(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/git/refs",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT,
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
    )
    return r.status_code == 201


def upsert_file(path: str, content: str, message: str, branch: str) -> bool:
    """Create or update one file on `branch` via the Contents API."""
    url = f"{API_ROOT}/repos/{config.GITHUB_REPO}/contents/{path}"
    existing = requests.get(url, headers=_headers(), params={"ref": branch},
                             timeout=config.REQUEST_TIMEOUT)
    sha = existing.json().get("sha") if existing.status_code == 200 else None

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_headers(), json=payload, timeout=config.REQUEST_TIMEOUT)
    return r.status_code in (200, 201)


def find_open_pr(branch: str) -> dict | None:
    owner = config.GITHUB_REPO.split("/")[0]
    r = requests.get(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/pulls",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT,
        params={"head": f"{owner}:{branch}", "state": "open"},
    )
    if r.status_code != 200 or not r.json():
        return None
    return r.json()[0]


def open_pull_request(branch: str, title: str, body: str) -> dict | None:
    """Open a PR for `branch` -> GITHUB_DEFAULT_BRANCH, or return the
    already-open one for this branch if a previous run already opened it."""
    existing = find_open_pr(branch)
    if existing:
        return existing
    r = requests.post(
        f"{API_ROOT}/repos/{config.GITHUB_REPO}/pulls",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT,
        json={"title": title, "head": branch, "base": config.GITHUB_DEFAULT_BRANCH, "body": body},
    )
    if r.status_code != 201:
        return None
    return r.json()


def publish_package(branch: str, files: dict[str, str], pr_title: str, pr_body: str) -> dict:
    """Push `files` (path -> content, relative to GITHUB_CONTENT_PREFIX) to
    `branch` and open/reuse a review PR. Returns a result dict describing
    what happened; never raises — a failure degrades to
    {"ok": False, "error": ...} so a GitHub outage can't crash the run."""
    if not is_configured():
        return {"ok": False, "error": "GITHUB_TOKEN/GITHUB_REPO not set"}
    try:
        if not ensure_branch(branch):
            return {"ok": False, "error": f"could not create branch {branch!r} off {config.GITHUB_DEFAULT_BRANCH!r}"}
        pushed = []
        for rel_path, content in files.items():
            full_path = f"{config.GITHUB_CONTENT_PREFIX}/{rel_path}"
            if upsert_file(full_path, content, f"SEO automation: update {rel_path}", branch):
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
