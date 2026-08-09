# Phase 3 — Severity Engine

**FR-7 (0–100 Score) + FR-8 (Reason Codes)**

---

## Purpose

Phase 3 extracts structured risk factors from incident text and calculates a deterministic severity score with human-readable reason codes.

---

## Location

```
ai_engine/
    classification/
        risk_extractor.py   ← rule-based risk factor extraction (fallback)
    severity/
        config.py           ← all weights and thresholds
        engine.py           ← deterministic calculate_severity()
    triage_service.py       ← orchestrates all three phases
```

---

## Hybrid Scoring Architecture

```
Incident Text
      │
      ├─► Granite.extract_risk_factors()  (or rule-based fallback)
      │           │
      │           ▼
      │       RiskFactors (structured)
      │           │
      └─► calculate_severity(risk_factors, confidence)
                  │
                  ▼
              SeverityResult
```

**Granite is responsible for understanding language.**  
**The scoring formula is always deterministic.**  
Same `RiskFactors` input always produces the same score — regardless of AI nondeterminism.

---

## RiskFactors Schema

```python
class RiskFactors(BaseModel):
    people_at_risk: int       # 0 = unknown/none
    trapped_persons: bool
    medical_emergency: bool
    rapidly_rising_water: bool
    structural_damage: bool
    fire_present: bool
    infrastructure_failure: bool
    evacuation_impossible: bool
    vulnerable_people: bool
    environmental_danger: bool
```

---

## Severity Weights (`ai_engine/severity/config.py`)

All weights are configurable without touching scoring logic:

| Factor | Weight | Notes |
|---|---|---|
| AI classification confidence | 18 | Scales with Granite's confidence |
| People at risk | 22 | Non-linear scaling (see below) |
| Medical emergency | 18 | Any injury/medical emergency |
| Trapped persons | 18 | Unable to evacuate |
| Immediate physical danger | 14 | Fire OR rapidly rising water |
| Structural damage | 6 | Building/structure collapse |
| Environmental risk | 2 | Flood, hazardous conditions |
| Other context | 2 | Infrastructure, evacuation impossible, vulnerable people |
| **Total** | **100** | |

### People-at-risk non-linear scaling

```python
PEOPLE_AT_RISK_SCALE = [
    (0,  0.00),   # 0 people → 0% of weight
    (1,  0.45),   # 1 person → 45%
    (2,  0.60),   # 2 people → 60%
    (3,  0.75),   # 3 people → 75%
    (5,  0.90),   # 5 people → 90%
    (10, 1.00),   # 10+ people → 100%
]
```

Interpolation is linear between brackets.

---

## Scoring Example

Input: 5 people trapped, one injured, rapidly rising water, Granite confidence = 0.9

| Component | Calculation | Score |
|---|---|---|
| AI confidence | 0.9 × 18 | 16.2 |
| People at risk (5) | 0.9 × 22 | 19.8 |
| Medical emergency | 1 × 18 | 18.0 |
| Trapped persons | 1 × 18 | 18.0 |
| Immediate danger (rising water) | 1 × 14 | 14.0 |
| **Total** | | **86.0 → CRITICAL** |

---

## Severity Labels

| Score | Label |
|---|---|
| 80–100 | CRITICAL |
| 60–79 | HIGH |
| 40–59 | MEDIUM |
| 0–39 | LOW |

Thresholds are configurable via `SEVERITY_CRITICAL_THRESHOLD`, `SEVERITY_HIGH_THRESHOLD`, `SEVERITY_MEDIUM_THRESHOLD` environment variables.

---

## Severity Reason Codes (FR-8 — Controlled Vocabulary)

Every severity result must contain at least one reason code.

| Code | Human Description |
|---|---|
| `MULTIPLE_PEOPLE_AT_RISK` | Multiple people are potentially affected. |
| `PEOPLE_AT_RISK` | At least one person is potentially at risk. |
| `TRAPPED_PERSONS` | People are reported to be unable to evacuate independently. |
| `MEDICAL_EMERGENCY` | A medical emergency or injury was reported. |
| `RAPIDLY_RISING_WATER` | Rapidly increasing flood water indicates escalating danger. |
| `FLOODING` | Flood conditions detected. |
| `STRUCTURAL_DAMAGE` | Damage to a building or structure increases risk. |
| `FIRE_PRESENT` | Active fire or flames were detected. |
| `INFRASTRUCTURE_FAILURE` | Critical infrastructure is damaged or non-functional. |
| `VULNERABLE_PEOPLE` | Vulnerable individuals may be present. |
| `ENVIRONMENTAL_DANGER` | Environmental conditions pose additional risk. |
| `EVACUATION_IMPOSSIBLE` | Evacuation is reported as impossible or blocked. |
| `INSUFFICIENT_RISK_INFORMATION` | Insufficient information to determine specific risk factors. |

`INSUFFICIENT_RISK_INFORMATION` is returned when no risk factors are detected. This satisfies FR-8 (never empty).

---

## Rule-Based Risk Extraction Fallback

When Granite is unavailable or times out, `extract_risk_factors_rule_based()` in `ai_engine/classification/risk_extractor.py` is used.

It uses:
- Regex patterns for each boolean risk factor
- Number-word recognition (one, two, three, ... twenty) for `people_at_risk`
- Digit extraction for `people_at_risk`

Always deterministic — same input → same `RiskFactors`.

---

## Image Input (FR-9)

`image` in `IncidentAIInput` is `Optional[str]`. If `null`:
- Classification and severity run on text only ✓
- No errors or degraded state ✓
- `risk_factors` still populated from text ✓

Full image fusion (embedding-level) is prepared for Phase 4.

---

## Triage Service

`TriageService` in `ai_engine/triage_service.py` orchestrates all three phases:

```
TriageService.triage(IncidentAIInput)
    │
    ├── 1. classify(description, provider)
    ├── 2. extract_risk(description, fallback_state)
    └── 3. calculate_severity(risk_factors, confidence)
         │
         └─► IncidentAIOutput
```

Fallback state from classification affects risk extraction:
- If classification already fell back (RULE_BASED), risk extraction also uses rule-based — avoids unnecessary Granite calls.

---

## Determinism Guarantee

The rule-based severity engine is fully deterministic.  
Given identical `RiskFactors` and `classification_confidence`:

```
Input A → Result A
Input A → Result A (repeated)
```

The AI layer (Granite) affects only the *extraction* of risk factors. The scoring formula does not depend on Granite at runtime.
