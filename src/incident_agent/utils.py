from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from incident_agent.schema import DUTCH_FIELD_LABELS
from incident_agent import messages as MSG

if TYPE_CHECKING:  # Avoid runtime import to prevent circular dependency
    from incident_agent.extract import IncidentExtractor


# =========================
# Question/label utilities
# =========================


def q_text(q: Any) -> str:
    if isinstance(q, dict):
        return str(q.get("question_text", ""))
    return str(getattr(q, "question_text", ""))


def q_field(q: Any) -> str:
    if isinstance(q, dict):
        return str(q.get("field_key", ""))
    return str(getattr(q, "field_key", ""))


def q_number(q: Any) -> str:
    field_key = q_field(q)
    label = DUTCH_FIELD_LABELS.get(field_key, field_key)
    num = label.split(" ")[0].strip()
    return num if num.replace(".", "").isdigit() else ""


def q_display(q: Any) -> str:
    num = q_number(q)
    text = q_text(q)
    return f"{num}: {text}" if num else text


# =========================
# Input parsing helpers
# =========================


def is_accept(text: str) -> bool:
    t = text.strip().lower()
    return t in {"ja", "ok", "okay", "akkoord", "yes", "y", "accept"}


def is_yes(text: str) -> bool:
    t = text.strip().lower()
    return t in {"ja", "yes", "y"}


def is_no(text: str) -> bool:
    t = text.strip().lower()
    return t in {"nee", "no", "n"}


def parse_mode_prefix(text: str) -> tuple[Optional[str], str]:
    stripped_text = text.strip()
    lower_text = stripped_text.lower()
    if lower_text.startswith("story"):
        rest = stripped_text[len("story") :].lstrip(" :")
        return ("story", rest)
    if lower_text.startswith("literal"):
        rest = stripped_text[len("literal") :].lstrip(" :")
        return ("literal", rest)
    return (None, stripped_text)


# =========================
# Conversation helpers
# =========================


def format_status(conv: Dict[str, Any]) -> str:
    data = conv.get("data", {})
    questions = conv.get("questions", [])
    idx = conv.get("index", 0)
    filled = [k for k, v in data.items() if isinstance(v, str) and v.strip()]
    remaining = max(len(questions) - idx, 0)
    mode = conv.get("mode", "story")
    pending = conv.get("pending") or {}
    pending_field = pending.get("field")
    lines = [
        "Status:",
        f"- Filled fields: {len(filled)}",
        f"- Open questions: {remaining}",
        f"- Input mode: {mode}",
    ]
    if pending_field:
        label = DUTCH_FIELD_LABELS.get(pending_field, pending_field)
        lines.append(f"- Waiting for confirmation for: {label}")
    elif remaining > 0 and idx < len(questions):
        total = len(questions)
        lines.append(f"- *Question {idx+1}/{total}* {q_display(questions[idx])}")
    return "\n".join(lines)


def next_step_text(conv: Dict[str, Any]) -> str:
    pending = conv.get("pending") or {}
    if isinstance(pending, dict) and pending.get("field"):
        field = pending["field"]
        label = DUTCH_FIELD_LABELS.get(field, field)
        return (
            f"Waiting for confirmation for {label}. Confirm with `yes`/`ok`, or provide an alternative "
            f"(e.g., `literal ...` or `story ...`)."
        )
    questions = conv.get("questions", [])
    idx = conv.get("index", 0)
    if idx < len(questions):
        total = len(questions)
        return f"*Question {idx+1}/{total}*\n{q_display(questions[idx])}"
    return MSG.no_open_questions_short()


def compute_next_index(conv: Dict[str, Any], start_index: int) -> int:
    questions = conv.get("questions", []) or []
    data = conv.get("data", {}) or {}
    i = max(int(start_index or 0), 0)
    while i < len(questions):
        fq = questions[i].get("field_key") if isinstance(questions[i], dict) else None
        val = data.get(fq)
        if isinstance(val, str) and val.strip():
            i += 1
            continue
        break
    return i


# =========================
# Field key resolution
# =========================


