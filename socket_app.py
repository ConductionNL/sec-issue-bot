from __future__ import annotations

import os
import logging
from typing import Any, Dict, Tuple, Optional, Callable
import re
import time

from dotenv import load_dotenv
from slack_bolt import App as SlackApp
from slack_bolt.adapter.socket_mode import SocketModeHandler

from incident_agent.extract import IncidentExtractor
from incident_agent.render import render_markdown
from incident_agent import messages as MSG
from incident_agent.jira_client import JiraClient
from incident_agent.schema import IncidentTemplate, DUTCH_FIELD_LABELS
from incident_agent.utils import (
    q_text as _q_text,
    q_field as _q_field,
    q_number as _q_number,
    q_display as _q_display,
    is_accept as _is_accept,
    is_yes as _is_yes,
    is_no as _is_no,
    format_status as _format_status,
    next_step_text as _next_step_text,
    compute_next_index as _compute_next_index,
    resolve_field_key as _resolve_field_key,
    format_fields_list as _format_fields_list,
    parse_mode_prefix as _parse_mode_prefix,
    to_adf as _to_adf,
    to_adf_desc as _to_adf_desc,
    build_home_view as _build_home_view,
    rewrite_with_model as _rewrite_with_model,
    revise_with_history as _revise_with_history,
    set_pending_with_history as _set_pending_with_history,
    propose_confirmation_for_field as _propose_confirmation_for_field,
)


# In-memory state: keyed by (channel, thread_ts)
ConversationKey = Tuple[str, str]
state: Dict[ConversationKey, Dict[str, Any]] = {}

# Lightweight per-user session store for DM sessions
# session schema: {
#   'user_id': str,
#   'state': 'IDLE' | 'WAITING_FOR_INCIDENT' | 'ACTIVE',
#   'linked_issue_key': Optional[str],
#   'pending_incident_keys': list[str],
#   'dm_channel': Optional[str],
# }
UserId = str
SESSIONS: Dict[UserId, Dict[str, Any]] = {}

# Regex to detect Jira ISO issue keys from URLs
ISO_REGEX = re.compile(r"/browse/(ISO-\d+)")


