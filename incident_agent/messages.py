from __future__ import annotations
from typing import Dict
from typing import Any, Optional

from .schema import IncidentTemplate
from .render import render_markdown
from . import utils


# ===== Slack/User-facing message builders =====


def proposal(label: str, value: str) -> str:
    """
    Build the standard proposal message asking the user to confirm or revise.

    :param label: Human-readable field label
    :param value: Proposed content
    :param include_mode_hint: Whether to include the optional literal/story hint
    :return: Slack-formatted message
    """
    return (
        f"Proposal for {label}:\n{value}\n\n"
        "Confirm with `yes`/`ok`, provide an alternative via `new <value>`"
        ", or type instructions how to change it."
    )


def next_question(prefix: str, question_display: str) -> str:
    """
    Build the standard next-question message with a prefix like 'Confirmed'/'Thank you'.
    """
    return f"{prefix}. Next question: {question_display}"


def all_questions_answered() -> str:
    """Message indicating all questions are answered."""
    return "Confirmed. All questions answered. Send `finalize` to finish."


def no_open_questions_short() -> str:
    """Short message when there are no open questions."""
    return "No open questions left. Send `finalize` to finish."


def no_open_questions_with_jira() -> str:
    """Guidance message when done, nudging to create a Jira issue."""
    return (
        "No open questions left. *Please create a Jira issue for this report using the `jira` command*. "
        "Type `finalize` to print the final report (markdown)."
    )


def warning_incomplete(action: str) -> str:
    """
    Warning about incomplete answers before a risky action ('finalize' or 'jira').
    :param action: Either 'finalize' or 'jira'
    """
    if action == "finalize":
        act_text = "finalize"
    else:
        act_text = "create a Jira issue"
    return (
        "Warning: You have not answered all the questions yet. "
        f"Are you sure you want to {act_text}? Reply `yes` to proceed or `no` to cancel and continue with the questions."
    )


def proceed_or_cancel_instruction() -> str:
    """Standard instruction for yes/no confirmation prompts."""
    return "Please answer `yes` to proceed or `no` to cancel."


# ===== Static help/preface/form texts =====

# General help shown in threads (DM)
HELP_TEXT: str = (
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
    "After each answer we show a proposal; confirm with `yes`/`ok`, provide an alternative with `new <value>`,  or type instructions how to change it."
)

# Intro text before the preface steps
PREFACE_TEXT: str = (
    "*Dealing with security incidents* (e.g. data leak)\n"
    "A security incident can happen with any project at any time. Do not panic, I will guide you through all the steps you should take to report this issue.\n"
    "I will show you the steps one by one, and you can confirm with `yes` when you have completed each step.\n\n"
)

# Preface steps split into confirmable chunks
PREFACE_STEPS: list[str] = [
    "Contact the appropriate internal product owner",
    (
        "Get approval for containing the incident from the product owner. Make sure the product owner understands the implications of containing the incident. Consider the following options:\n"
        "  a) If it is a user, block the user.\n"
        "  b) If it is a connection, block the connection/disconnect\n"
        "  c) If it is an API key or certificate, revoke it.\n"
        "  d) If it is an environment or server, shut it down."
    ),
    "Secure the data, e.g., make additional copies of logs, databases, etc.",
    "Fix the problem. If you can fix it, fix it.",
    (
        "Assess the damage\n"
        "  a) Has personal data been leaked?\n"
        "  b) Has confidential data been leaked?"
    ),
    "Write a report: type `start` to start filling out the report together with me",
]