def _build_number_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for key, label in DUTCH_FIELD_LABELS.items():
        num = label.split(" ")[0].strip()
        if num.replace(".", "").isdigit():
            index[num] = key
    return index


_NUMBER_INDEX = _build_number_index()


def resolve_field_key(user_token: str) -> Optional[str]:
    t = user_token.strip().lower()
    if not t:
        return None
    if t in DUTCH_FIELD_LABELS:
        return t
    if t in _NUMBER_INDEX:
        return _NUMBER_INDEX[t]
    for key, label in DUTCH_FIELD_LABELS.items():
        lbl = label.lower()
        if lbl.startswith(t) or t in lbl:
            return key
    for key in DUTCH_FIELD_LABELS.keys():
        if t in key.lower():
            return key
    return None


def format_fields_list(conv: Dict[str, Any]) -> str:
    data = conv.get("data", {})
    lines = ["Fields (use with `edit <field> <value>`):"]
    for key, label in DUTCH_FIELD_LABELS.items():
        value = data.get(key)
        preview = (
            ""
            if not isinstance(value, str)
            else (value.strip()[:80] + ("…" if len(value.strip()) > 80 else ""))
        )
        num = label.split(" ")[0]
        lines.append(f"- {num} | {key}: {preview}")
    return "\n".join(lines)


# =========================
# Markdown/ADF helpers
# =========================


def to_adf(md_text: str) -> Dict[str, Any]:
    lines = (md_text or "").splitlines()
    content: list[Dict[str, Any]] = []
    for line in lines:
        s = line.rstrip("\n")
        if s.strip() == "":
            content.append({"type": "paragraph", "content": []})
            continue
        i = 0
        while i < len(s) and s[i] == "#":
            i += 1
        if i > 0 and i <= 6 and i < len(s) and s[i] == " ":
            heading_text = s[i + 1 :].lstrip()
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": i},
                    "content": [{"type": "text", "text": heading_text}],
                }
            )
        else:
            content.append(
                {"type": "paragraph", "content": [{"type": "text", "text": s}]}
            )
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}


def to_adf_desc(md_text: str) -> Dict[str, Any]:
    return to_adf(md_text)


def _load_usage_text() -> str:
    """Load USAGE.md from well-known locations.

    Prefer a project-root `USAGE.md`. Allow override via USAGE_MD_PATH.
    """
    module_dir = os.path.abspath(os.path.dirname(__file__))
    candidates: list[str] = []
    # Explicit override via env
    override = os.getenv("USAGE_MD_PATH")
    if override:
        candidates.append(override)
    # Project root (src layout → two levels up)
    candidates.append(os.path.abspath(os.path.join(module_dir, "..", "..", "USAGE.md")))
    # Previous relative assumption (kept for compatibility)
    candidates.append(os.path.abspath(os.path.join(module_dir, "..", "USAGE.md")))
    # CWD fallback
    candidates.append(os.path.abspath(os.path.join(os.getcwd(), "USAGE.md")))
    # Common container root path
    candidates.append("/app/USAGE.md")

    for path in candidates:
        try:
            if path and os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logging.warning(f"Error reading USAGE.md candidate {path}: {e}")

    logging.error(
        "Failed to load USAGE.md for App Home: none of the candidate paths exist: %s",
        candidates,
    )
    return "Usage guide unavailable. Make sure USAGE.md exists at the project root."


def _markdown_to_blocks(md: str) -> list[Dict[str, Any]]:
    blocks: list[Dict[str, Any]] = []
    lines = (md or "").splitlines()
    in_code = False
    paragraph_parts: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_parts:
            return
        text = "\n".join(paragraph_parts).strip()
        if not text:
            paragraph_parts.clear()
            return
        max_len = 2900
        start = 0
        while start < len(text):
            chunk = text[start : start + max_len]
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
            )
            start += max_len
        paragraph_parts.clear()

    for raw in lines:
        s = raw.rstrip("\n")
        if s.strip().startswith("```"):
            if in_code:
                paragraph_parts.append("```")
                in_code = False
                flush_paragraph()
            else:
                flush_paragraph()
                in_code = True
                paragraph_parts.append("```")
            continue
        if in_code:
            paragraph_parts.append(s)
            continue
        if s.startswith("#"):
            flush_paragraph()
            i = 0
            while i < len(s) and s[i] == "#":
                i += 1
            heading_text = s[i:].strip()
            formatted = f"*{heading_text}*" if heading_text else ""
            if formatted:
                blocks.append(
                    {"type": "section", "text": {"type": "mrkdwn", "text": formatted}}
                )
            continue
        if s.strip() == "":
            flush_paragraph()
            continue
        paragraph_parts.append(s)

    flush_paragraph()
    return blocks[:100]


