# Phase 0 — Shared AI Contract

**Person 1 ↔ Person 2 Integration Boundary**

---

## Purpose

Phase 0 defines the Pydantic data contract that both Person 1 (AI engine) and Person 2 (backend) use. Person 2 constructs an `IncidentAIInput` from the citizen's report and sends it to the AI engine. The AI engine returns an `IncidentAIOutput`.

---

## Location

```
shared/
    schemas/
        incident_ai.py       ← IncidentAIInput, IncidentAIOutput, error types
        classification.py    ← IncidentType, ScoringProvider, FallbackState, ClassificationResult
        severity.py          ← SeverityLabel, SeverityReasonCode, RiskFactors, SeverityResult
```

---

## IncidentAIInput

```python
class IncidentAIInput(BaseModel):
    report_id: str              # required, non-empty
    description: str            # required, non-empty
    image: Optional[str]        # base64 or URL — optional (FR-9)
    latitude: Optional[float]   # -90..90
    longitude: Optional[float]  # -180..180
    client_timestamp: Optional[datetime]
    reporter_pseudonym: Optional[str]  # pseudonym only — no real identity
```

### Validation rules

| Field | Constraint |
|---|---|
| `report_id` | Non-empty string (whitespace-only rejected) |
| `description` | Non-empty string (whitespace-only rejected) |
| `latitude` | -90.0 ≤ latitude ≤ 90.0 |
| `longitude` | -180.0 ≤ longitude ≤ 180.0 |
| `image` | Optional — system must work without it (FR-9) |

---

## IncidentAIOutput

```python
class IncidentAIOutput(BaseModel):
    report_id: str
    incident_type: IncidentType                         # one of 8 fixed categories
    classification_confidence: float                    # 0.0–1.0
    classification_reason_codes: list[ClassificationReasonCode]
    risk_factors: RiskFactors
    severity_score: int                                 # 0–100
    severity_label: SeverityLabel                       # CRITICAL/HIGH/MEDIUM/LOW
    severity_reason_codes: list[SeverityReasonCode]     # ≥1 required (FR-8)
    scoring_provider: ScoringProvider
    fallback_state: FallbackState
```

### Validation rules

| Field | Constraint |
|---|---|
| `severity_score` | 0 ≤ score ≤ 100 |
| `classification_confidence` | 0.0 ≤ confidence ≤ 1.0 |
| `severity_reason_codes` | min_length=1 (FR-8: never empty) |

---

## Enums

### IncidentType (FR-6 — exactly 8 categories)

```python
class IncidentType(str, Enum):
    STRUCTURAL_COLLAPSE = "structural_collapse"
    FLOODING = "flooding"
    MEDICAL = "medical"
    TRAPPED_PERSONS = "trapped_persons"
    FIRE = "fire"
    LANDSLIDE = "landslide"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"
```

### SeverityLabel

```python
class SeverityLabel(str, Enum):
    CRITICAL = "CRITICAL"   # 80–100
    HIGH = "HIGH"           # 60–79
    MEDIUM = "MEDIUM"       # 40–59
    LOW = "LOW"             # 0–39
```

### FallbackState

```python
class FallbackState(str, Enum):
    NORMAL = "NORMAL"                       # AI worked normally
    RULE_BASED = "RULE_BASED"              # AI failed, rule-based used
    AI_BACKFILL_PENDING = "AI_BACKFILL_PENDING"  # Timeout, AI can retry later
    AI_UNAVAILABLE = "AI_UNAVAILABLE"      # Ollama not reachable
```

### ScoringProvider

```python
class ScoringProvider(str, Enum):
    LOCAL_GRANITE = "local_granite"
    RULE_BASED = "rule_based"
```

---

## Error Format (NFR-5)

```python
class AIErrorResponse(BaseModel):
    error: AIErrorDetail

class AIErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
```

---

## Stability Guarantee

**Later phases will only ADD new optional/nullable fields to `IncidentAIOutput`.**  
Existing fields will not be renamed or removed.  
Person 2 should deserialise with `model_config = {"extra": "ignore"}` to handle future additions gracefully.

Fields reserved for future phases (not yet active):
- `authenticity_score` — Phase 4
- `duplicate_of` — Phase 5
- `corroboration_count` — Phase 5
