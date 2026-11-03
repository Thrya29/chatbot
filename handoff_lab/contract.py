"""Handoff contract v1: build, validate, render and parse.

Two layers so the handoff survives any integration:
  * attributes  -> machine routing (Genesys participant data)
  * note        -> a fixed-order briefing a human reads in seconds
If a platform only passes free text, the first line of the rendered note is
machine-readable, so routing can still work by string match (see parse_header).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "contracts" / "handoff-contract.v1.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
HEADER_RE = re.compile(r"^\[HANDOFF v1\] (?P<kv>.+)$")


@dataclass
class Handoff:
    queue: str
    priority: str
    flags: list[str]
    topic: str
    conversation_id: str
    summary: str
    client_wants: str
    lang: str = "en"
    auth: str = "verified"
    entity: str = ""
    ref: str = ""
    ai_told_them: list[str] = field(default_factory=list)
    data_pulled: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    client_was_told: str = ""
    do_not_ask_again: list[str] = field(default_factory=list)
    suggested_next_step: str = ""
    transcript_link: str = ""

    def to_payload(self) -> dict:
        attrs = {
            "hl_queue": self.queue,
            "hl_priority": self.priority,
            "hl_flags": ",".join(sorted(set(self.flags))),
            "hl_lang": self.lang,
            "hl_auth": self.auth,
            "hl_topic": self.topic,
            "hl_conversation_id": self.conversation_id,
            "hl_contract": "v1",
        }
        if self.entity:
            attrs["hl_entity"] = self.entity
        if self.ref:
            attrs["hl_ref"] = self.ref
        note = {
            "summary": self.summary,
            "client_wants": self.client_wants,
            "ai_told_them": self.ai_told_them,
            "data_pulled": self.data_pulled,
            "signals": self.signals,
            "client_was_told": self.client_was_told,
            "do_not_ask_again": self.do_not_ask_again,
            "suggested_next_step": self.suggested_next_step,
        }
        if self.transcript_link:
            note["transcript_link"] = self.transcript_link
        payload = {"version": "v1", "attributes": attrs, "note": note}
        validate(payload)
        return payload


def validate(payload: dict) -> None:
    """Raise jsonschema.ValidationError if the payload breaks the contract."""
    jsonschema.validate(payload, SCHEMA)


def render_note(payload: dict) -> str:
    """Fixed-order text a human agent reads. Line 1 is machine-readable."""
    a, n = payload["attributes"], payload["note"]
    header = (
        f"[HANDOFF v1] queue={a['hl_queue']} priority={a['hl_priority']} "
        f"flags={a['hl_flags'] or 'none'} lang={a['hl_lang']} auth={a['hl_auth']}"
    )
    def block(label: str, items) -> str:
        if isinstance(items, list):
            items = "; ".join(items) if items else "(none)"
        return f"{label}: {items}"
    lines = [
        header,
        block("SUMMARY", n["summary"]),
        block("CLIENT WANTS", n["client_wants"]),
        block("AI ALREADY TOLD THEM", n["ai_told_them"]),
        block("DATA PULLED", n["data_pulled"]),
        block("SIGNALS", n["signals"]),
        block("CLIENT WAS TOLD", n["client_was_told"]),
        block("DO NOT ASK AGAIN", n["do_not_ask_again"]),
        block("SUGGESTED NEXT STEP", n["suggested_next_step"] + " (the human decides)"),
    ]
    if n.get("transcript_link"):
        lines.append(block("TRANSCRIPT", n["transcript_link"]))
    return "\n".join(lines)


def parse_header(text: str) -> dict[str, str] | None:
    """Recover routing fields from the first line when only free text survives."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    m = HEADER_RE.match(first)
    if not m:
        return None
    out = {}
    for pair in m.group("kv").split():
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k] = "" if v == "none" else v
    return out