def build_home_view() -> Dict[str, Any]:
    md = _load_usage_text()
    blocks: list[Dict[str, Any]] = []
    blocks.append(
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Security Incident Agent — Usage",
                "emoji": True,
            },
        }
    )
    blocks.append({"type": "divider"})
    blocks.extend(_markdown_to_blocks(md))
    return {"type": "home", "blocks": blocks}


# =========================
# Model-assisted drafting helpers
# =========================


def rewrite_with_model(
    extractor: IncidentExtractor,
    raw_text: str,
    field_key: str,
    current_data: Dict[str, Any],
) -> str:
    """
    Rewrite user input into a concise sentence using the model.

    Returns empty string on blank input; returns original on error.
    """
    try:
        label = DUTCH_FIELD_LABELS.get(field_key, field_key)
        sys = MSG.rewriter_system_prompt()
        if not isinstance(raw_text, str) or not raw_text.strip():
            return ""
        messages = [
            {"role": "system", "content": sys},
            {"role": "user", "content": MSG.rewriter_user_prompt(label, raw_text)},
        ]
        completion = extractor.client.chat.completions.create(
            model=extractor.model,
            messages=messages,
        )
        content = completion.choices[0].message.content or ""
        return content.strip()
    except Exception as e:
        logging.error(f"Error rewriting with model: {e}")
        return raw_text


def revise_with_history(
    extractor: IncidentExtractor, field_key: str, history: list[dict], instructions: str
) -> str:
    """
    Use accumulated per-field history to produce a refined draft.
    """
    try:
        if not isinstance(instructions, str) or not instructions.strip():
            for msg in reversed(history):
                if msg.get("role") == "assistant":
                    content = msg.get("content") or ""
                    return content.strip()
            return ""
        sys = MSG.revision_system_prompt()
        messages = [{"role": "system", "content": sys}]
        for m in history:
            role = m.get("role") or "user"
            content = (m.get("content") or "").strip()
            if not content:
                continue
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": instructions.strip()})
        completion = extractor.client.chat.completions.create(
            model=extractor.model,
            messages=messages,
        )
        content = completion.choices[0].message.content or ""
        return content.strip()
    except Exception as e:
        logging.error(f"Error revising with history: {e}")
        return instructions


def set_pending_with_history(
    conv: Dict[str, Any], field: str, user_text: str, draft_value: str
) -> None:
    """
    Initialize/reset the pending structure for a field with history.
    """
    history = [
        {"role": "user", "content": user_text or ""},
        {"role": "assistant", "content": draft_value or ""},
    ]
    conv["pending"] = {"field": field, "candidate": draft_value, "history": history}


def propose_confirmation_for_field(
    extractor: IncidentExtractor,
    conv: Dict[str, Any],
    field: str,
    thread_ts: str,
    say,
) -> None:  # type: ignore
    """
    Propose a reformulated value for a field and request user confirmation.
    """
    current_value = conv.get("data", {}).get(field, "")
    mode_to_use = conv.get("mode", "story")
    value = (
        rewrite_with_model(extractor, str(current_value), field, conv.get("data", {}))
        if isinstance(current_value, str)
        and current_value.strip()
        and mode_to_use == "story"
        else str(current_value)
    )
    set_pending_with_history(conv, field, str(current_value), value)
    label = DUTCH_FIELD_LABELS.get(field, field)
    say(
        text=MSG.proposal(label, value),
        thread_ts=thread_ts,
    )
