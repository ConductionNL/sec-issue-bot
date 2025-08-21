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
    "beschrijving_afwijking": "Describe the deviation.",
    "maatregelen_beheersen_corrigeren": "What measures were taken to control and correct the deviation?",
    "aanpassen_consequenties": "Which consequences were adjusted?",
    "risicoafweging": "Should a risk assessment be made: yes / no",
    "oorzaak_ontstaan": "What is the cause of the deviation?",
    "gevolgen": "What are the consequences of the deviation?",
    "oorzaak_wegnemen": "How will the cause be removed?",
    "elders_voorgedaan": "Could the deviation have occurred elsewhere?",
    "acties_elders": "What actions are needed for deviations that occurred elsewhere?",
    "doeltreffendheid": "What is the effectiveness of the measures taken?",
    "actualisatie_risico": "Should the risk inventory be updated? (if applicable)",
    "aanpassing_kwaliteitssysteem": "Should the quality system be adjusted? (if applicable)",
    "leerpunten": "What lessons have been learned?",
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