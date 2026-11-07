"""Application settings, read from environment variables (and a .env file)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Any OpenAI-compatible chat API works. Default: Groq.
    llm_api_key: str = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    # low | medium | high (GPT-OSS reasoning depth). Reasoning tokens count as output tokens.
    reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "low")
    # Upper bound per call. Groq counts this reservation against the per-minute budget.
    max_output_tokens: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048"))
    # Provider tokens-per-minute limit (Groq free tier: 8000). 0 = no limit.
    tpm_limit: int = int(os.getenv("LLM_TPM_LIMIT", "8000"))
    # How many recent user turns are sent word for word; older ones are summarized.
    context_keep_turns: int = int(os.getenv("CONTEXT_KEEP_TURNS", "3"))
    # GPT-OSS on Groq makes one tool call per step, so allow enough steps.
    max_agent_steps: int = int(os.getenv("SCHOLARPILOT_MAX_STEPS", "15"))

    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    jwt_hours: int = int(os.getenv("JWT_HOURS", "72"))
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT_DIR / 'scholarpilot.db'}")
    admin_emails: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
        )
    )

    monitor_enabled: bool = _bool("MONITOR_ENABLED", False)
    monitor_hour: int = int(os.getenv("MONITOR_HOUR", "6"))

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "")

    catalog_path: Path = ROOT_DIR / "data" / "scholarships.json"
    web_dir: Path = ROOT_DIR / "web"


settings = Settings()
