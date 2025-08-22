from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI

from .schema import (
    DUTCH_FIELD_LABELS,
    ExtractionQuestion,
    ExtractionResult,
    IncidentTemplate,
)


load_dotenv()


SYSTEM_PROMPT = (
    "You are an extractor that structures security incidents in English and fills in a template. "
    "Goal: extract the most important, high-signal information from the input and omit noise/irrelevant details. "
    "Rules: (1) Paraphrase concisely for clarity, but do not change the meaning. "
    "(2) Do not add new facts or make unfounded assumptions; minor, strongly implied normalizations (dates, counts, data types) are allowed. "
    "(3) Fill a field when the input provides sufficient basis; if unclear, set null. "
    "(4) Focus on who/what/when/where/impact/data involved/cause/actions taken. "
    "(5) Respond only with JSON that exactly conforms to the schema. "
    "(6) Distribute facts across the appropriate fields; do not place an all-in-one summary under 'beschrijving_afwijking' if the information belongs elsewhere, and do not unnecessarily duplicate facts."
)

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


def _target_fields() -> List[str]:
    """
    Return the ordered list of field keys we aim to collect for an incident.

    @return List[str]: Field keys defined in DUTCH_FIELD_LABELS.
    """
    return list(DUTCH_FIELD_LABELS.keys())


def _build_output_stub() -> Dict[str, Any]:
    """
    Build an initial empty extraction structure with all fields set to None.

    @return Dict[str, Any]: Stub with keys 'data' (field map) and 'questions' (empty list).
    """
    data: Dict[str, Any] = {key: None for key in _target_fields()}
    return {"data": data, "questions": []}


class IncidentExtractor:
    def __init__(self, client: OpenAI | None = None, model: str | None = None) -> None:
        """
        Initialize the extractor with an OpenAI client and model selection.

        @param client: Optional external OpenAI client to reuse.
        @param model: Optional model name override; falls back to env OPENAI_MODEL.
        @return None
        """
        self.client = client or OpenAI()
        env_model = os.environ.get("OPENAI_MODEL")
        self.model = model or env_model or "gpt-4o-mini"

    def extract(self, description: str, temperature: float = 0) -> ExtractionResult:
        """
        Produce an empty template and fixed follow-up questions for every field.

        @param description: Initial freeform description (currently not auto-extracted).
        @param temperature: Kept for API compatibility; unused in current logic.
        @return ExtractionResult: Result with empty data and a question per field.
        """
        # We no longer perform LLM-based extraction from the initial description.
        # Instead, we return an empty data structure and fixed follow-up questions
        # for all fields so the flow starts at question 1.

        stub = _build_output_stub()
        merged_data: Dict[str, Any] = stub["data"]

        # Validate with Pydantic
        result = ExtractionResult(
            data=IncidentTemplate(**merged_data),
            questions=[],
        )

        # Build fixed follow-up questions for all fields (start at 1)
        questions_fixed: List[ExtractionQuestion] = []
        for key in _target_fields():
            question_text = FIXED_QUESTIONS.get(key)
            if not question_text:
                label = DUTCH_FIELD_LABELS.get(key, key)
                question_text = f"Fill in: {label}"
            questions_fixed.append(ExtractionQuestion(field_key=key, question_text=question_text))

        result.questions = questions_fixed
        return result