"""Builds dashboard/optimization-dashboard.html from the *given* dashboard
asset (docs/Optimization Guide (2).html) plus the pipeline's real output.

Rather than rebuild the dashboard UI from scratch, this patches the exact
JS data constants (and the few hardcoded stat widgets) that feed its
"Dashboard" tab with real numbers from data/*.json, and leaves everything
else — the Guide, Agents and Tracker tabs, the whole page shell, every
font/asset payload — byte-for-byte untouched. The Guide tab's own banner
("Values are sample data until Search Console, CrUX, PageSpeed Insights, a
backlink API and analytics are connected") stays accurate: those panels are
deliberately left alone here, because that's the one thing still true —
no GSC/CrUX/Ahrefs/Analytics credentials exist for darwix.ai yet.

This is pure text surgery on a bundler-exported single-file HTML page:
  1. The page has four top-level `<script type="__bundler/...">` tags
     (manifest, ext_resources, page_order, template), each holding a JSON
     string. `template`'s string is itself an HTML document, which in turn
     embeds the dashboard's actual JS logic in one
     `<script type="text/x-dc" data-dc-script="">...</script>` tag.
  2. We decode `template`, find specific JS array literals inside that
     inner script (by key name, with a bracket-depth scanner — not regex,
     since the arrays contain nested `{}`), splice in new JSON arrays built
     from pipeline output, and patch a few hardcoded stat strings in the
     surrounding markup (a gauge percentage and an open-issue count that
     the design tool baked in as static text rather than a binding).
  3. Re-encode template back into a JSON string with the same `</` ->
     `/` escaping the original export uses (so the literal text
     `</script>` never appears inside the outer `<script>` tag — otherwise
     the browser would end the tag early), and splice that back into the
     original raw HTML at the same byte range, nothing else touched.
"""
from __future__ import annotations

import json
import re

from . import config, dashboard_data

SOURCE_HTML = config.DOCS_DIR / "Optimization Guide (2).html"
OUTPUT_HTML = config.DASHBOARD_DIR / "optimization-dashboard.html"

TOP_SCRIPT_RE = re.compile(r'<script type="__bundler/(\w+)">(.*?)</script>', re.S)


def _find_matching_close(src: str, open_idx: int) -> int:
    """Given the index of an opening '[' or '{', return the index of its
    matching close, skipping over double/single-quoted string literals."""
    opener = src[open_idx]
    closer = {"[": "]", "{": "}"}[opener]
    depth = 0
    i = open_idx
    in_str: str | None = None
    while i < len(src):
        ch = src[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"No matching close found for '{opener}' at {open_idx}")


def replace_js_array(src: str, key: str, new_value) -> str:
    """Replace the array literal assigned to `key:[...]` with new_value
    (a Python list, serialized as JSON — valid JS syntax)."""
    marker = f"{key}:["
    idx = src.find(marker)
    if idx == -1:
        raise ValueError(f"Could not find {marker!r} in dashboard script — the given "
                          f"dashboard's structure may have changed; patch is out of date.")
    open_idx = idx + len(key) + 1
    close_idx = _find_matching_close(src, open_idx)
    new_text = json.dumps(new_value, ensure_ascii=False)
    return src[:open_idx] + new_text + src[close_idx + 1:]


def replace_const_array(src: str, name: str, new_value) -> str:
    """Same as replace_js_array, but for a top-level `const NAME=[...]`
    declaration instead of an object-literal `key:[...]` property."""
    marker = f"const {name}=["
    idx = src.find(marker)
    if idx == -1:
        raise ValueError(f"Could not find {marker!r} in dashboard script — the given "
                          f"dashboard's structure may have changed; patch is out of date.")
    open_idx = idx + len(marker) - 1
    close_idx = _find_matching_close(src, open_idx)
    new_text = json.dumps(new_value, ensure_ascii=False)
    return src[:open_idx] + new_text + src[close_idx + 1:]


def replace_literal(src: str, old: str, new: str, *, required: bool = True) -> str:
    count = src.count(old)
    if count != 1:
        if required:
            raise ValueError(f"Expected exactly one occurrence of {old!r}, found {count}.")
        return src
    return src.replace(old, new, 1)


