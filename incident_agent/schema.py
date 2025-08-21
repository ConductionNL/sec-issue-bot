from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class IncidentTemplate(BaseModel):
    beschrijving_afwijking: Optional[str] = Field(None, description="1. Description of deviation")

    # 2. Maatregelen
    maatregelen_beheersen_corrigeren: Optional[str] = Field(None, description="2.1 Measures to control and correct the deviation")
    aanpassen_consequenties: Optional[str] = Field(None, description="2.2 Adjust consequences")
    risicoafweging: Optional[str] = Field(
        None,
        description=(
            "2.3 Risk assessment: If the deviation is of such a nature, a risk assessment must be made."
            " Contact Mark, holder of the risk inventory."
        ),
    )

    # 3. Analyse en oorzaken wegnemen
    oorzaak_ontstaan: Optional[str] = Field(None, description="3.1 Cause of the deviation")
    gevolgen: Optional[str] = Field(None, description="3.2 Consequences of the deviation")
    oorzaak_wegnemen: Optional[str] = Field(None, description="3.3 Remove cause")
    elders_voorgedaan: Optional[str] = Field(None, description="3.4 Could the deviation have occurred elsewhere")
    acties_elders: Optional[str] = Field(None, description="3.5 Actions on deviation that occurred elsewhere")

    # 4. Beoordeling genomen maatregelen (wordt gevuld na JIRA acties)
    doeltreffendheid: Optional[str] = Field(None, description="4.1 Effectiveness of the measures taken")
    actualisatie_risico: Optional[str] = Field(None, description="4.2 Update of risk inventory based on deviation (if applicable)")
    aanpassing_kwaliteitssysteem: Optional[str] = Field(None, description="4.3 Adjustment to quality system (if applicable)")

    # 5. Leerpunten
    leerpunten: Optional[str] = Field(None, description="5. Lessons learned")


class ExtractionQuestion(BaseModel):
    field_key: str = Field(..., description="Key of the field in IncidentTemplate to be filled")
    question_text: str = Field(..., description="Targeted follow-up question in English for the user")
    rationale: Optional[str] = Field(None, description="(Optional) Brief reason why this info is needed")


class ExtractionResult(BaseModel):
    data: IncidentTemplate
    questions: List[ExtractionQuestion] = Field(default_factory=list)


DUTCH_FIELD_LABELS: Dict[str, str] = {
    "beschrijving_afwijking": "1. Description of deviation",
    "maatregelen_beheersen_corrigeren": "2.1 Measures to control and correct the deviation",
    "aanpassen_consequenties": "2.2 Adjust consequences",
    "risicoafweging": "2.3 Risk assessment",
    "oorzaak_ontstaan": "3.1 Cause of the deviation",
    "gevolgen": "3.2 Consequences of the deviation",
    "oorzaak_wegnemen": "3.3 Remove cause",
    "elders_voorgedaan": "3.4 Could the deviation have occurred elsewhere",
    "acties_elders": "3.5 Actions on deviation that occurred elsewhere",
    "doeltreffendheid": "4.1 Effectiveness of the measures taken",
    "actualisatie_risico": "4.2 Update of risk inventory based on deviation (if applicable)",
    "aanpassing_kwaliteitssysteem": "4.3 Adjustment to quality system (if applicable)",
    "leerpunten": "5. Lessons learned",
}