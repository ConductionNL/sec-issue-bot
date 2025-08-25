from __future__ import annotations
from typing import Dict


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

