# Phase 1 — Local AI Environment

**IBM Granite through Ollama — Offline Provider Architecture**

---

## Purpose

Phase 1 establishes the local AI provider architecture that allows IBM Granite to run entirely offline.  
The architecture uses an abstract base class so the exact model can be swapped without changing application code.

---

## Provider Hierarchy

```
AIProvider (abstract base)
    └── GraniteLocalProvider
            └── Ollama HTTP API
                    └── IBM Granite (local model)
```

### Location

```
ai_engine/
    config.py                   ← environment variables (single source of truth)
    exceptions.py               ← structured custom exceptions
    providers/
        base.py                 ← AIProvider abstract interface
        granite_local.py        ← GraniteLocalProvider implementation
```

---

## Configuration (`ai_engine/config.py`)

All model configuration lives in one place:

```python
GRANITE_MODEL      = os.environ.get("GRANITE_MODEL", "granite3.3:8b")
OLLAMA_HOST        = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "5"))
AI_TEMPERATURE     = float(os.environ.get("AI_TEMPERATURE", "0.0"))
```

**Never reference `os.environ` directly in any other module.** Always import from `ai_engine.config`.

---

## AIProvider Interface (`ai_engine/providers/base.py`)

```python
class AIProvider(ABC):
    @property
    def provider_name(self) -> str: ...
    @property
    def model_name(self) -> str: ...
    @property
    def model_version(self) -> str: ...

    async def is_available(self) -> bool: ...
    async def analyze_text(self, text: str) -> dict: ...
    async def classify_incident(self, description: str) -> ClassificationResult: ...
    async def extract_risk_factors(self, description: str) -> RiskFactors: ...
```

To add a new provider (e.g., cloud fallback), implement `AIProvider` and update `app/dependencies.py`. No other code changes needed.

---

## GraniteLocalProvider (`ai_engine/providers/granite_local.py`)

Communicates with Ollama over HTTP. Uses structured JSON prompts.

### Key implementation details

- All prompts force JSON output via the system message — no free-form text parsing
- `asyncio.wait_for` implements the hard timeout (FR-10)
- `_extract_json()` strips markdown code fences and extracts the first `{...}` block
- Unknown model outputs are coerced defensively — never crash the request

### Prompt engineering

**Classification prompt** forces this exact schema:
```json
{"incident_type": "<category>", "confidence": 0.0, "reason_codes": ["<CODE>"]}
```

**Risk extraction prompt** forces this exact schema:
```json
{
  "people_at_risk": 0,
  "trapped_persons": false,
  "medical_emergency": false,
  "rapidly_rising_water": false,
  "structural_damage": false,
  "fire_present": false,
  "infrastructure_failure": false,
  "evacuation_impossible": false,
  "vulnerable_people": false,
  "environmental_danger": false
}
```

---

## Health Check

```
GET /health/ai
```

Calls `provider.is_available()` which queries `GET /api/tags` on Ollama and verifies the configured model is present.

If Ollama is unreachable or the model is not downloaded, the endpoint returns:

```json
{
  "status": "degraded",
  "provider": "local_granite",
  "model": "granite3.3:8b",
  "offline_capable": true,
  "error_code": "AI_PROVIDER_UNAVAILABLE"
}
```

The engine continues running in rule-based mode when degraded.

---

## Custom Exceptions

```
AIEngineError (base)
    AIProviderError
        AIModelUnavailableError   → code: AI_PROVIDER_UNAVAILABLE
        AITimeoutError            → code: AI_TIMEOUT
    InvalidIncidentError          → code: INVALID_INCIDENT
    ClassificationError           → code: CLASSIFICATION_ERROR
    SeverityCalculationError      → code: SEVERITY_CALCULATION_ERROR
```

All exceptions carry `code` (string) and `retryable` (bool). The API layer converts them to `AIErrorResponse` — raw stack traces never reach the client (NFR-5).

---

## Offline Operation (NFR-2)

After `ollama pull granite3.3:8b`, the engine runs with zero network access:

1. Ollama caches the model locally
2. The AI engine connects only to `localhost:11434`
3. No external DNS, no remote APIs

To verify offline operation:
```bash
# Pull the model first
ollama pull granite3.3:8b

# Disable network (or unplug cable)
# Start Ollama
ollama serve

# Start AI engine
uvicorn app.main:app --reload

# Triage a report — should return normal AI results
curl -X POST http://localhost:8000/ai/triage ...
```
