"""Render what the human agent sees when a handed-off chat arrives."""
from __future__ import annotations

import html

from .mock_genesys import Conversation

CSS = """
body{font-family:Inter,system-ui,sans-serif;background:#f6f4ef;color:#1b1f3b;margin:0;padding:24px}
.card{background:#fff;border:1px solid #dcd9e6;border-radius:12px;padding:20px;max-width:980px;margin:0 auto 24px}
h2{margin:0 0 4px;font-size:20px} .meta{color:#606478;font-size:14px;margin-bottom:12px}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;background:#e8e6fb;color:#3b33b5;margin:2px;font-size:13px}
.p1{background:#fde2e2;color:#9b1c1c}.p2{background:#fdf0d5;color:#8a5a00}.p3{background:#e5f4e4;color:#1e6b2d}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 14px} td{border-top:1px solid #eee;padding:6px 8px;vertical-align:top}
td:first-child{color:#606478;width:180px} pre{white-space:pre-wrap;background:#1e2134;color:#e6e8f5;padding:14px;border-radius:10px;font-size:13px}
.msg{padding:6px 0;border-top:1px solid #f0eef5;font-size:14px}.who{font-weight:600;margin-right:6px}
"""


def render(conv: Conversation) -> str:
    pd = conv.participant_data
    flags = [f for f in pd.get("hl_flags", "").split(",") if f]
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in sorted(pd.items()))
    first = html.escape(conv.messages[0][1]) if conv.messages else ""
    rest = "".join(f"<div class='msg'><span class='who'>{html.escape(s)}</span>{html.escape(m)}</div>"
                   for s, m in conv.messages[1:])
    return f"""<div class='card'>
<h2>{html.escape(conv.queue)} <span class='pill {conv.priority.lower()}'>{conv.priority}</span></h2>
<div class='meta'>{conv.id} · routed by {conv.routed_by} · agent {html.escape(conv.agent or 'waiting')} · state {conv.state}</div>
<div>{''.join(f"<span class='pill'>{html.escape(f)}</span>" for f in flags) or "<span class='pill'>no flags</span>"}</div>
<table>{rows}</table>
<pre>{first}</pre>{rest}</div>"""


def page(convs: list[Conversation], title: str = "Agent view") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><h1 style='max-width:980px;margin:0 auto 16px'>"
            f"{html.escape(title)}</h1>{''.join(render(c) for c in convs)}</body></html>")