def patch_dcscript(script: str, data: dict) -> str:
    script = replace_js_array(script, "tech", data["tech"])
    script = replace_js_array(script, "actions", data["actions"])
    script = replace_js_array(script, "qaFails", data["qaFails"])
    script = replace_js_array(script, "pipeStats", data["pipeStats"])
    script = replace_js_array(script, "agentRows", data["agentRows"])
    script = replace_js_array(script, "audit", data["audit"])
    # Google-services panels (see dashboard_data.google_panels()) — each
    # field inside these already falls back to the original sample value
    # wherever its service isn't configured, so this is safe to always run.
    script = replace_js_array(script, "kpis", data["kpis"])
    script = replace_js_array(script, "cwv", data["cwv"])
    script = replace_js_array(script, "funnel", data["funnel"])
    script = replace_const_array(script, "WK_D", data["WK_D"])
    script = replace_const_array(script, "WK_C", data["WK_C"])
    script = replace_const_array(script, "WK_R", data["WK_R"])
    if "health" in data:
        script = replace_const_array(script, "HEALTH", data["health"])

    # Bind the top 4 metricCards directly to D.kpis so that real GSC / GA4 / crawl data displays
    script = re.sub(
        r'metricCards:\[\{k:"Organic clicks",.*?\}\]',
        'metricCards:['
        '{k:"Organic clicks",v:(D.kpis&&D.kpis[0]?D.kpis[0].v:"0"),d:(D.kpis&&D.kpis[0]?D.kpis[0].d:""),cmp:(D.kpis&&D.kpis[0]&&D.kpis[0].v!=="0"&&!D.kpis[0].d.includes("connected")?"vs previous 28 days":""),ic:ICN.pointer,ai:(D.kpis&&D.kpis[0]&&D.kpis[0].v!=="0"?ICN.up:ICN.check),c:(D.kpis&&D.kpis[0]?D.kpis[0].c:"#17AA2E")},'
        '{k:"Average position",v:(D.kpis&&D.kpis[3]?D.kpis[3].v:"—"),d:(D.kpis&&D.kpis[3]?D.kpis[3].d:""),cmp:(D.kpis&&D.kpis[3]&&D.kpis[3].v!=="—"?"higher than previous 28 days":""),ic:ICN.target,ai:(D.kpis&&D.kpis[3]&&D.kpis[3].v!=="—"?ICN.up:ICN.info),c:(D.kpis&&D.kpis[3]?D.kpis[3].c:"#949395")},'
        '{k:"Demo requests",v:(D.kpis&&D.kpis[4]?D.kpis[4].v:"0"),d:(D.kpis&&D.kpis[4]?D.kpis[4].d:""),cmp:(D.kpis&&D.kpis[4]&&D.kpis[4].v!=="0"?"vs previous 28 days":""),ic:ICN.calendar,ai:(D.kpis&&D.kpis[4]&&D.kpis[4].v!=="0"?ICN.up:ICN.check),c:(D.kpis&&D.kpis[4]?D.kpis[4].c:"#17AA2E")},'
        '{k:"Indexed pages",v:(D.kpis&&D.kpis[6]?D.kpis[6].v:"21 / 21"),d:(D.kpis&&D.kpis[6]?D.kpis[6].d:"All crawled pages 200 OK"),cmp:"",ic:ICN.database,ai:ICN.check,c:(D.kpis&&D.kpis[6]?D.kpis[6].c:"#17AA2E")}'
        ']',
        script,
        count=1
    )
    return script


def patch_static_markup(template_html: str, data: dict) -> str:
    static = data["static"]
    pct = static["pct"]
    template_html = replace_literal(template_html, 'stroke-dasharray="72 100"', f'stroke-dasharray="{pct} 100"')
    template_html = replace_literal(template_html, '>72%</div>', f'>{pct}%</div>')
    template_html = replace_literal(
        template_html,
        "Target 85%. 16 of 58 pages failed at least one check on the first pass.",
        static["sentence"],
    )
    # The original has a mojibake'd middle dot (·) around this phrase depending
    # on source encoding; match on the stable parts around it instead.
    template_html = re.sub(
        r"46 pages published.*?from brief to live",
        static["publish_sentence"].replace("\\", "\\\\"),
        template_html,
        count=1,
    )
    template_html = replace_literal(template_html, ">7 open<", f'>{data["open_count"]} open<')
    if "health_overall" in data:
        template_html = replace_literal(template_html, ">78</span>", f'>{data["health_overall"]}</span>', required=False)
    return template_html


def build() -> None:
    raw = SOURCE_HTML.read_text(encoding="utf-8")

    blocks = {m.group(1): (m.span(2), m.group(2)) for m in TOP_SCRIPT_RE.finditer(raw)}
    if "template" not in blocks:
        raise ValueError("Could not find the __bundler/template script in the source dashboard HTML.")
    (tmpl_start, tmpl_end), tmpl_raw_json = blocks["template"]

    template_html = json.loads(tmpl_raw_json)

    dc_attr_idx = template_html.index("data-dc-script")
    script_open_end = template_html.index(">", dc_attr_idx) + 1
    script_close = template_html.index("</script>", script_open_end)
    old_script = template_html[script_open_end:script_close]

    data = dashboard_data.build()
    new_script = patch_dcscript(old_script, data)

    new_template_html = template_html[:script_open_end] + new_script + template_html[script_close:]
    new_template_html = patch_static_markup(new_template_html, data)

    encoded = json.dumps(new_template_html, ensure_ascii=True)
    encoded = encoded.replace("</", "<\\u002F")

    new_raw = raw[:tmpl_start] + encoded + raw[tmpl_end:]

    config.DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(new_raw, encoding="utf-8")
    return data


if __name__ == "__main__":
    d = build()
    print(f"Wrote {OUTPUT_HTML} with real data as of {d['generated_at']}")
