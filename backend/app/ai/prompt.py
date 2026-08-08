"""The shared extraction prompt.

One prompt for every remote provider, so a model swap cannot quietly change what is
being asked for. The model extracts signals only — it is never asked for a severity
score, because scoring is the system's job and must stay identical across providers.
"""

from __future__ import annotations

from app.models.enums import IncidentType

INCIDENT_VALUES = ", ".join(member.value for member in IncidentType)

LIFE_RISK_VOCABULARY = (
    "unconscious, not_breathing, bleeding, trapped, drowning, rising_water, no_exit"
)
VULNERABILITY_VOCABULARY = "children, elderly, disabled, pregnant, injured"

SYSTEM_PROMPT = f"""You are a triage signal extractor for an emergency dispatch system.
Read the citizen report and return ONLY a JSON object. No prose, no markdown fences.

Schema:
{{
  "incident_type": one of [{INCIDENT_VALUES}],
  "life_risk_terms": array drawn ONLY from [{LIFE_RISK_VOCABULARY}],
  "people_affected_estimate": integer or null,
  "vulnerability_terms": array drawn ONLY from [{VULNERABILITY_VOCABULARY}],
  "visual_severity_modifier": integer between -10 and 10,
  "confidence": number between 0 and 1
}}

Rules:
- Report only what the text or image supports. Do not infer beyond the evidence.
- life_risk_terms and vulnerability_terms must use the exact vocabulary above.
- people_affected_estimate is the number of people at risk, or null if unstated.
- visual_severity_modifier: 0 when there is no image. When there is one, use a positive
  value if it corroborates the text and a negative value if it contradicts it.
- Do not assign a severity score. Extract signals only.
"""


def user_prompt(text: str, has_image: bool) -> str:
    image_note = (
        "An image accompanies this report; judge whether it corroborates the text."
        if has_image
        else "No image accompanies this report; visual_severity_modifier must be 0."
    )
    return f"{image_note}\n\nReport text:\n{text}"
