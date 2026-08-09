"""
Custom exceptions for the RescueNet AI Engine.
All errors raised here must be caught at the API boundary and
converted to structured AIErrorResponse payloads (NFR-5).
"""


class AIEngineError(Exception):
    """Base class for all AI engine errors."""
    code: str = "AI_ENGINE_ERROR"
    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.message = message
        if retryable is not None:
            self.retryable = retryable


class AIProviderError(AIEngineError):
    """Generic provider-level failure."""
    code = "AI_PROVIDER_ERROR"
    retryable = True


class AIModelUnavailableError(AIProviderError):
    """Ollama or the configured model is not reachable."""
    code = "AI_PROVIDER_UNAVAILABLE"
    retryable = True


class AITimeoutError(AIProviderError):
    """AI inference exceeded AI_TIMEOUT_SECONDS."""
    code = "AI_TIMEOUT"
    retryable = True


class InvalidIncidentError(AIEngineError):
    """Input validation failed — the incident payload is malformed."""
    code = "INVALID_INCIDENT"
    retryable = False


class ClassificationError(AIEngineError):
    """Classification pipeline failed and fallback also failed."""
    code = "CLASSIFICATION_ERROR"
    retryable = False


class SeverityCalculationError(AIEngineError):
    """Severity calculation failed unexpectedly."""
    code = "SEVERITY_CALCULATION_ERROR"
    retryable = False
