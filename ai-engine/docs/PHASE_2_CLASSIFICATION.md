# Phase 2 — Incident Classification

**FR-6 — Fixed 8-Category Taxonomy**

---

## Purpose

Phase 2 classifies a free-text incident description into one of exactly 8 fixed categories using IBM Granite, with a deterministic rule-based fallback.

---

## Location

```
ai_engine/
    classification/
        classifier.py       ← orchestrates AI + fallback
        rule_based.py       ← deterministic keyword classifier
        risk_extractor.py   ← rule-based risk factor extraction (also Phase 3)
```

---

## Fixed Taxonomy (FR-6 — never extended without contract change)

| Value | Description |
|---|---|
| `structural_collapse` | Building, wall, or roof collapse |
| `flooding` | Flood water, inundation, rising water |
| `medical` | Injury, unconscious persons, medical emergency |
| `trapped_persons` | People unable to evacuate independently |
| `fire` | Active fire, flames, or smoke |
| `landslide` | Landslide, mudslide, slope collapse |
| `infrastructure` | Bridge, road, power, or utility damage |
| `other` | No matching category |

---

## Classification Flow

```
classify(description, provider)
    │
    ├─ provider available?
    │       ├─ YES → GraniteLocalProvider.classify_incident()
    │       │           ├─ Success → ClassificationResult (NORMAL)
    │       │           ├─ Timeout → rule_based + AI_BACKFILL_PENDING
    │       │           └─ Error  → rule_based + RULE_BASED
    │       └─ NO  → rule_based + RULE_BASED
    │
    └─ ClassificationResult
```

---

## Granite Classification

IBM Granite is prompted with a strict JSON schema. The model must output only:

```json
{"incident_type": "flooding", "confidence": 0.94, "reason_codes": ["FLOOD_WATER_DETECTED"]}
```

If the model returns invalid JSON or unknown values:
1. `_extract_json()` strips fences and finds the first `{...}` block
2. Unknown `incident_type` → defaults to `OTHER`
3. Out-of-range confidence → clamped to `[0, 1]`
4. Unknown reason codes → dropped silently
5. Empty reason codes → replaced with `AI_CLASSIFIED`

---

## Rule-Based Classifier Fallback

The rule-based classifier uses a configurable list of `KeywordRule` objects in `ai_engine/classification/rule_based.py`.

### Rule structure

```python
@dataclass
class KeywordRule:
    pattern: re.Pattern
    incident_type: IncidentType
    reason_code: ClassificationReasonCode
    weight: float = 1.0
```

### Scoring algorithm

1. Each matching rule adds its `weight` to the accumulated score for its `incident_type`
2. The type with the highest accumulated weight wins
3. Confidence = `min(0.85, 0.5 + weight / 5.0 × 0.35)` — capped at 0.85 to signal lower accuracy

### Precedence policy (mixed incidents)

When a description matches multiple types (e.g., flooding AND trapped persons), the type with the **higher accumulated keyword weight** wins. This is not hardcoded — it emerges naturally from the weight accumulation. Both types will contribute risk factors in Phase 3.

### Extending keyword rules

Add new `KeywordRule` entries to `KEYWORD_RULES` in `ai_engine/classification/rule_based.py`. No logic changes needed.

---

## Classification Reason Codes (Controlled Vocabulary)

All valid codes are in `ClassificationReasonCode` enum:

| Code | Meaning |
|---|---|
| `FLOOD_WATER_DETECTED` | Flooding keyword matched |
| `WATER_RISING` | Rising water detected |
| `BUILDING_COLLAPSED` | Collapse keyword matched |
| `STRUCTURAL_DAMAGE` | Structural damage keyword |
| `PERSONS_TRAPPED` | Trapped keyword matched |
| `UNABLE_TO_EVACUATE` | Evacuation impossibility keyword |
| `FIRE_DETECTED` | Fire keyword matched |
| `SMOKE_DETECTED` | Smoke keyword matched |
| `MEDICAL_EMERGENCY` | Medical emergency keyword |
| `INJURY_REPORTED` | Injury keyword matched |
| `UNCONSCIOUS_PERSON` | Unconscious keyword matched |
| `LANDSLIDE_DETECTED` | Landslide keyword matched |
| `SLOPE_COLLAPSE` | Slope/hillside collapse keyword |
| `INFRASTRUCTURE_DAMAGE` | Infrastructure damage keyword |
| `ROAD_BLOCKED` | Road blocked keyword |
| `POWER_FAILURE` | Power failure keyword |
| `GENERIC_INCIDENT` | No keywords matched (OTHER) |
| `AI_CLASSIFIED` | AI classified without specific reason code |

---

## ClassificationResult Schema

```python
class ClassificationResult(BaseModel):
    incident_type: IncidentType
    confidence: float           # 0.0–1.0
    reason_codes: list[ClassificationReasonCode]
    provider: ScoringProvider   # local_granite | rule_based
    fallback_state: FallbackState
```

---

## Test Cases Coverage

| Description | Expected Type |
|---|---|
| "Water has entered our house and the street is flooded." | flooding |
| "Five people are trapped inside a collapsed building." | structural_collapse OR trapped_persons |
| "My father is unconscious and bleeding." | medical |
| "Flames and heavy smoke are coming from the building." | fire |
| "The entire hillside has collapsed onto the road." | landslide |
| "The bridge is damaged and vehicles cannot cross." | infrastructure |
| "We cannot get out of the second floor." | trapped_persons |
| "Something unusual happened in the area." | other |
