from __future__ import annotations

from typing import Dict

from .schema import IncidentTemplate, DUTCH_FIELD_LABELS


def render_markdown(template: IncidentTemplate) -> str:
    """
    Render a human-readable markdown document from the incident template fields.

    @param template: Pydantic model with incident fields (may contain empty strings/None).
    @return str: Markdown string with numbered headings and filled sections.
    """
    d: Dict[str, str | None] = template.model_dump()

    def val(key: str) -> str:
        """
        Retrieve a trimmed value for a given field key (empty if None).

        @param key: Field key in the template.
        @return str: Trimmed string value or empty string.
        """
        value = d.get(key)
        return value.strip() if isinstance(value, str) else ""

    # Keep exact numbering but translate headings to English
    lines = []
    lines.append("# 1. Description of deviation")
    lines.append(val("beschrijving_afwijking"))
    lines.append("")
    lines.append("# 2. Measures")
    lines.append("")
    lines.append("## 2.1 Measures to control and correct the deviation")
    lines.append(val("maatregelen_beheersen_corrigeren"))
    lines.append("")
    lines.append("## 2.2 Adjust consequences")
    lines.append(val("aanpassen_consequenties"))
    lines.append("")
    lines.append("## 2.3 Risk assessment If the deviation is of such a nature, a risk assessment must be made. Contact Mark, holder of the risk inventory")
    lines.append(val("risicoafweging"))
    lines.append("")
    lines.append("# 3. Analysis and removing causes")
    lines.append("")
    lines.append("## 3.1 Cause of the deviation")
    lines.append(val("oorzaak_ontstaan"))
    lines.append("")
    lines.append("## 3.2 Consequences of the deviation")
    lines.append(val("gevolgen"))
    lines.append("")
    lines.append("## 3.3 Remove cause")
    lines.append(val("oorzaak_wegnemen"))
    lines.append("")
    lines.append("## 3.4 Could the deviation have occurred elsewhere")
    lines.append(val("elders_voorgedaan"))
    lines.append("")
    lines.append("## 3.5 Actions on deviation that occurred elsewhere")
    lines.append(val("acties_elders"))
    lines.append("")
    lines.append("# 4. Assessment of measures taken This chapter will be filled once the JIRA actions are completed.")
    lines.append("")
    lines.append("## 4.1 Effectiveness of the measures taken")
    lines.append(val("doeltreffendheid"))
    lines.append("")
    lines.append("## 4.2 Update of risk inventory based on deviation (if applicable)")
    lines.append(val("actualisatie_risico"))
    lines.append("")
    lines.append("## 4.3 Adjustment to quality system (if applicable)")
    lines.append(val("aanpassing_kwaliteitssysteem"))
    lines.append("")
    lines.append("# 5. Lessons learned")
    lines.append(val("leerpunten"))
    lines.append("")
    lines.append("# 6. Relation to ISO 27001 Annex A controls")
    lines.append(val("relatie_iso27001_annex_a"))

    return "\n".join(lines)