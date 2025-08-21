from __future__ import annotations

import os
import logging
from typing import Any, Dict, Tuple, Optional

from dotenv import load_dotenv
from slack_bolt import App as SlackApp
from slack_bolt.adapter.socket_mode import SocketModeHandler

from incident_agent.extract import IncidentExtractor
from incident_agent.render import render_markdown
from incident_agent.jira_client import JiraClient
from incident_agent.schema import IncidentTemplate, DUTCH_FIELD_LABELS


# In-memory state: keyed by (channel, thread_ts)
ConversationKey = Tuple[str, str]
state: Dict[ConversationKey, Dict[str, Any]] = {}


def build_slack_app() -> SlackApp:
    """
    Build and configure the Slack Bolt app with event handlers and conversation flow.

    @return SlackApp: Configured Slack Bolt application ready for Socket Mode handler.
    """
    app = SlackApp(token=os.environ.get("SLACK_BOT_TOKEN"))
    extractor = IncidentExtractor()

    def _q_text(q: Any) -> str:
        """
        Resolve the human-readable question text for a question object/dict.

        @param q: Question, either a dict with keys like 'field_key'/'question_text' or a model.
        @return str: Display text for the question.
        """
        if isinstance(q, dict):
            field_for_q = str(q.get("field_key", ""))
            if field_for_q == "risicoafweging":
                return "Should a risk assessment be made: yes / no"
            return str(q.get("question_text", ""))
        field_for_q = str(getattr(q, "field_key", ""))
        if field_for_q == "risicoafweging":
            return "Should a risk assessment be made: yes / no"
        return str(getattr(q, "question_text", ""))

    def _q_field(q: Any) -> str:
        """
        Get the field key for a question object/dict.

        @param q: Question, either a dict or model with 'field_key'.
        @return str: Field key string (may be empty).
        """
        if isinstance(q, dict):
            return str(q.get("field_key", ""))
        return str(getattr(q, "field_key", ""))

    def _q_number(q: Any) -> str:
        """
        Extract the numeric prefix from the localized label (e.g., "2.1").

        @param q: Question reference used to resolve the field key.
        @return str: Number token if present, otherwise empty string.
        """
        field_key = _q_field(q)
        label = DUTCH_FIELD_LABELS.get(field_key, field_key)
        num = label.split(" ")[0].strip()
        return num if num.replace(".", "").isdigit() else ""

    def _q_display(q: Any) -> str:
        """
        Build a display string for the question with optional numeric prefix.

        @param q: Question object/dict.
        @return str: Display string suitable for Slack messages.
        """
        num = _q_number(q)
        text = _q_text(q)
        return f"{num}: {text}" if num else text

    def _is_accept(text: str) -> bool:
        """
        Check if text indicates acceptance/confirmation.

        @param text: User-provided raw text.
        @return bool: True if the text means accept/confirm.
        """
        t = text.strip().lower()
        return t in {"ja", "ok", "okay", "akkoord", "yes", "y", "accept"}

    def _is_yes(text: str) -> bool:
        """
        Check if text indicates a yes.

        @param text: User-provided raw text.
        @return bool: True if interpreted as yes.
        """
        t = text.strip().lower()
        return t in {"ja", "yes", "y"}

    def _is_no(text: str) -> bool:
        """
        Check if text indicates a no.

        @param text: User-provided raw text.
        @return bool: True if interpreted as no.
        """
        t = text.strip().lower()
        return t in {"nee", "no", "n"}

    def _format_status(conv: Dict[str, Any]) -> str:
        """
        Create a user-facing status summary for the current conversation.

        @param conv: Conversation state dict containing data/questions/index/mode/pending.
        @return str: Multiline summary.
        """
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
            lines.append(f"- Next question: {_q_display(questions[idx])}")
        return "\n".join(lines)

    def _next_step_text(conv: Dict[str, Any]) -> str:
        """
        Compute the next actionable instruction for the user in the thread.

        @param conv: Conversation state dict.
        @return str: Message indicating what the user should do next.
        """
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
            return f"Next question: {_q_display(questions[idx])}"
        return "No open questions left. Send `finalize` to finish."

    def _compute_next_index(conv: Dict[str, Any], start_index: int) -> int:
        """
        Compute the next unanswered question index starting from a position.

        @param conv: Conversation state dict with 'questions' and filled 'data'.
        @param start_index: Index to start scanning from.
        @return int: Index of the next unanswered question.
        """
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

    def _build_number_index() -> Dict[str, str]:
        """
        Build a mapping from numeric label prefixes (e.g., "2.1") to field keys.

        @return Dict[str, str]: Map of number token to field key.
        """
        index: Dict[str, str] = {}
        for key, label in DUTCH_FIELD_LABELS.items():
            num = label.split(" ")[0].strip()
            if num.replace(".", "").isdigit():
                index[num] = key
        return index

    NUMBER_INDEX = _build_number_index()

    def _resolve_field_key(user_token: str) -> Optional[str]:
        """
        Resolve a user-entered token to a field key by key, number, or label text.

        @param user_token: User-supplied token (key, number like 2.1, or label fragment).
        @return Optional[str]: Matching field key, or None if not found.
        """
        t = user_token.strip().lower()
        if not t:
            return None
        # Exact key
        if t in DUTCH_FIELD_LABELS:
            return t
        # Exact number like 2.1 or 3.4
        if t in NUMBER_INDEX:
            return NUMBER_INDEX[t]
        # Match by label prefix/contains
        for key, label in DUTCH_FIELD_LABELS.items():
            lbl = label.lower()
            if lbl.startswith(t) or t in lbl:
                return key
        # Fuzzy contains on key
        for key in DUTCH_FIELD_LABELS.keys():
            if t in key.lower():
                return key
        return None

    def _format_fields_list(conv: Dict[str, Any]) -> str:
        """
        Produce a list of fields and current short previews to assist editing.

        @param conv: Conversation state with current 'data'.
        @return str: Multiline list of fields with preview text.
        """
        data = conv.get("data", {})
        lines = ["Fields (use with `edit <field> <value>`):"]
        for key, label in DUTCH_FIELD_LABELS.items():
            value = data.get(key)
            preview = "" if not isinstance(value, str) else (value.strip()[:80] + ("…" if len(value.strip()) > 80 else ""))
            num = label.split(" ")[0]
            lines.append(f"- {num} | {key}: {preview}")
        return "\n".join(lines)

    def _rewrite_with_model(raw_text: str, field_key: str, current_data: Dict[str, Any]) -> str:
        """
        Rewrite user input into a concise, clean English sentence using the model.

        @param raw_text: Original user text.
        @param field_key: Field key the text belongs to.
        @param current_data: Current conversation data for context (not used for sourcing).
        @return str: Reformulated text; returns original on error or empty if input blank.
        """
        try:
            label = DUTCH_FIELD_LABELS.get(field_key, field_key)
            sys = (
                "You are an assistant that turns short notes into a clear, professional, concise text in English. "
                "Strict rules: (1) Use ONLY the 'Input' section as the source. Do NOT use any other context. "
                "(2) Do NOT add facts or details that are not explicitly in the input (no hallucinations). "
                "(3) Rewrite for clarity/readability; reformulate into well-formed sentences; do not change content."
                "(4) Do not enrich the output with additional information. "
                "(5) Return only the reformulated text, without extra explanations. "
                "(6) Do not repeat the field name in the output."
                "(7) Make sure you retain all relevant facts."
                "Example: dataleak in db -> There is a data leak in the database."
            )
            # Als de input leeg of whitespace is, geef leeg terug
            if not isinstance(raw_text, str) or not raw_text.strip():
                return ""
            messages = [
                {"role": "system", "content": sys},
                {
                    "role": "user",
                    "content": (
                        "Rewrite only the text under 'Input'. Do NOT use any other source. "
                        "If the input carries little information, keep the output equally minimal.\n\n"
                        f"Field: '{label}'\n"
                        f"Input (only source):\n{raw_text}"
                    ),
                },
            ]
            print(messages)
            completion = extractor.client.chat.completions.create(
                model=extractor.model,
                messages=messages,
            )
            content = completion.choices[0].message.content or ""
            return content.strip()
        except Exception as e:
            logging.error(f"Error rewriting with model: {e}")     
            return raw_text

    def _parse_mode_prefix(text: str) -> tuple[Optional[str], str]:
        """
        Parse optional 'story'/'literal' prefix from a user message.

        @param text: Raw user input.
        @return tuple[Optional[str], str]: (forced_mode or None, remaining_text).
        """
        s = text.strip()
        l = s.lower()
        if l.startswith("story"):
            rest = s[len("story"):].lstrip(" :")
            return ("story", rest)
        if l.startswith("literal"):
            rest = s[len("literal"):].lstrip(" :")
            return ("literal", rest)
        return (None, s)

    def _propose_confirmation_for_field(conv: Dict[str, Any], field: str, thread_ts: str, say) -> None:  # type: ignore
        """
        Propose a reformulated value for a field and request user confirmation.

        @param conv: Conversation state dict (mutated with 'pending').
        @param field: Field key to confirm.
        @param thread_ts: Slack thread timestamp to reply in.
        @param say: Slack 'say' function used to send messages.
        @return None: This function sends a message and mutates state.
        """
        current_value = conv.get("data", {}).get(field, "")
        mode_to_use = conv.get("mode", "story")
        value = (
            _rewrite_with_model(str(current_value), field, conv.get("data", {}))
            if isinstance(current_value, str) and current_value.strip() and mode_to_use == "story"
            else str(current_value)
        )
        conv["pending"] = {"field": field, "candidate": value}
        label = DUTCH_FIELD_LABELS.get(field, field)
        say(
            text=(
                f"Proposal for {label}:\n{value}\n\nConfirm with `yes`/`ok`, or provide an alternative via `new <value>` (optional `new literal:` or `new story:`)."
            ),
            thread_ts=thread_ts,
        )

    HELP_TEXT = (
        "Available commands (DM):\n"
        "- help: show this help\n"
        "- status: progress\n"
        "- show: show current Markdown\n"
        "- jira: create a Jira issue from the current Markdown\n"
        "- fields: show field names and numbers\n"
        "- edit <field> <value>: change a previous answer (field = key, number, or label text)\n"
        "- mode literal|story: set the input mode\n"
        "- showmode: show current input mode\n"
        "- continue: show the next question again\n"
        "- new <value>: change the current proposal (optionally prefixed with `story:` or `literal:`)\n"
        "- cancel: cancel this incident thread\n\n"
        "Tip: prefix `story ...` or `story: ...` or `literal ...` for a one-time choice per answer.\n"
        "Start: type `start` to begin, then provide a short description.\n"
        "Finish: type `finalize` in the thread.\n"
        "Create an issue: type `jira` in the thread.\n"
        "After each answer we show a proposal; confirm with `yes`/`ok` or provide an alternative via `new <value>` (optional `new literal:` or `new story:`)."
    )

    PREFACE_TEXT = (
        "*Dealing with security incidents* (e.g. data leak)\n"
        "A security incident can happen with any project at any time. When a security incident occurs, the reporter should take the steps outlines below.\n"
        "I can assist you with creating the incident report (step 7). Please start with the first six steps yourself. Once you are ready to start the report, type `start` to start filling out the report\n\n"
        "*Steps*\n"
        "_Steps preceding the incident report_\n"
        "1. Contact the appropriate internal product owner\n"
        "2. Create an issue on the appropriate Jira board and label it “incident” under security.\n"
        "3. Get approval for containing the incident from the product owner. Make sure the product owner understands the implications of containing the incident. Consider the following options:\n"
        "  a) If it is a user, block the user.\n"
        "  b) If it is a connection, block the connection/disconnect\n"
        "  c) If it is an API key or certificate, revoke it.\n"
        "  d) If it is an environment or server, shut it down.\n"
        "4. Secure the data, e.g., make additional copies of logs, databases, etc.\n"
        "5. Fix the problem. If you can fix it, fix it.\n"
        "6. Assess the damage\n"
        "  a) Has personal data been leaked?\n"
        "  b) Has confidential data been leaked?\n\n"
        "7. Write a report: type `start` to start filling out the report\n\n"
        "_Steps following the incident report_\n"
        "8. Any resulting issues like tasks and user stories or documents should be linked to the original issue to preserve a chain of events.\n"
        "9. The product owner will determine additional actions, like\n"
        "  a) Preventive measures for the future\n"
        "  b) Changes in manuals and processes\n"
        "  c) Contact with the client\n"
        "  d) Reporting the incident to authorities\n"
        "10. Security incidents will ALWAYS be shown in the Kwaliteit/veiligheidsdashboard and reviewed every LT meeting (Weekly).\n"
        "11. A final review will be planned in the first LT kwaliteit/veiligheid meeting to come after three months.\n"
        "12. Evaluation After Incidents Involving External Vendors: "
        "After the resolution of an incident in which an external vendor (such as a hosting partner) played a role in the cause or impact, Conduction conducts an evaluation meeting with the client."
        "During this meeting, the cause, impact, communication, responsibilities, and possible next steps are discussed."
        "If the client requires a detailed advisory report or redesign proposal, a confirmation of assignment or quotation will be prepared accordingly.\n"
        "\n"
        "*Data Security Contact Person*\n"
        "Ruben is our designated Data Security Officer. If you encounter any incidents or have questions related to data security, he is your go-to person. Please feel free to reach out to him directly for support, guidance, or to report any concerns.\n\n"
        "*Fill out the report here*\n"
        "Once you have completed steps 1-6, type `start` to start filling out the report. After you have answered all the questions, I will create a Jira issue for you."
    )

    FOLLOWUP_STEPS_TEXT = (
        "*Reminder: Steps following the incident report*\n"
        "8. Any resulting issues like tasks and user stories or documents should be linked to the original issue to preserve a chain of events.\n"
        "9. The product owner will determine additional actions, like\n"
        "  a) Preventive measures for the future\n"
        "  b) Changes in manuals and processes\n"
        "  c) Contact with the client\n"
        "  d) Reporting the incident to authorities\n"
        "10. Security incidents will ALWAYS be shown in the Kwaliteit/veiligheidsdashboard and reviewed every LT meeting (Weekly).\n"
        "11. A final review will be planned in the first LT kwaliteit/veiligheid meeting to come after three months.\n"
        "12. Evaluation After Incidents Involving External Vendors: After the resolution of an incident in which an external vendor (such as a hosting partner) played a role in the cause or impact, Conduction conducts an evaluation meeting with the client.\n"
        "During this meeting, the cause, impact, communication, responsibilities, and possible next steps are discussed.\n"
        "If the client requires a detailed advisory report or redesign proposal, a confirmation of assignment or quotation will be prepared accordingly."
    )

    WELCOME_TEXT = (
        "Welcome! I will help you quickly and systematically record a security incident.\n\n"
        "*How it works*:\n"
        "I will ask short questions so we can complete the incident template together and create a Jira issue.\n\n"
        "*Mode*:\n"
        "We are in `story` mode: I rewrite your answers into short, clean sentences. In this mode I will first show each rewritten text for your confirmation.\n"
        "If you want me to take your words literally, switch to `literal` mode with `mode literal` or use a one-off `literal: <answer>`. In `literal` mode your answer is taken as-is without confirmation.\n\n"
        "*Options*:\n"
        "- `edit`: change previously filled fields with `edit <field> <value>`; for example `edit 2.1 email data leak`\n"
        "- `show`: show the current Markdown\n"
        "- `status`: show progress\n"
        "- `fields`: show available field names and numbers\n"
        "- `mode literal|story`: switch input mode\n"
        "- `finalize`: receive the final document\n"
        "- `jira`: create a Jira issue with the final document as description (and as .md attachment)\n\n"
    )


    def _load_usage_text() -> str:
        """
        Load `USAGE.md` content for the App Home view.

        @return str: Markdown content or a fallback message if unavailable.
        """
        try:
            # Load USAGE.md from the same directory as this file
            module_dir = os.path.abspath(os.path.dirname(__file__))
            usage_path = os.path.join(module_dir, "USAGE.md")
            with open(usage_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logging.error(f"Failed to load USAGE.md for App Home: {e}")
            return (
                "Usage guide unavailable. Make sure USAGE.md exists at the project root."
            )

    def _markdown_to_blocks(md: str) -> list[Dict[str, Any]]:
        """
        Convert a markdown string into Slack Block Kit sections, chunked for limits.

        @param md: Markdown input.
        @return list[Dict[str, Any]]: List of Block Kit block dicts.
        """
        # Simplistic Markdown → Block Kit conversion with chunking
        # - Convert headings (#/##/###/####) to bold lines
        # - Preserve fenced code blocks
        # - Group paragraphs into sections, split to <= 2900 chars
        blocks: list[Dict[str, Any]] = []
        lines = (md or "").splitlines()
        in_code = False
        paragraph_parts: list[str] = []

        def flush_paragraph() -> None:
            """
            Flush accumulated paragraph lines into one or more Slack section blocks.

            @return None: Mutates outer 'blocks' with new sections.
            """
            if not paragraph_parts:
                return
            text = "\n".join(paragraph_parts).strip()
            if not text:
                paragraph_parts.clear()
                return
            # Split into chunks to respect Slack section text limit (~3000)
            max_len = 2900
            start = 0
            while start < len(text):
                chunk = text[start : start + max_len]
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": chunk},
                })
                start += max_len
            paragraph_parts.clear()

        for raw in lines:
            s = raw.rstrip("\n")
            if s.strip().startswith("```"):
                if in_code:
                    # Close code block
                    paragraph_parts.append("```")
                    in_code = False
                    flush_paragraph()
                else:
                    # Start code block
                    flush_paragraph()
                    in_code = True
                    paragraph_parts.append("```")
                continue
            if in_code:
                paragraph_parts.append(s)
                continue

            # Headings
            if s.startswith("#"):
                flush_paragraph()
                i = 0
                while i < len(s) and s[i] == '#':
                    i += 1
                heading_text = s[i:].strip()
                formatted = f"*{heading_text}*" if heading_text else ""
                if formatted:
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": formatted},
                    })
                continue

            # Blank line flushes current paragraph
            if s.strip() == "":
                flush_paragraph()
                continue

            # Regular text
            paragraph_parts.append(s)

        flush_paragraph()
        # Cap to 100 blocks to respect Slack limits
        return blocks[:100]

    def _build_home_view() -> Dict[str, Any]:
        """
        Build the App Home view payload with content from `USAGE.md`.

        @return Dict[str, Any]: Slack view payload for the home tab.
        """
        md = _load_usage_text()
        blocks: list[Dict[str, Any]] = []
        blocks.append({
            "type": "header",
            "text": {"type": "plain_text", "text": "Security Incident Agent — Usage", "emoji": True},
        })
        blocks.append({"type": "divider"})
        blocks.extend(_markdown_to_blocks(md))
        return {"type": "home", "blocks": blocks}

    @app.event("app_home_opened")
    def update_app_home(event, client, logger):  # type: ignore
        """
        Event handler for `app_home_opened`; publishes the home view to the user.

        @param event: Slack event payload.
        @param client: Slack WebClient.
        @param logger: Logger instance.
        @return None: Sends the view; logs on error.
        """
        try:
            user_id = event.get("user")
            if not user_id:
                return
            logger.info(f"[incident-bot] app_home_opened by user={user_id}")
            view = _build_home_view()
            client.views_publish(user_id=user_id, view=view)
        except Exception as e:
            logger.error(f"Failed to publish App Home: {e}")


    @app.event("message")
    def handle_message_events(body, say, event, logger, client):  # type: ignore
        """
        Handle direct message events to guide the incident reporting flow.

        @param body: Full request body.
        @param say: Slack responder function for posting messages.
        @param event: Slack event dict containing the message.
        @param logger: Logger instance.
        @param client: Slack WebClient.
        @return None: Manages conversation state and responds in-thread.
        """
        # Ignore bot messages to avoid loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        channel_type = event.get("channel_type")  # only handle DMs
        if channel_type != "im":
            return

        channel = str(event.get("channel"))
        has_thread = bool(event.get("thread_ts"))
        root_ts = str(event.get("thread_ts") or event.get("ts"))

        conv = state.get((channel, root_ts))

        text_raw = (event.get("text") or "").strip()
        text = text_raw.lower()

        # In new DM messages (no thread), do not map to old threads and do not process commands:
        # every new message starts its own conversation.

        # If no conversation context found in DM
        if not conv:
            # If user types 'start' as a thread reply, initialize and start at question 1
            if has_thread and text in {"start", "/start"}:
                say(text=WELCOME_TEXT, thread_ts=root_ts)
                result = extractor.extract("")
                state[(channel, root_ts)] = {
                    "data": result.data.model_dump(),
                    "questions": [q.model_dump() for q in result.questions],
                    "index": 0,
                    "status": "collecting",
                    "mode": "story",
                    "pending": None,
                    "confirm_action": None,
                }
                if result.questions:
                    say(text=f"First question: {_q_display(result.questions[0])}", thread_ts=root_ts)
                else:
                    say(text="No open questions. Send `finalize` to finish.", thread_ts=root_ts)
                return
            # In a DM without a thread: always start a new incident anchored to this message
            if not has_thread and text_raw:
                print(f"[incident-bot] New DM conversation: channel={channel} root_ts={root_ts} text={text_raw!r}")
                # Show preface first. Only start after user types 'start'.
                say(text=PREFACE_TEXT, thread_ts=root_ts)
                if text in {"start", "/start"}:
                    say(text=WELCOME_TEXT, thread_ts=root_ts)
                    result = extractor.extract("")
                    state[(channel, root_ts)] = {
                        "data": result.data.model_dump(),
                        "questions": [q.model_dump() for q in result.questions],
                        "index": 0,
                        "status": "collecting",
                        "mode": "story",
                        "pending": None,
                        "confirm_action": None,
                    }
                    if result.questions:
                        say(text=f"First question: {_q_display(result.questions[0])}", thread_ts=root_ts)
                    else:
                        say(text="No open questions. Send `finalize` to finish.", thread_ts=root_ts)
                    return
                return
            # Otherwise guide the user
            if not has_thread:
                say(text=PREFACE_TEXT, thread_ts=root_ts)
                say(text="Type `start` to begin.", thread_ts=root_ts)
            return

        # If we are awaiting a confirmation to proceed with a risky action (finalize/jira)
        confirm_action = conv.get("confirm_action")
        if confirm_action in {"finalize", "jira"}:
            if _is_accept(text_raw):
                # User confirmed to proceed; clear flag and continue as if they typed the action again
                conv["confirm_action"] = None
                conv["override_incomplete"] = True
                text = confirm_action
            elif _is_no(text_raw):
                conv["confirm_action"] = None
                say(text="Okay, not proceeding. " + _next_step_text(conv), thread_ts=root_ts)
                return
            else:
                say(text=(
                    "Please answer `yes` to proceed or `no` to cancel. "
                    + _next_step_text(conv)
                ), thread_ts=root_ts)
                return

        # Thread-level commands
        if text in {"help", "/help"}:
            say(text=HELP_TEXT, thread_ts=root_ts)
            return
        if text in {"finaliseer", "finaliseren", "finalize", "finaliseren aub", "finalise"}:
            try:
                questions = conv.get("questions", [])
                pending = conv.get("pending") or {}
                has_pending = isinstance(pending, dict) and pending.get("field")
                incomplete = bool(has_pending) or (conv.get("index", 0) < len(questions))
                if incomplete and not conv.pop("override_incomplete", False):
                    conv["confirm_action"] = "finalize"
                    say(
                        text=(
                            "Warning: You have not answered all the questions yet. "
                            "Are you sure you want to finalize? Reply `yes` to proceed or `no` to continue."
                        ),
                        thread_ts=root_ts,
                    )
                    return
                md = render_markdown(IncidentTemplate(**conv.get("data", {})))
                say(text=f"Final document:\n```\n{md}\n```", thread_ts=root_ts)
            except Exception as e:
                say(text=f"Could not generate the final document: {e}", thread_ts=root_ts)
            return
        # (Removed) update existing Jira issue flow per request

        if text in {"jira", "/jira"}:
            try:
                questions = conv.get("questions", [])
                pending = conv.get("pending") or {}
                has_pending = isinstance(pending, dict) and pending.get("field")
                incomplete = bool(has_pending) or (conv.get("index", 0) < len(questions))
                if incomplete and not conv.pop("override_incomplete", False):
                    conv["confirm_action"] = "jira"
                    say(
                        text=(
                            "Warning: You have not answered all the questions yet. "
                            "Are you sure you want to create a Jira issue? Reply `yes` to proceed or `no` to continue."
                        ),
                        thread_ts=root_ts,
                    )
                    return
                # Build full markdown and per-section texts for field mapping
                template = IncidentTemplate(**conv.get("data", {}))
                md = render_markdown(template)
                d = template.model_dump()

                def val(key: str) -> str:
                    """
                    Helper to retrieve a trimmed string value from the template data.

                    @param key: Field key in the template data.
                    @return str: Trimmed value or empty string.
                    """
                    v = d.get(key)
                    return v.strip() if isinstance(v, str) else ""

                # Description: Section 1 only (optionally include heading for clarity)
                desc_text_lines = []
                if val("beschrijving_afwijking"):
                    desc_text_lines.append("# 1. Beschrijving afwijking")
                    desc_text_lines.append(val("beschrijving_afwijking"))
                description_text = "\n".join(desc_text_lines) if desc_text_lines else ""

                # Section 2 → customfield_10061
                sec2_lines = []
                if any(val(k) for k in [
                    "maatregelen_beheersen_corrigeren",
                    "aanpassen_consequenties",
                    "risicoafweging",
                ]):
                    sec2_lines.append("# 2. Measures")
                    if val("maatregelen_beheersen_corrigeren"):
                        sec2_lines.append("## 2.1 Measures to control and correct the deviation")
                        sec2_lines.append(val("maatregelen_beheersen_corrigeren"))
                    if val("aanpassen_consequenties"):
                        sec2_lines.append("## 2.2 Adjust consequences")
                        sec2_lines.append(val("aanpassen_consequenties"))
                    if val("risicoafweging"):
                        sec2_lines.append("## 2.3 Risk assessment If the deviation is of such a nature, a risk assessment must be made. Contact Mark, holder of the risk inventory")
                        sec2_lines.append(val("risicoafweging"))
                sec2_text = "\n".join(sec2_lines)

                # Section 3 → customfield_10062
                sec3_lines = []
                if any(val(k) for k in [
                    "oorzaak_ontstaan",
                    "gevolgen",
                    "oorzaak_wegnemen",
                    "elders_voorgedaan",
                    "acties_elders",
                ]):
                    sec3_lines.append("# 3. Analysis and removing causes")
                    if val("oorzaak_ontstaan"):
                        sec3_lines.append("## 3.1 Cause of the deviation")
                        sec3_lines.append(val("oorzaak_ontstaan"))
                    if val("gevolgen"):
                        sec3_lines.append("## 3.2 Consequences of the deviation")
                        sec3_lines.append(val("gevolgen"))
                    if val("oorzaak_wegnemen"):
                        sec3_lines.append("## 3.3 Remove cause")
                        sec3_lines.append(val("oorzaak_wegnemen"))
                    if val("elders_voorgedaan"):
                        sec3_lines.append("## 3.4 Could the deviation have occurred elsewhere")
                        sec3_lines.append(val("elders_voorgedaan"))
                    if val("acties_elders"):
                        sec3_lines.append("## 3.5 Actions on deviation that occurred elsewhere")
                        sec3_lines.append(val("acties_elders"))
                sec3_text = "\n".join(sec3_lines)

                # Section 4 → customfield_10063
                sec4_lines = []
                if any(val(k) for k in [
                    "doeltreffendheid",
                    "actualisatie_risico",
                    "aanpassing_kwaliteitssysteem",
                ]):
                    sec4_lines.append("# 4. Assessment of measures taken This chapter will be filled once the JIRA actions are completed.")
                    if val("doeltreffendheid"):
                        sec4_lines.append("## 4.1 Effectiveness of the measures taken")
                        sec4_lines.append(val("doeltreffendheid"))
                    if val("actualisatie_risico"):
                        sec4_lines.append("## 4.2 Update of risk inventory based on deviation (if applicable)")
                        sec4_lines.append(val("actualisatie_risico"))
                    if val("aanpassing_kwaliteitssysteem"):
                        sec4_lines.append("## 4.3 Adjustment to quality system (if applicable)")
                        sec4_lines.append(val("aanpassing_kwaliteitssysteem"))
                sec4_text = "\n".join(sec4_lines)

                # Helper: convert text with Markdown-style headings into ADF (heading/paragraph)
                def _to_adf(md_text: str) -> Dict[str, Any]:
                    """
                    Convert a lightweight Markdown string into minimal Jira ADF document.

                    @param md_text: Markdown-like text supporting headings (#) and paragraphs.
                    @return Dict[str, Any]: ADF document dict with content nodes.
                    """
                    lines = (md_text or "").splitlines()
                    content: list[Dict[str, Any]] = []
                    for line in lines:
                        s = line.rstrip("\n")
                        if s.strip() == "":
                            content.append({"type": "paragraph", "content": []})
                            continue
                        i = 0
                        while i < len(s) and s[i] == '#':
                            i += 1
                        if i > 0 and i <= 6 and i < len(s) and s[i] == ' ':
                            heading_text = s[i + 1 :].lstrip()
                            content.append({
                                "type": "heading",
                                "attrs": {"level": i},
                                "content": [{"type": "text", "text": heading_text}],
                            })
                        else:
                            content.append({
                                "type": "paragraph",
                                "content": [{"type": "text", "text": s}],
                            })
                    if not content:
                        content = [{"type": "paragraph", "content": []}]
                    return {"type": "doc", "version": 1, "content": content}

                # Create Jira issue with mapped fields
                jc = JiraClient()

                extra_fields: Dict[str, Any] = {}
                if sec2_text:
                    extra_fields["customfield_10061"] = _to_adf(sec2_text)
                if sec3_text:
                    extra_fields["customfield_10062"] = _to_adf(sec3_text)
                if sec4_text:
                    extra_fields["customfield_10063"] = _to_adf(sec4_text)

                issue = jc.create_issue(summary="Security incident", description=description_text or "", extra_fields=extra_fields or None)
                key = issue.get("key") or issue.get("id") or "(unknown)"
                try:
                    jc.attach_markdown(str(key), "incident.md", md)
                except Exception:
                    pass
                say(text=f"Jira issue created: {key}", thread_ts=root_ts)
            except Exception as e:
                say(text=f"Could not create Jira issue: {e}", thread_ts=root_ts)
            return
        if text in {"status", "/status"}:
            say(text=_format_status(conv), thread_ts=root_ts)
            say(text=_next_step_text(conv), thread_ts=root_ts)
            return
        if text in {"fields", "/fields", "velden", "/velden"}:
            say(text=_format_fields_list(conv), thread_ts=root_ts)
            say(text=_next_step_text(conv), thread_ts=root_ts)
            return
        if text in {"show", "/show", "toon", "/toon", "preview", "/preview"}:
            md = render_markdown(IncidentTemplate(**conv.get("data", {})))
            say(text=f"Current Markdown:\n```\n{md}\n```", thread_ts=root_ts)
            say(text=_next_step_text(conv), thread_ts=root_ts)
            return
        if text in {"continue", "/continue", "verder", "/verder"}:
            say(text=_next_step_text(conv), thread_ts=root_ts)
            return
        if text.startswith("mode ") or text.startswith("/mode "):
            try:
                parts = text.split(" ", 1)
                choice = parts[1].strip().lower()
                if choice not in {"literal", "story"}:
                    raise ValueError
                conv["mode"] = choice
                say(text=f"Input mode set to: {choice}", thread_ts=root_ts)
                # After confirming mode change, show the next question
                say(text=_next_step_text(conv), thread_ts=root_ts)
            except Exception:
                say(text="Usage: mode literal|story", thread_ts=root_ts)
            return
        if text in {"showmode", "/showmode"}:
            say(text=f"Current input mode: {conv.get('mode', 'story')}", thread_ts=root_ts)
            return
        if text.startswith("edit ") or text.startswith("/edit ") or text.startswith("wijzig "):
            # edit <field> <value>  (accept optional 'story'/'literal' prefixes with space or colon)
            try:
                parts = text_raw.split(" ", 2)
                if len(parts) < 3:
                    raise ValueError
                _, field_token, new_value = parts
                key = _resolve_field_key(field_token)
                if not key:
                    say(text=f"Unknown field `{field_token}`. Use `fields` to see available fields.", thread_ts=root_ts)
                    return
                forced, nv_body = _parse_mode_prefix(new_value)
                mode_to_use = forced or conv.get("mode", "story")
                if mode_to_use == "story":
                    value = _rewrite_with_model(nv_body, key, conv["data"])  # propose and confirm
                    conv["pending"] = {"field": key, "candidate": value}
                    label = DUTCH_FIELD_LABELS.get(key, key)
                    say(text=(
                        f"Proposal for {label}:\n{value}\n\nConfirm with `yes`/`ok`, or provide an alternative via `new <value>` (optional `new literal:` or `new story:`)."
                    ), thread_ts=root_ts)
                else:
                    conv["data"][key] = nv_body
                    say(text=f"Changed: {key}", thread_ts=root_ts)
            except Exception:
                say(text="Usage: edit <field> <value>. Example: edit beschrijving_afwijking story: There is a data leak...", thread_ts=root_ts)
            return
        if text in {"cancel", "/cancel"}:
            state.pop((channel, root_ts), None)
            say(text="Incident canceled. Send a new description to start again.", thread_ts=root_ts)
            return

        # Pending confirmation flow (only for story mode usage)
        pending = conv.get("pending")
        if pending and isinstance(pending, dict) and pending.get("field"):
            field = pending["field"]
            label = DUTCH_FIELD_LABELS.get(field, field)
            # For risicoafweging: if we don't have detail yet (candidate is empty or just yes), do not allow acceptance
            if field == "risicoafweging":
                _cand_now = (pending.get("candidate") or "").strip().lower()
                if _cand_now in {"", "ja", "yes"} and _is_accept(text_raw):
                    say(text=(
                        "You answered `yes`. Now provide the outcome/explanation, for example: `Agreed that ...`."
                    ), thread_ts=root_ts)
                    return
            if _is_accept(text_raw):
                # Commit candidate and move on
                conv["data"][field] = pending.get("candidate", "")
                conv["pending"] = None
                # Advance to next question
                idx = conv.get("index", 0)
                questions = conv.get("questions", [])
                # After confirming an auto-filled field, handle the autofill queue then proceed
                queue = conv.get("autofill_queue") or []
                # Remove the just-confirmed field from the queue
                conv["autofill_queue"] = [f for f in queue if f != field]
                if conv["autofill_queue"]:
                    _propose_confirmation_for_field(conv, conv["autofill_queue"][0], root_ts, say)
                    return
                # Otherwise proceed with the next unanswered question index
                next_idx = _compute_next_index(conv, idx)
                conv["index"] = next_idx
                if next_idx < len(questions):
                    next_q = questions[next_idx]
                    say(text=f"Confirmed. Next question: {_q_display(next_q)}", thread_ts=root_ts)
                else:
                    say(text="Confirmed. All questions answered. Send `finalize` to finish.", thread_ts=root_ts)
                return
            else:
                # Special case: for risicoafweging, after a 'yes' we expect an additional detail.
                # If no meaningful candidate yet ("" or "ja"/"yes"), treat this message as the detail
                if field == "risicoafweging":
                    cand_now = (pending.get("candidate") or "").strip().lower()
                    if cand_now in {"", "ja", "yes"}:
                        forced, detail_body = _parse_mode_prefix(text_raw)
                        mode_to_use = forced or conv.get("mode", "story")
                        detail_value = _rewrite_with_model(detail_body, field, conv["data"]) if mode_to_use == "story" else detail_body
                        combined = f"yes: {detail_value}"
                        conv["pending"] = {"field": field, "candidate": combined}
                        say(text=(
                            f"Proposal for {label}:\n{combined}\n\nConfirm with `yes`/`ok`, or provide an alternative via `new <value>` (optional `new literal:` or `new story:`)."
                        ), thread_ts=root_ts)
                        return
                # Only accept 'new <waarde>' to change proposal; otherwise repeat current proposal
                lower = text.strip().lower()
                if lower.startswith("new ") or lower == "new":
                    new_body = text_raw.split(" ", 1)[1] if " " in text_raw else ""
                    forced, new_value_body = _parse_mode_prefix(new_body)
                    mode_to_use = forced or conv.get("mode", "story")
                    if mode_to_use == "literal":
                        # Commit literal immediately, no confirmation (forced or current mode)
                        conv["data"][field] = new_value_body
                        conv["pending"] = None
                        idx = conv.get("index", 0)
                        questions = conv.get("questions", [])
                        queue = conv.get("autofill_queue") or []
                        conv["autofill_queue"] = [f for f in queue if f != field]
                        if conv["autofill_queue"]:
                            _propose_confirmation_for_field(conv, conv["autofill_queue"][0], root_ts, say)
                            return
                        next_idx = _compute_next_index(conv, idx)
                        conv["index"] = next_idx
                        if next_idx < len(questions):
                            next_q = questions[next_idx]
                            say(text=f"Confirmed. Next question: {_q_display(next_q)}", thread_ts=root_ts)
                        else:
                            say(text="Confirmed. All questions answered. Send `finalize` to finish.", thread_ts=root_ts)
                        return
                    # Story mode: propose and require confirmation
                    value = _rewrite_with_model(new_value_body, field, conv["data"]) if mode_to_use == "story" else new_value_body
                    conv["pending"] = {"field": field, "candidate": value}
                    say(text=f"Proposal for {label}:\n{value}\n\nConfirm with `yes`/`ok`, or provide an alternative via `new <value>` (optional `new literal:` or `new story:`).", thread_ts=root_ts)
                    return
                # Repeat current proposal unchanged, but inform about valid options
                say(text="This is not a recognized option. Choose `yes`/`ok` to accept or `new` to propose a new value.", thread_ts=root_ts)
                current = (pending.get("candidate") or "").strip()
                say(text=f"Proposal for {label}:\n{current}\n\nConfirm with `yes`/`ok`, or provide an alternative via `new <value>` (optional `new literal:` or `new story:`).", thread_ts=root_ts)
                return

        # If there are questions, consume next and request confirmation only in story mode
        questions = conv.get("questions", [])
        idx = conv.get("index", 0)
        if idx < len(questions):
            q = questions[idx]
            field = _q_field(q)
            if field:
                forced, body_text = _parse_mode_prefix(text_raw)
                mode_to_use = forced or conv.get("mode", "story")
                if field == "risicoafweging":
                    # Force yes/no; if invalid, reprompt without advancing
                    yn = body_text.strip().lower()
                    if _is_yes(yn):
                        conv["data"][field] = "yes"
                        say(
                            text=(
                                "For making a risk assessment, contact Mark, holder of the risk inventory. What was agreed as a result of this discussion?"
                            ),
                            thread_ts=root_ts,
                        )
                        # Keep index; expect user's next message as the outcome and confirm/commit per mode
                        conv["pending"] = {"field": field, "candidate": conv["data"][field]}
                        return
                    elif _is_no(yn):
                        conv["data"][field] = "no"
                        # Move to next unanswered question
                        next_idx = _compute_next_index(conv, idx + 1)
                        conv["index"] = next_idx
                        if next_idx < len(questions):
                            next_q = questions[next_idx]
                            say(text=f"Thank you. Next question: {_q_display(next_q)}", thread_ts=root_ts)
                        else:
                            say(text="Thank you. All questions answered. Send `finalize` to finish.", thread_ts=root_ts)
                            say(text=FOLLOWUP_STEPS_TEXT, thread_ts=root_ts)
                        return
                    else:
                        say(text=f"Please answer `yes` or `no` for 2.3 Risk assessment.\n\nQuestion: {_q_display(q)}", thread_ts=root_ts)
                        return
                else:
                    if mode_to_use == "story":
                        value = _rewrite_with_model(body_text, field, conv["data"]) 
                        conv["pending"] = {"field": field, "candidate": value}
                        label = DUTCH_FIELD_LABELS.get(field, field)
                        say(text=f"Proposal for {label}:\n{value}\n\nConfirm with `yes`/`ok`, or provide an alternative via `new <value>` (optional `new literal:`/`new story:`).", thread_ts=root_ts)
                        return
                    else:
                        # Literal mode: commit and advance
                        conv["data"][field] = body_text
                        conv["index"] = idx + 1
                        if conv["index"] < len(questions):
                            next_q = questions[conv["index"]]
                            say(text=f"Thank you. Next question: {_q_display(next_q)}", thread_ts=root_ts)
                        else:
                            say(text="No open questions left. *Please create a Jira issue for this report using the `jira` command*. Type `finalize` to print the final report (markdown).", thread_ts=root_ts)
                            say(text=FOLLOWUP_STEPS_TEXT, thread_ts=root_ts)
                        return
        else:
            # No questions; suggest finalize
            say(text="No open questions left. *Please create a Jira issue for this report using the `jira` command*. Type `finalize` to print the final report (markdown).", thread_ts=root_ts)
            say(text=FOLLOWUP_STEPS_TEXT, thread_ts=root_ts)
            return

        # Fallback
        say(text="I could not process your message. Type `help` for assistance or reply in the thread to the question.", thread_ts=root_ts)

    return app


def main() -> int:
    """
    Entrypoint to start the Socket Mode handler for the Slack app.

    @return int: Process exit code (0 on normal start).
    """
    load_dotenv()
    # Enable verbose Slack logging for troubleshooting
    os.environ.setdefault("SLACK_LOG_LEVEL", "DEBUG")
    logging.basicConfig(level=logging.INFO)

    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise RuntimeError("Missing SLACK_APP_TOKEN (xapp- token) for Socket Mode")

    bolt_app = build_slack_app()
    handler = SocketModeHandler(bolt_app, app_token)
    print("[incident-bot] Socket Mode handler starting...")
    handler.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())