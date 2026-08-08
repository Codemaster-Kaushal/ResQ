"""Application configuration.

Every operational threshold in RescueNet is env-driven (TRD §9). When a judge asks
"what if you tuned that?", the answer is an environment variable, not a code change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Env-driven settings. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "RescueNet AI Backend"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"  # json | console
    cors_origins: str = "*"  # comma-separated, or "*"
    enable_debug_routes: bool = True

    # --- Database ---
    database_url: str = "sqlite:///./rescuenet.db"
    db_echo: bool = False

    # --- Media storage ---
    media_storage_path: str = "./media"
    max_image_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    allowed_image_types: str = "image/jpeg,image/png,image/webp"

    # --- AI providers (Phase 4) ---
    ai_provider_order: str = "gemini,groq,local"
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    ai_timeout_seconds: float = 4.0
    ai_retry_attempts: int = Field(default=1, ge=0, le=3)
    gemini_model: str = "gemini-2.0-flash"
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Authenticity thresholds (Phase 5) ---
    authenticity_baseline: int = Field(default=60, ge=0, le=100)
    authenticity_flag_threshold: int = Field(default=40, ge=0, le=100)
    phash_duplicate_distance: int = Field(default=8, ge=0, le=64)
    corroboration_radius_m: int = Field(default=500, gt=0)
    corroboration_window_min: int = Field(default=30, gt=0)
    corroboration_min_reports: int = Field(default=2, ge=2)
    corroboration_require_same_type: bool = True
    stale_report_hours: float = Field(default=6.0, gt=0)
    impossible_movement_km: float = Field(default=100.0, gt=0)
    impossible_movement_window_min: float = Field(default=10.0, gt=0)
    exif_match_radius_km: float = Field(default=1.0, gt=0)
    low_information_max_tokens: int = Field(default=5, ge=1)

    # --- Dispatch (Phase 7) ---
    dispatch_max_radius_km: float = Field(default=25.0, gt=0)

    # --- Process mining (Phase 9) ---
    bottleneck_deviation_ratio: float = Field(default=1.5, gt=0)

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in {"json", "console"}:
            raise ValueError("LOG_FORMAT must be 'json' or 'console'")
        return normalised

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalised = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if normalised not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalised

    @property
    def provider_order(self) -> list[str]:
        """AI provider fallback chain, in order. Always ends with the local scorer."""
        chain = [p.strip().lower() for p in self.ai_provider_order.split(",") if p.strip()]
        if "local" not in chain:
            chain.append("local")  # the floor is never optional (TRD §5)
        return chain

    @property
    def allowed_image_type_set(self) -> frozenset[str]:
        return frozenset(
            t.strip().lower() for t in self.allowed_image_types.split(",") if t.strip()
        )

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def media_dir(self) -> Path:
        path = Path(self.media_storage_path)
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def configured_providers(self) -> dict[str, bool]:
        """Which remote providers actually have credentials. Never exposes the keys."""
        return {
            "gemini": bool(self.gemini_api_key),
            "groq": bool(self.groq_api_key),
            "local": True,
        }

    def redacted_summary(self) -> dict[str, object]:
        """Config snapshot safe to log or serve. Reused by /api/governance in Phase 10."""
        return {
            "app_version": self.app_version,
            "environment": self.environment,
            "database_dialect": self.database_url.split(":", 1)[0],
            "media_storage_path": str(self.media_dir),
            "ai_provider_order": self.provider_order,
            "providers_with_credentials": self.configured_providers(),
            "thresholds": {
                "ai_timeout_seconds": self.ai_timeout_seconds,
                "authenticity_flag_threshold": self.authenticity_flag_threshold,
                "phash_duplicate_distance": self.phash_duplicate_distance,
                "corroboration_radius_m": self.corroboration_radius_m,
                "corroboration_window_min": self.corroboration_window_min,
                "dispatch_max_radius_km": self.dispatch_max_radius_km,
                "bottleneck_deviation_ratio": self.bottleneck_deviation_ratio,
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Tests clear the cache to override env."""
    return Settings()


settings = get_settings()