FOLLOWUP_STEPS_TEXT: str = (
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

FORM_TEXT_PART_1: str = (
    "I will now help you to quickly and systematically record a security incident.\n\n"
    "*How it works*:\n"
    "I will ask short questions so we can complete the incident template together and create a Jira issue.\n\n"
)

FORM_TEXT_PART_2: str = (
    "*Mode*:\n"
    "We are in `story` mode: I rewrite your answers into short, clean sentences. In this mode I will first show each rewritten text for your confirmation.\n"
    "If you want me to take your words literally, switch to `literal` mode with `mode literal` or use a one-off `literal: <answer>`. In `literal` mode your answer is taken as-is without confirmation.\n\n"
)

FORM_TEXT_PART_3: str = (
    "*Options*:\n"
    "- `edit`: change previously filled fields with `edit <field> <value>`; for example `edit 2.1 email data leak`\n"
    "- `show`: show the current Markdown\n"
    "- `status`: show progress\n"
    "- `fields`: show available field names and numbers\n"
    "- `mode literal|story`: switch input mode\n"
    "- `finalize`: receive the final document\n"
    "- `jira`: create a Jira issue with the final document as description (and as .md attachment)\n\n"
)


def preface_step_text(step_index: int) -> str:
    """
    Render the preface step message for a given 1-based step index using PREFACE_STEPS.
    """
    total = len(PREFACE_STEPS)
    step_index = max(1, min(step_index, total))
    body = PREFACE_STEPS[step_index - 1]
    if step_index < total:
        return (
            f"*Step {step_index}/{total}*\n{body}\n\n"
            "Reply `yes` when completed to show the next step."
        )
    return (
        f"*Step {step_index}/{total}*\n{body}\n\n"
        "Type `start` to begin the incident report."
    )


# ===== LLM prompt templates =====


def rewriter_system_prompt() -> str:
    """
    System prompt for rewriting short notes into concise English for a specific field.
    """
    return (
        "You are an assistant that transforms short notes into clear, professional, and neutral narrative text in English."
        "Strict rules:"
        "(1) Use ONLY the 'Input' section as the source. Do NOT add facts or details that are not explicitly in the input."
        "(2) Reformulate into smooth, natural sentences that flow well, while staying factual and precise."
        "(3) It is allowed to add connecting words or stylistic phrasing to improve readability, but not to introduce new content (no hallucinations)."
        "(4) Do not enrich the output with outside information."
        "(5) Return only the reformulated text, without explanations or labels."
        "(6) Make sure all relevant facts from the input remain intact."
        "(7) Do not repeat the field name in the output."
        "(8) The tone should be professional, neutral, and narrative — as if summarizing an incident or report in a clear manner."
    )


def rewriter_user_prompt(field_label: str, raw_text: str) -> str:
    """User message for the rewriter, including the field label and input."""
    return (
        "Rewrite only the text under 'Input'. Do NOT use any other source. "
        "If the input carries little information, keep the output equally minimal.\n\n"
        f"Field: '{field_label}'\n"
        f"Input (only source):\n{raw_text}"
    )


def revision_system_prompt() -> str:
    """System prompt for iterative revision using conversation history."""
    return (
        "You are revising a DRAFT for a specific field based on the user's running instructions. "
        "Strict rules: (1) Use only the user's previous inputs and earlier assistant drafts contained in this conversation as the source. "
        "(3) Apply the user's latest instructions faithfully. "
        "(4) Return only the revised text, no commentary."
    )


def risk_assessment_followup_question() -> str:
    """Follow-up question after a 'yes' for risk assessment."""
    return (
        "For making a risk assessment, contact Mark, holder of the risk inventory. "
        "What was agreed as a result of this discussion?"
    )


def risk_assessment_yesno_prompt(question_display: str) -> str:
    """Prompt to enforce yes/no for the risk assessment question."""
    return f"Please answer `yes` or `no` for 2.3 Risk assessment.\n\nQuestion: {question_display}"


# ===== Fixed follow-up questions for each field =====


FIXED_QUESTIONS: Dict[str, str] = {
    "beschrijving_afwijking": (
        "*Describe the deviation*.\n"
        "Please provide the following details:\n"
        "* _Deviation identified_: Describe the deviation in detail.\n"
        "* _Impact_: Indicate which processes, systems, or stakeholders are affected.\n"
        "* _Initial assessment by Mark_: Mark performs a risk assessment based on the risk inventory framework.\n"
        "* _Cause_: Briefly explain why this deviation occurred."
    ),
    "maatregelen_beheersen_corrigeren": (
        "*What measures were taken to control and correct the deviation?*\n"
        "Measures have the following requirements:\n"
        "* _Immediate correction_: The error is corrected immediately where possible.\n"
        "* _Communication_: All stakeholders are informed about the deviation and the measures taken.\n"
        "* _Temporary solutions_: If a structural solution takes time, temporary measures are taken.\n"
        "* _Monitoring and control_: The situation is monitored to assess whether further actions are needed.\n"
        "* _Documentation_: All steps are recorded for future reference."
    ),
    "aanpassen_consequenties": (
        "*Are these any consequential adjustments?*\n"
        "Consider for instance the following types of adjustments:\n"
        "* _Changes to work processes_: Procedures and work methods are adjusted.\n"
        "* _Review of responsibilities_: Tasks and roles may be redistributed.\n"
        "* _Additional training and awareness_: Staff receive instruction to prevent recurrence.\n"
        "* _Policy adjustment_: Policies may be revised if necessary."
    ),
    "risicoafweging": (
        "*Should a risk assessment be made?*\n"
        "If the deviation is of such a nature, a risk assessment must be made. Contact Mark, holder of the risk inventory.\n\n"
        "A risk assessment includes:\n"
        "* Risk type:\n"
        "  - Operational\n"
        "  - Technical\n"
        "  - Financial\n"
        "  - Reputational\n"
        "  - Other\n\n"
        "* Risk score (High/Medium/Low):\n"
        "Provide a score based on impact and likelihood.\n\n"
        "* Action need:\n"
        "  - Immediate action required (use the incidents flow in JIRA)\n"
        "  - Include in audit discussion\n\n"
        "*Please answer yes/no whether a risk assessment should be made.*"
    ),
    "oorzaak_ontstaan": (
        "*What is the cause of the deviation?*\n"
        "Make sure to include:\n"
        "* Analysis of the source of the deviation.\n"
        "* Investigation into process errors, human errors, or technical problems.\n"
        "* Assessment of whether insufficient control measures contributed to the deviation."
    ),
    "gevolgen": (
        "*What are the consequences of the deviation?*\n"
        "Make sure to include:\n"
        "* The impact on the organization, customers, or processes.\n"
        "* Potential risks and additional issues resulting from the deviation.\n"
        "* Financial or operational consequences."
    ),
    "oorzaak_wegnemen": (
        "*How will the cause be removed?*\n"
        "Consider the following:\n"
        "* Structural adjustments to processes or systems to prevent recurrence.\n"
        "* Implementation of additional controls or improved work instructions.\n"
        "* Adjustments to software, hardware, or infrastructure if needed."
    ),
    "elders_voorgedaan": (
        "*Could the deviation have occurred elsewhere?*\n"
        "Make sure to:\n"
        "* Check whether the same deviation also occurs in other departments or systems.\n"
        "* Analyze similar processes and whether they face the same risk."
    ),
    "acties_elders": (
        "*What actions are needed for deviations that occurred elsewhere?*\n"
        "Consider the following actions:\n"
        "* If the deviation also occurred elsewhere, take preventive measures.\n"
        "* Implement improvements at other locations or within other teams.\n"
        "* Raise awareness and provide training to prevent recurrence."
    ),
    "doeltreffendheid": (
        "*What is the effectiveness of the measures taken*\n"
        "Make sure to include:\n"
        "* An evaluation of whether the measures have effectively resolved the issue.\n"
        "* Verify that the deviation has not recurred.\n"
        "* Feedback from involved employees and teams about the implementation."
    ),
    "actualisatie_risico": (
        "*Should the risk inventory be updated based on this deviation?* (if applicable)\n"
        "* Adjust the risk inventory and control measures where needed.\n"
        "* Document any new risks that have emerged."
    ),
    "aanpassing_kwaliteitssysteem": (
        "*Should the quality system be adjusted?* (if applicable)\n"
        "Consider:\n"
        "* Assessment of whether processes, guidelines, or protocols need adjustment.\n"
        "* Updating documentation and work instructions.\n"
        "* Communication to relevant stakeholders about changes to the quality system."
    ),
    "leerpunten": (
        "*Lessons learned (anchoring and dissemination)*\n"
        "Consider:\n"
        "* What the organization has learned from this deviation.\n"
        "* Which structural improvements can be implemented to prevent future deviations.\n"
        "* Whether training or awareness measures are needed for employees.\n"
        "* How the process around deviations and corrective actions can be further optimized."
    ),
    "relatie_iso27001_annex_a": (
        "*Relation to ISO 27001 Annex A controls*\n"
        "If relevant, specify whether this deviation relates to a control from ISO 27001 Annex A. This is always explicitly stated. Include:\n"
        "* The specific control (e.g., A.5.25 Incident management).\n"
        "* The control's role in the occurrence or containment/control of the deviation.\n"
        "* Any proposed adjustments or improvement actions.\n\n"
        "Coordination on this is carried out with the ISMS coordinator."
    ),
}

# ===== Small helpers for dynamic Slack messages =====


def first_question(total: int, question_display: str) -> str:
    return f"*Question 1/{total}*\n{question_display}"


def preface_step_incomplete(step_index: int) -> str:
    return f"Please complete step {step_index} and reply `yes` to continue."


def not_proceeding() -> str:
    return "Okay, not proceeding."


def final_document(md: str) -> str:
    return f"""Final document:
```
{md}
```
"""


def current_markdown(md: str) -> str:
    return f"""Current Markdown:
```
{md}
```
"""


def could_not_generate_final_document(error: object) -> str:
    return f"Could not generate the final document: {error}"


def jira_created(key: str) -> str:
    return f"Jira issue created: {key}"


def could_not_create_jira(error: object) -> str:
    return f"Could not create Jira issue: {error}"


def jira_updated(key: str) -> str:
    return f"Jira issue updated: {key}"


def input_mode_set(choice: str) -> str:
    return f"Input mode set to: {choice}"


def usage_mode() -> str:
    return "Usage: mode literal|story"


def current_input_mode(mode: str) -> str:
    return f"Current input mode: {mode}"


def unknown_field(field_token: str) -> str:
    return f"Unknown field `{field_token}`. Use `fields` to see available fields."


def proposal_edit(label: str, value: str) -> str:
    return (
        f"Proposal for {label}:\n{value}\n\n"
        "Confirm with `yes`/`ok`, provide an alternative via `new <value>` (optional `new literal:` or `new story:`), or type instructions how to change it.."
    )


def changed_field(key: str) -> str:
    return f"Changed: {key}"


def usage_edit_example() -> str:
    return "Usage: edit <field> <value>. Example: edit beschrijving_afwijking story: There is a data leak..."


def incident_canceled() -> str:
    return "Incident canceled. Send a new description to start again."


def need_risk_assessment_detail() -> str:
    return "You answered `yes`. Now provide the outcome/explanation, for example: `Agreed that ...`."


def all_questions_answered_thank_you() -> str:
    return "Thank you. All questions answered. Send `finalize` to finish."


def could_not_process_message() -> str:
    return "I could not process your message. Type `help` for assistance or reply in the thread to the question."


# ===== Jira post creation helpers =====


def create_jira_post(conv: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the Jira description, attachment markdown, and extra custom fields
    from the current conversation state.

    Returns a dict with keys:
      - md: full markdown document to attach
      - description_text: short description text (Section 1 only)
      - extra_fields: mapping for customfield_10061/10062/10063 with ADF content
    """
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
            sec2_lines.append("## 2.1 Measures to control and correct the deviation")
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
            sec3_lines.append("## 3.4 Could the deviation have occurred elsewhere")
            sec3_lines.append(val("elders_voorgedaan"))
        if val("acties_elders"):
            sec3_lines.append("## 3.5 Actions on deviation that occurred elsewhere")
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
            sec4_lines.append("## 4.3 Adjustment to quality system (if applicable)")
            sec4_lines.append(val("aanpassing_kwaliteitssysteem"))
    sec4_text = "\n".join(sec4_lines)

    extra_fields: Dict[str, Any] = {}
    if sec2_text:
        extra_fields["customfield_10061"] = utils.to_adf(sec2_text)
    if sec3_text:
        extra_fields["customfield_10062"] = utils.to_adf(sec3_text)
    if sec4_text:
        extra_fields["customfield_10063"] = utils.to_adf(sec4_text)

    return {
        "md": md,
        "description_text": description_text,
        "extra_fields": extra_fields,
    }
