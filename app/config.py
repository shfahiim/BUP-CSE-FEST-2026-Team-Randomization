from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


# Local convenience only. Existing deployment environment variables always win.
load_dotenv(override=False)


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    gemini_api_key: str | None
    gemini_backup_api_key: str | None
    gemini_models: tuple[str, ...]
    gemini_base_url: str
    llm_attempt_timeout_seconds: float
    llm_total_timeout_seconds: float
    interpretation_cache_size: int
    output_decimal_places: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            gemini_backup_api_key=os.getenv("GEMINI_BACKUP_API_KEY") or None,
            gemini_models=_csv(
                "GEMINI_MODELS",
                "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.1-flash-lite",
            ),
            gemini_base_url=os.getenv(
                "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
            ).rstrip("/"),
            llm_attempt_timeout_seconds=float(os.getenv("LLM_ATTEMPT_TIMEOUT_SECONDS", "3")),
            llm_total_timeout_seconds=float(os.getenv("LLM_TOTAL_TIMEOUT_SECONDS", "8")),
            interpretation_cache_size=int(os.getenv("INTERPRETATION_CACHE_SIZE", "256")),
            output_decimal_places=int(os.getenv("OUTPUT_DECIMAL_PLACES", "6")),
        )