def build_slack_app() -> SlackApp:
    """
    Build and configure the Slack Bolt app with event handlers and conversation flow.

    @return SlackApp: Configured Slack Bolt application ready for Socket Mode handler.
    """
    app = SlackApp(token=os.environ.get("SLACK_BOT_TOKEN"))
    extractor = IncidentExtractor()
    # Helper functions are imported from incident_agent.utils

    # ===== Session helpers =====
    def _get_or_create_session(user_id: str) -> Dict[str, Any]:
        sess = SESSIONS.get(user_id)
        if not sess:
            sess = {
                "user_id": user_id,
                "state": "WAITING_FOR_INCIDENT",
                "linked_issue_key": None,
                "pending_incident_keys": [],
                "dm_channel": None,
            }
            SESSIONS[user_id] = sess
        return sess

    def _set_session_dm(user_id: str, channel_id: Optional[str]) -> None:
        sess = _get_or_create_session(user_id)
        sess["dm_channel"] = channel_id

    def _ensure_dm_channel(client, user_id: str) -> Optional[str]:
        sess = _get_or_create_session(user_id)
        if sess.get("dm_channel"):
            return str(sess["dm_channel"])  # type: ignore
        try:
            resp = client.conversations_open(users=user_id)  # type: ignore[attr-defined]
            ch = resp.get("channel", {}).get("id")
            if ch:
                _set_session_dm(user_id, ch)
                return str(ch)
        except Exception:
            return None
        return None

    def _handle_chat_flow(
        conv: Dict[str, Any],
        text: str,
        text_raw: str,
        channel: str,
        root_ts: str,
        say: Callable[[str, str], None],
    ) -> None:
        # If we are in the form state, wait for explicit 'start' to continue to questionnaire
        if conv.get("status") == "form":
            if text in {"start", "/start"}:
                _start_regular_flow(channel, root_ts, say)
                return
            # Otherwise, ignore other input and remind user to type start
            say(
                text=MSG.preface_step_text(len(MSG.PREFACE_STEPS)),
                thread_ts=root_ts,
            )
            return
        idx = int(conv.get("preface_index", 1))
        total = len(MSG.PREFACE_STEPS)
        # Steps 1-6 require confirmation (yes/ok)
        if idx < total:
            if _is_accept(text_raw) or _is_yes(text_raw):
                conv["preface_index"] = idx + 1
                say(text=MSG.preface_step_text(idx + 1), thread_ts=root_ts)
            else:
                say(text=MSG.preface_step_incomplete(idx), thread_ts=root_ts)
            return
        # Step 7 requires 'start' to proceed
        if text in {"start", "/start"}:
            _start_regular_flow(channel, root_ts, say)
            return
        else:
            say(text=MSG.preface_step_text(idx), thread_ts=root_ts)
            return

    def _reconstruct_last_message_from_state(
        channel: str, root_ts: str
    ) -> Optional[str]:
        """
        Reconstruct the most recent bot prompt based on conversation state instead of
        reading Slack history.

        Preference order:
        - Preface/form step text
        - Pending proposal for a field (with candidate value)
        - Current question prompt
        - Done guidance
        """
        conv = state.get((channel, root_ts)) or {}
        if not conv:
            return None

        status = conv.get("status")
        if status in {"preface", "form"}:
            try:
                preface_idx = int(conv.get("preface_index", 1))
            except Exception:
                preface_idx = 1
            total_steps = len(MSG.PREFACE_STEPS)
            if status == "form":
                # During the form gate, remind the final preface step which instructs to type start
                return MSG.preface_step_text(total_steps)
            return MSG.preface_step_text(preface_idx)

        pending = conv.get("pending") or {}
        if isinstance(pending, dict) and pending.get("field"):
            field_key = str(pending.get("field"))
            candidate = str(pending.get("candidate") or "")
            label = DUTCH_FIELD_LABELS.get(field_key, field_key)
            return MSG.proposal(label, candidate)

        try:
            idx = int(conv.get("index", 0))
        except Exception:
            idx = 0
        questions = conv.get("questions", []) or []
        if idx < len(questions):
            return MSG.next_step_text(conv)

        return MSG.no_open_questions_with_jira()

    def _reply_in_active_thread_and_continue(
        client, dm_channel: str, issue_key: str
    ) -> None:
        """
        Post the link confirmation inside the most recently active DM thread and, if a
        conversation exists for that thread, prompt the next step instead of restarting.

        @param client: Slack WebClient
        @param dm_channel: User's DM channel id
        @param issue_key: Linked Jira issue key to acknowledge
        """
        root_ts = ""
        try:
            # Fetch recent top-level messages and pick the thread with the most recent activity
            hist = client.conversations_history(  # type: ignore[attr-defined]
                channel=dm_channel,
                limit=50,
                inclusive=True,
            )
            msgs = hist.get("messages", []) or []
            best = None
            best_activity = -1.0
            for m in msgs:
                try:
                    root = str(m.get("ts") or "")
                    # Prefer the most recent reply timestamp if available
                    latest_reply = str(m.get("latest_reply") or "")
                    last_ts = float(latest_reply or root)
                    if last_ts > best_activity:
                        best_activity = last_ts
                        best = root
                except Exception:
                    continue
            if best:
                root_ts = str(best)
        except Exception:
            pass

        if root_ts:
            say_like = _make_say_via_client(client, dm_channel)
            reconstructed = _reconstruct_last_message_from_state(dm_channel, root_ts)
            # Post the acknowledgement
            say_like(text=f"{issue_key} gekoppeld.", thread_ts=root_ts)
            # Repeat the reconstructed last message, if available
            if reconstructed:
                try:
                    say_like(text=reconstructed, thread_ts=root_ts)
                except Exception:
                    pass
        else:
            # No messages found in DM; post a simple acknowledgement without starting a new flow
            try:
                client.chat_postMessage(  # type: ignore[attr-defined]
                    channel=dm_channel,
                    text=f"{issue_key} gekoppeld.",
                )
            except Exception:
                pass

    def _reply_in_given_thread_and_continue(
        client, channel: str, root_ts: str, issue_key: str
    ) -> None:
        """
        Reply within the provided thread, acknowledge linking, and continue if a
        conversation exists.

        @param client: Slack WebClient
        @param channel: Channel id (DM)
        @param root_ts: Thread root timestamp to reply within
        @param issue_key: Jira issue key
        """
        say_like = _make_say_via_client(client, channel)
        reconstructed = _reconstruct_last_message_from_state(channel, root_ts)
        try:
            say_like(text=f"{issue_key} gekoppeld.", thread_ts=root_ts)
        except Exception:
            pass
        if reconstructed:
            try:
                say_like(text=reconstructed, thread_ts=root_ts)
            except Exception:
                pass

    def _find_most_recent_user_thread(client, dm_channel: str) -> Optional[str]:
        """
        Find the most recently active DM thread whose root message was initiated by a human user.

        @param client: Slack WebClient
        @param dm_channel: DM channel id
        @return Optional[str]: Root thread timestamp if found
        """
        try:
            hist = client.conversations_history(  # type: ignore[attr-defined]
                channel=dm_channel,
                limit=100,
                inclusive=True,
            )
            msgs = hist.get("messages", []) or []
            best_root = None
            best_activity = -1.0
            for m in msgs:
                # Only consider top-level messages authored by a user
                if m.get("bot_id") or m.get("subtype") == "bot_message":
                    continue
                if not m.get("user"):
                    continue
                # Ensure it's a root message (not a nested reply)
                ts = str(m.get("ts") or "")
                thread_ts = str(m.get("thread_ts") or "")
                if thread_ts and thread_ts != ts:
                    continue
                latest_reply = str(m.get("latest_reply") or "")
                try:
                    last_ts = float(latest_reply or ts)
                except Exception:
                    last_ts = 0.0
                if last_ts > best_activity:
                    best_activity = last_ts
                    best_root = ts
            return best_root
        except Exception:
            return None

    def _ask_link_in_active_user_thread(
        client, dm_channel: str, issue_key: str
    ) -> None:
        """
        Ask for permission to link the incident inside the most recent user-initiated thread.

        @param client: Slack WebClient
        @param dm_channel: DM channel id
        @param issue_key: Jira issue key
        """
        root_ts = _find_most_recent_user_thread(client, dm_channel) or ""
        try:
            prompt_text = f"Er is een link gedeeld voor {issue_key}. Wil je dit incident koppelen aan dit gesprek?"
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": prompt_text,
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Ja, koppel"},
                            "style": "primary",
                            "action_id": "link_incident_confirm",
                            "value": issue_key,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Nee"},
                            "action_id": "link_incident_decline",
                            "value": issue_key,
                        },
                    ],
                },
            ]
            if root_ts:
                client.chat_postMessage(  # type: ignore[attr-defined]
                    channel=dm_channel,
                    thread_ts=root_ts,
                    text=prompt_text,
                    blocks=blocks,
                )
            else:
                client.chat_postMessage(  # type: ignore[attr-defined]
                    channel=dm_channel,
                    text=prompt_text,
                    blocks=blocks,
                )
        except Exception:
            pass

    def _link_issue_to_session(user_id: str, issue_key: str) -> None:
        sess = _get_or_create_session(user_id)
        sess["linked_issue_key"] = issue_key
        sess["state"] = "ACTIVE"
        # remove from pending if present
        if isinstance(sess.get("pending_incident_keys"), list):
            sess["pending_incident_keys"] = [
                k for k in sess["pending_incident_keys"] if k != issue_key
            ]

    # ===== Event: link_shared (auto-link ISO issues) =====
    @app.event("link_shared")
    def handle_link_shared(event, logger, client):  # type: ignore
        """
        Handle Slack `link_shared` events; detect Jira browse links and extract ISO key.

        If a user's session is WAITING_FOR_INCIDENT, automatically link the incident
        to their session and transition to ACTIVE.
        Otherwise, open a DM to the reporter and create a WAITING_FOR_INCIDENT session
        with the ISO key in pending_incident_keys.
        """
        logger.info(f"[incident-bot] link_shared event: {event}")
        try:
            links = event.get("links", []) or []
            # Identify the reporter from the channel message text if possible
            user_id = ""
            channel_id = str(event.get("channel") or "")
            message_ts = str(event.get("message_ts") or "")
            # Try to fetch the original message to parse reporter mentions or names
            logger.info(f"[incident-bot] channel_id: {channel_id}")
            logger.info(f"[incident-bot] message_ts: {message_ts}")
            try:
                if channel_id and message_ts:
                    hist = client.conversations_history(  # type: ignore[attr-defined]
                        channel=channel_id,
                        latest=message_ts,
                        inclusive=True,
                        limit=1,
                    )
                    msgs = hist.get("messages", []) or []
                    if msgs:
                        txt = str(msgs[0].get("text") or "")
                        # Prefer Slack user mentions like <@U123>
                        muser = re.search(r"<@([UW][A-Z0-9]+)>", txt)
                        if muser:
                            user_id = muser.group(1)
                        else:
                            # Fallback: parse 'Name heeft onder issue ...'
                            mname = re.search(
                                r"^([^\n]+?)\s+heeft\s+onder\s+issue\s+",
                                txt,
                                re.IGNORECASE,
                            )
                            if mname:
                                display_name = mname.group(1).strip()
                                try:
                                    users = client.users_list(limit=200).get("members", [])  # type: ignore[attr-defined]
                                    for u in users:
                                        profile = u.get("profile", {}) or {}
                                        dn = str(
                                            profile.get("real_name")
                                            or profile.get("display_name")
                                            or ""
                                        )
                                        if dn and dn.lower() == display_name.lower():
                                            user_id = str(u.get("id") or "")
                                            break
                                except Exception:
                                    pass
            except Exception:
                pass
            # Fallback to the event user if reporter not found via text
            if not user_id:
                user_id = str(event.get("user") or "")
            iso_key_found: Optional[str] = None
            for link in links:
                logger.info(f"[incident-bot] link: {link}")
                url = str(link.get("url") or "")
                m = ISO_REGEX.search(url)
                if m:
                    iso_key_found = m.group(1)
                    logger.info(f"[incident-bot] iso_key_found: {iso_key_found}")
                    break
            if not iso_key_found:
                return
            if not user_id:
                logger.info(
                    f"[incident-bot] ISO {iso_key_found} shared but no user on event"
                )
                return

            sess = _get_or_create_session(user_id)
            logger.info(f"[incident-bot] sess: {sess}")
            # If they were waiting, ask permission in the most recent user-initiated thread
            if sess.get("state") == "WAITING_FOR_INCIDENT":
                dm = _ensure_dm_channel(client, user_id)
                if dm:
                    # Track as pending for later selection
                    pending = list(sess.get("pending_incident_keys") or [])
                    if iso_key_found not in pending:
                        pending.append(iso_key_found)
                        sess["pending_incident_keys"] = pending
                    _ask_link_in_active_user_thread(client, dm, iso_key_found)
                return

            # No session or not waiting: start a DM and create a waiting session with pending
            dm = _ensure_dm_channel(client, user_id)
            logger.info(f"[incident-bot] dm: {dm}")
            if dm:
                sess["state"] = "WAITING_FOR_INCIDENT"
                pending = sess.get("pending_incident_keys") or []
                if iso_key_found not in pending:
                    pending = list(pending) + [iso_key_found]
                sess["pending_incident_keys"] = pending
                client.chat_postMessage(  # type: ignore[attr-defined]
                    channel=dm,
                    text=(
                        f"Je hebt zojuist een beveiligingsincident gemeld voor issue {iso_key_found}. "
                        "Laten we samen de intake afronden. Wil je dit incident nu koppelen aan dit gesprek?"
                    ),
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"Je hebt zojuist een beveiligingsincident gemeld voor issue *{iso_key_found}*.\n"
                                    "Wil je dit incident nu koppelen aan dit gesprek?"
                                ),
                            },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Ja, koppel dit incident",
                                    },
                                    "style": "primary",
                                    "action_id": "link_incident_confirm",
                                    "value": iso_key_found,
                                },
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Nee, later kiezen",
                                    },
                                    "action_id": "link_incident_decline",
                                    "value": iso_key_found,
                                },
                            ],
                        },
                    ],
                )
        except Exception as e:
            logger.error(f"Error handling link_shared: {e}")

    # ===== Block actions: confirm/decline and picker selections =====
    @app.action("link_incident_confirm")
    def action_link_incident_confirm(ack, body, client, logger):  # type: ignore
        try:
            ack()
            user_id = str(body.get("user", {}).get("id") or "")
            issue_key = str(body.get("actions", [{}])[0].get("value") or "")
            if not user_id or not issue_key:
                return
            _link_issue_to_session(user_id, issue_key)
            dm = _ensure_dm_channel(client, user_id)
            # Prefer replying exactly in the thread where the user clicked
            container = body.get("container", {}) or {}
            thread_ts = str(
                container.get("thread_ts") or container.get("message_ts") or ""
            )
            if dm and thread_ts:
                _reply_in_given_thread_and_continue(client, dm, thread_ts, issue_key)
            elif dm:
                _reply_in_active_thread_and_continue(client, dm, issue_key)
        except Exception as e:
            logger.error(f"action_link_incident_confirm error: {e}")

    @app.action("link_incident_decline")
    def action_link_incident_decline(ack, body, client, logger):  # type: ignore
        try:
            ack()
            user_id = str(body.get("user", {}).get("id") or "")
            if not user_id:
                return
            sess = _get_or_create_session(user_id)
            sess["state"] = "WAITING_FOR_INCIDENT"

        except Exception as e:
            logger.error(f"action_link_incident_decline error: {e}")

    @app.action("pick_pending_incident")
    def action_pick_pending_incident(ack, body, client, logger):  # type: ignore
        try:
            ack()
            user_id = str(body.get("user", {}).get("id") or "")
            sel = body.get("actions", [{}])[0].get("selected_option") or {}
            issue_key = str(sel.get("value") or "")
            if not user_id or not issue_key or issue_key == "NONE":
                return
            _link_issue_to_session(user_id, issue_key)
            dm = _ensure_dm_channel(client, user_id)
            container = body.get("container", {}) or {}
            thread_ts = str(
                container.get("thread_ts") or container.get("message_ts") or ""
            )
            if dm and thread_ts:
                _reply_in_given_thread_and_continue(client, dm, thread_ts, issue_key)
            elif dm:
                _reply_in_active_thread_and_continue(client, dm, issue_key)
        except Exception as e:
            logger.error(f"action_pick_pending_incident error: {e}")

    # _format_fields_list moved to incident_agent.utils

    # moved helpers are now imported from incident_agent.utils

    def _send_form(thread_ts: str, say) -> None:  # type: ignore
        """
        Send the form content as three consecutive messages.

        @param thread_ts: Slack thread timestamp to reply in.
        @param say: Slack 'say' function used to send messages.
        @return None
        """
        say(text=MSG.FORM_TEXT_PART_1, thread_ts=thread_ts)
        time.sleep(2)
        say(text=MSG.FORM_TEXT_PART_2, thread_ts=thread_ts)
        time.sleep(2)
        say(text=MSG.FORM_TEXT_PART_3, thread_ts=thread_ts)

    def _make_say_via_client(client, channel: str):
        """
        Adapter that returns a say-like callable which posts via WebClient.

        @param client: Slack WebClient
        @param channel: Target channel id
        @return Callable: function like say(text=..., thread_ts=...)
        """

        def _say(*, text: str, thread_ts: str) -> None:
            client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)  # type: ignore[attr-defined]

        return _say

    def _start_regular_flow(channel: str, root_ts: str, say_like) -> None:
        """
        Start the regular incident intake flow in a given thread.

        @param channel: Channel id (used as conversation key)
        @param root_ts: Thread root timestamp
        @param say_like: Callable compatible with say(text=..., thread_ts=...)
        @return None
        """
        _send_form(root_ts, say_like)
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
            total = len(result.questions)
            say_like(
                text=MSG.first_question(total, _q_display(result.questions[0])),
                thread_ts=root_ts,
            )
        else:
            say_like(text=MSG.no_open_questions_short(), thread_ts=root_ts)

    def _start_preface_flow(channel: str, root_ts: str, say_like) -> None:
        """
        Begin the flow with PREFACE text and step 1, requiring confirmations
        through all PREFACE_STEPS before the form/questionnaire.

        @param channel: Channel id
        @param root_ts: Thread root timestamp
        @param say_like: Callable compatible with say(text=..., thread_ts=...)
        """
        state[(channel, root_ts)] = {
            "status": "preface",
            "preface_index": 1,
        }
        say_like(text=MSG.PREFACE_TEXT, thread_ts=root_ts)
        say_like(text=MSG.preface_step_text(1), thread_ts=root_ts)

    def _send_closeout_with_followup(root_ts: str, say_like) -> None:
        """
        Send the standard closeout message and follow-up steps together.

        @param root_ts: Thread root timestamp
        @param say_like: Callable compatible with say(text=..., thread_ts=...)
        """
        say_like(text=MSG.no_open_questions_with_jira(), thread_ts=root_ts)
        say_like(text=MSG.FOLLOWUP_STEPS_TEXT, thread_ts=root_ts)

    # _to_adf and _to_adf_desc moved to incident_agent.utils

    def _post_to_jira(conv: Dict[str, Any], event: Dict[str, Any], root_ts: str, say, only_update: bool = False) -> None:  # type: ignore
        """
        Create or update Jira issue from the current conversation state.

        If a linked issue exists in the user's session, update it; otherwise create
        a new issue unless only_update=True.
        """
        try:
            # Validate completeness if needed is handled by caller; here we just map and send
            template = IncidentTemplate(**conv.get("data", {}))
            md = render_markdown(template)
            d = template.model_dump()

            def val(key: str) -> str:
                v = d.get(key)
                return v.strip() if isinstance(v, str) else ""

            # Description: Section 1 only
            desc_text_lines: list[str] = []
            if val("beschrijving_afwijking"):
                desc_text_lines.append("# 1. Beschrijving afwijking")
                desc_text_lines.append(val("beschrijving_afwijking"))
            description_text = "\n".join(desc_text_lines) if desc_text_lines else ""

            # Section 2 → customfield_10061
            sec2_lines: list[str] = []
            if any(
                val(k)
                for k in [
                    "maatregelen_beheersen_corrigeren",
                    "aanpassen_consequenties",
                    "risicoafweging",
                ]
            ):
                sec2_lines.append("# 2. Measures")
                if val("maatregelen_beheersen_corrigeren"):
                    sec2_lines.append(
                        "## 2.1 Measures to control and correct the deviation"
                    )
                    sec2_lines.append(val("maatregelen_beheersen_corrigeren"))
                if val("aanpassen_consequenties"):
                    sec2_lines.append("## 2.2 Adjust consequences")
                    sec2_lines.append(val("aanpassen_consequenties"))
                if val("risicoafweging"):
                    sec2_lines.append(
                        "## 2.3 Risk assessment If the deviation is of such a nature, a risk assessment must be made. Contact Mark, holder of the risk inventory"
                    )
                    sec2_lines.append(val("risicoafweging"))
            sec2_text = "\n".join(sec2_lines)

            # Section 3 → customfield_10062
            sec3_lines: list[str] = []
            if any(
                val(k)
                for k in [
                    "oorzaak_ontstaan",
                    "gevolgen",
                    "oorzaak_wegnemen",
                    "elders_voorgedaan",
                    "acties_elders",
                ]
            ):
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
                    sec3_lines.append(
                        "## 3.4 Could the deviation have occurred elsewhere"
                    )
                    sec3_lines.append(val("elders_voorgedaan"))
                if val("acties_elders"):
                    sec3_lines.append(
                        "## 3.5 Actions on deviation that occurred elsewhere"
                    )
                    sec3_lines.append(val("acties_elders"))
            sec3_text = "\n".join(sec3_lines)

            # Section 4 → customfield_10063
            sec4_lines: list[str] = []
            if any(
                val(k)
                for k in [
                    "doeltreffendheid",
                    "actualisatie_risico",
                    "aanpassing_kwaliteitssysteem",
                ]
            ):
                sec4_lines.append(
                    "# 4. Assessment of measures taken This chapter will be filled once the JIRA actions are completed."
                )
                if val("doeltreffendheid"):
                    sec4_lines.append("## 4.1 Effectiveness of the measures taken")
                    sec4_lines.append(val("doeltreffendheid"))
                if val("actualisatie_risico"):
                    sec4_lines.append(
                        "## 4.2 Update of risk inventory based on deviation (if applicable)"
                    )
                    sec4_lines.append(val("actualisatie_risico"))
                if val("aanpassing_kwaliteitssysteem"):
                    sec4_lines.append(
                        "## 4.3 Adjustment to quality system (if applicable)"
                    )
                    sec4_lines.append(val("aanpassing_kwaliteitssysteem"))
            sec4_text = "\n".join(sec4_lines)

            # Try to resolve the linked issue key from the session
            linked_key: Optional[str] = None
            try:
                msg_user = str(event.get("user") or "")
                if msg_user:
                    linked_key = (
                        str(
                            _get_or_create_session(msg_user).get("linked_issue_key")
                            or ""
                        )
                        or None
                    )
            except Exception:
                linked_key = None

            jc = JiraClient()
            extra_fields: Dict[str, Any] = {}
            if sec2_text:
                extra_fields["customfield_10061"] = _to_adf(sec2_text)
            if sec3_text:
                extra_fields["customfield_10062"] = _to_adf(sec3_text)
            if sec4_text:
                extra_fields["customfield_10063"] = _to_adf(sec4_text)

            if linked_key:
                update_fields: Dict[str, Any] = {}
                if description_text:
                    update_fields["description"] = _to_adf_desc(description_text)
                if extra_fields:
                    update_fields.update(extra_fields)
                if update_fields:
                    jc.update_issue(linked_key, update_fields)
                key = linked_key
            else:
                if only_update:
                    # Nothing to do if we're only allowed to update
                    return
                issue = jc.create_issue(
                    summary="Security incident",
                    description=description_text or "",
                    extra_fields=extra_fields or None,
                )
                key = issue.get("key") or issue.get("id") or "(unknown)"
            try:
                jc.attach_markdown(str(key), "incident.md", md)
            except Exception:
                pass
            if linked_key:
                say(text=MSG.jira_updated(str(key)), thread_ts=root_ts)
            else:
                say(text=MSG.jira_created(str(key)), thread_ts=root_ts)
        except Exception as e:
            say(text=MSG.could_not_create_jira(e), thread_ts=root_ts)

    # App Home rendering helpers moved to incident_agent.utils

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
        user_id = str(event.get("user") or "")

        # Track user's DM channel in session for later proactive messages
        if user_id:
            _set_session_dm(user_id, channel)

        conv = state.get((channel, root_ts))

        text_raw = (event.get("text") or "").strip()
        text = text_raw.lower()

        # In new DM messages (no thread), do not map to old threads and do not process commands:
        # every new message starts its own conversation.

        # If no conversation context found in DM
        if not conv:
            # If the user has pending incidents, offer a picker at DM start
            if user_id:
                sess = _get_or_create_session(user_id)
                pending = list(sess.get("pending_incident_keys") or [])
                if pending:
                    options = [
                        {"text": {"type": "plain_text", "text": k}, "value": k}
                        for k in pending
                    ]
                    say(
                        text="Je hebt eerder incidenten gemeld. Wil je er een koppelen?",
                        blocks=[
                            {
                                "type": "section",
                                "block_id": "pending_picker",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "Kies een incident om te koppelen",
                                },
                                "accessory": {
                                    "type": "static_select",
                                    "action_id": "pick_pending_incident",
                                    "placeholder": {
                                        "type": "plain_text",
                                        "text": "Selecteer incident",
                                    },
                                    "options": options,
                                },
                            }
                        ],
                        thread_ts=root_ts,
                    )
                    return
            # If user types 'start' as a thread reply, initialize and start at question 1
            if has_thread and text in {"start", "/start"}:
                _start_regular_flow(channel, root_ts, say)
                return
            # In a DM without a thread: initialize preface flow anchored to this message
            if not has_thread and text_raw:
                print(
                    f"[incident-bot] New DM conversation: channel={channel} root_ts={root_ts} text={text_raw!r}"
                )
                print(sess)
                _start_preface_flow(channel, root_ts, say)
                return
            # Otherwise guide the user by starting the preface flow
            if not has_thread:
                _start_preface_flow(channel, root_ts, say)
            return

        # Handle preface/form confirmation flow
        if conv.get("status") in {"preface", "form"}:
            _handle_chat_flow(conv, text, text_raw, channel, root_ts, say)
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
                say(text=MSG.next_step_text(conv), thread_ts=root_ts)
                return
            else:
                say(
                    text=(
                        MSG.proceed_or_cancel_instruction()
                        + " "
                        + MSG.next_step_text(conv)
                    ),
                    thread_ts=root_ts,
                )
                return

        # Thread-level commands
        if text in {"help", "/help"}:
            say(text=MSG.HELP_TEXT, thread_ts=root_ts)
            return
        if text in {
            "finaliseer",
            "finaliseren",
            "finalize",
            "finaliseren aub",
            "finalise",
        }:
            try:
                questions = conv.get("questions", [])
                pending = conv.get("pending") or {}
                has_pending = isinstance(pending, dict) and pending.get("field")
                incomplete = bool(has_pending) or (
                    conv.get("index", 0) < len(questions)
                )
                if incomplete and not conv.pop("override_incomplete", False):
                    conv["confirm_action"] = "finalize"
                    say(
                        text=MSG.warning_incomplete("finalize"),
                        thread_ts=root_ts,
                    )
                    return
                md = render_markdown(IncidentTemplate(**conv.get("data", {})))
                say(text=MSG.final_document(md), thread_ts=root_ts)
            except Exception as e:
                say(text=MSG.could_not_generate_final_document(e), thread_ts=root_ts)
            return
        # (Removed) update existing Jira issue flow per request

        if text in {"jira", "/jira"}:
            try:
                questions = conv.get("questions", [])
                pending = conv.get("pending") or {}
                has_pending = isinstance(pending, dict) and pending.get("field")
                incomplete = bool(has_pending) or (
                    conv.get("index", 0) < len(questions)
                )
                if incomplete and not conv.pop("override_incomplete", False):
                    conv["confirm_action"] = "jira"
                    say(
                        text=MSG.warning_incomplete("jira"),
                        thread_ts=root_ts,
                    )
                    return
                _post_to_jira(conv, event, root_ts, say)
            except Exception as e:
                say(text=MSG.could_not_create_jira(e), thread_ts=root_ts)
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
            say(text=MSG.current_markdown(md), thread_ts=root_ts)
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
                say(text=MSG.input_mode_set(choice), thread_ts=root_ts)
                # After confirming mode change, show the next question
                say(text=_next_step_text(conv), thread_ts=root_ts)
            except Exception:
                say(text=MSG.usage_mode(), thread_ts=root_ts)
            return
        if text in {"showmode", "/showmode"}:
            say(
                text=MSG.current_input_mode(conv.get("mode", "story")),
                thread_ts=root_ts,
            )
            return
        if (
            text.startswith("edit ")
            or text.startswith("/edit ")
            or text.startswith("wijzig ")
        ):
            # edit <field> <value>  (accept optional 'story'/'literal' prefixes with space or colon)
            try:
                parts = text_raw.split(" ", 2)
                if len(parts) < 3:
                    raise ValueError
                _, field_token, new_value = parts
                key = _resolve_field_key(field_token)
                if not key:
                    say(text=MSG.unknown_field(field_token), thread_ts=root_ts)
                    return
                forced, nv_body = _parse_mode_prefix(new_value)
                mode_to_use = forced or conv.get("mode", "story")
                if mode_to_use == "story":
                    value = _rewrite_with_model(
                        extractor, nv_body, key, conv["data"]
                    )  # propose and confirm
                    _set_pending_with_history(conv, key, nv_body, value)
                    label = DUTCH_FIELD_LABELS.get(key, key)
                    say(text=MSG.proposal_edit(label, value), thread_ts=root_ts)
                else:
                    conv["data"][key] = nv_body
                    say(text=MSG.changed_field(key), thread_ts=root_ts)
            except Exception:
                say(text=MSG.usage_edit_example(), thread_ts=root_ts)
            return
        if text in {"cancel", "/cancel"}:
            state.pop((channel, root_ts), None)
            say(text=MSG.incident_canceled(), thread_ts=root_ts)
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
                    say(text=MSG.need_risk_assessment_detail(), thread_ts=root_ts)
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
                    _propose_confirmation_for_field(
                        extractor, conv, conv["autofill_queue"][0], root_ts, say
                    )
                    return
                # Otherwise proceed with the next unanswered question index
                next_idx = _compute_next_index(conv, idx)
                conv["index"] = next_idx
                if next_idx < len(questions):
                    next_q = questions[next_idx]
                    say(
                        text=MSG.next_question("Confirmed", _q_display(next_q)),
                        thread_ts=root_ts,
                    )
                else:
                    say(text=MSG.all_questions_answered(), thread_ts=root_ts)
                return
            else:
                # Special case: for risicoafweging, after a 'yes' we expect an additional detail.
                # If no meaningful candidate yet ("" or "ja"/"yes"), treat this message as the detail
                if field == "risicoafweging":
                    cand_now = (pending.get("candidate") or "").strip().lower()
                    if cand_now in {"", "ja", "yes"}:
                        forced, detail_body = _parse_mode_prefix(text_raw)
                        mode_to_use = forced or conv.get("mode", "story")
                        detail_value = (
                            _rewrite_with_model(
                                extractor, detail_body, field, conv["data"]
                            )
                            if mode_to_use == "story"
                            else detail_body
                        )
                        combined = f"yes: {detail_value}"
                        # Initialize per-field history starting from the yes + detail response and draft
                        _set_pending_with_history(conv, field, text_raw, combined)
                        say(text=MSG.proposal(label, combined), thread_ts=root_ts)
                        return
                # Treat any non-accept, non-new input as revision instructions using per-field history
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
                            _propose_confirmation_for_field(
                                extractor, conv, conv["autofill_queue"][0], root_ts, say
                            )
                            return
                        next_idx = _compute_next_index(conv, idx)
                        conv["index"] = next_idx
                        if next_idx < len(questions):
                            next_q = questions[next_idx]
                            say(
                                text=MSG.next_question("Confirmed", _q_display(next_q)),
                                thread_ts=root_ts,
                            )
                        else:
                            say(text=MSG.all_questions_answered(), thread_ts=root_ts)
                        return
                    # Story mode: propose and require confirmation
                    value = (
                        _rewrite_with_model(
                            extractor, new_value_body, field, conv["data"]
                        )
                        if mode_to_use == "story"
                        else new_value_body
                    )
                    _set_pending_with_history(conv, field, new_value_body, value)
                    say(text=MSG.proposal(label, value), thread_ts=root_ts)
                    return
                # Otherwise, refine using history and the freeform instructions
                history = pending.get("history") or []
                # Append latest user instruction
                history.append({"role": "user", "content": text_raw})
                revised = _revise_with_history(extractor, field, history, text_raw)
                # Append assistant result and update pending
                history.append({"role": "assistant", "content": revised})
                conv["pending"] = {
                    "field": field,
                    "candidate": revised,
                    "history": history,
                }
                say(text=MSG.proposal(label, revised), thread_ts=root_ts)
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
                            text=MSG.risk_assessment_followup_question(),
                            thread_ts=root_ts,
                        )
                        # Keep index; expect user's next message as the outcome and confirm/commit per mode
                        conv["pending"] = {
                            "field": field,
                            "candidate": conv["data"][field],
                        }
                        return
                    elif _is_no(yn):
                        conv["data"][field] = "no"
                        # Move to next unanswered question
                        next_idx = _compute_next_index(conv, idx + 1)
                        conv["index"] = next_idx
                        if next_idx < len(questions):
                            next_q = questions[next_idx]
                            say(
                                text=MSG.next_question("Thank you", _q_display(next_q)),
                                thread_ts=root_ts,
                            )
                        else:
                            say(
                                text=MSG.all_questions_answered_thank_you(),
                                thread_ts=root_ts,
                            )
                            _send_closeout_with_followup(root_ts, say)
                        return
                    else:
                        say(
                            text=MSG.risk_assessment_yesno_prompt(_q_display(q)),
                            thread_ts=root_ts,
                        )
                        return
                else:
                    if mode_to_use == "story":
                        value = _rewrite_with_model(
                            extractor, body_text, field, conv["data"]
                        )
                        _set_pending_with_history(conv, field, body_text, value)
                        label = DUTCH_FIELD_LABELS.get(field, field)
                        say(text=MSG.proposal(label, value), thread_ts=root_ts)
                        return
                    else:
                        # Literal mode: commit and advance
                        conv["data"][field] = body_text
                        conv["index"] = idx + 1
                        if conv["index"] < len(questions):
                            next_q = questions[conv["index"]]
                            say(
                                text=MSG.next_question("Thank you", _q_display(next_q)),
                                thread_ts=root_ts,
                            )
                        else:
                            _send_closeout_with_followup(root_ts, say)
                        return
        else:
            # No questions; suggest finalize
            _send_closeout_with_followup(root_ts, say)
            return

        # Fallback
        say(text=MSG.could_not_process_message(), thread_ts=root_ts)

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
