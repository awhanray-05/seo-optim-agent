"""The shared data stores every agent reads and writes.

These are the five stores named in the architecture diagram:
  - keyword_map   : keyword-to-URL map (1 primary + 2-5 secondary per page)
  - page_db       : page database / CMS stand-in (content, metadata, status)
  - sitemap_tree  : parent of each page; drives URLs and breadcrumbs
  - entity_list   : consistent brand/product/industry names
  - results_store : daily crawl / vitals / backlink / conversion data

Each is just a JSON file under data/. Any agent can be re-run on its own
because it only ever reads/writes through this module — nothing is held in
memory between runs.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import config

STORE_FILES = {
    "keyword_map": "keyword_map.json",
    "page_db": "page_db.json",
    "sitemap_tree": "sitemap_tree.json",
    "entity_list": "entity_list.json",
    "results_store": "results_store.json",
    "qa_reports": "qa_reports.json",
    "publish_log": "publish_log.json",
    "action_queue": "action_queue.json",
}

AUDIT_LOG_FILE = "audit_trail.jsonl"


def _path(name: str) -> Path:
    config.ensure_data_dir()
    return config.DATA_DIR / STORE_FILES[name]


def load(name: str, default: Any = None) -> Any:
    p = _path(name)
    if not p.exists():
        return {} if default is None else default
    with open(p, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {} if default is None else default


def save(name: str, data: Any) -> None:
    p = _path(name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)


def update_page(url: str, patch: dict) -> dict:
    """Merge `patch` into page_db[url] and persist. Returns the full record."""
    db = load("page_db", {})
    rec = db.get(url, {})
    rec.update(patch)
    db[url] = rec
    save("page_db", db)
    return rec


def get_page(url: str) -> dict:
    return load("page_db", {}).get(url, {})


def append_audit(actor: str, action: str, evidence: str = "") -> None:
    """One row per automated action, as the dashboard's 'Audit trail' panel expects."""
    config.ensure_data_dir()
    row = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "a": actor,
        "x": action,
        "e": evidence,
    }
    p = config.DATA_DIR / AUDIT_LOG_FILE
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_audit(limit: int = 200) -> list[dict]:
    p = config.DATA_DIR / AUDIT_LOG_FILE
    if not p.exists():
        return []
    rows = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows[-limit:][::-1]


def push_action(action: dict) -> None:
    """Add one row to the action queue (Loop B: monitoring -> owning agent)."""
    q = load("action_queue", [])
    q.append(action)
    save("action_queue", q)


def clear_action_queue() -> None:
    save("action_queue", [])


def init_entity_list() -> None:
    if not load("entity_list", None):
        save("entity_list", config.ENTITY_LIST)
