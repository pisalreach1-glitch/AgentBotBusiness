from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    api_key: str
    model: str
    database: str = "data/business.sqlite3"
    daily_requests: int = 30
    max_tokens: int = 5000
    allowed_ids: frozenset[int] = frozenset()
    timezone: str = "Asia/Phnom_Penh"

    @classmethod
    def from_env(cls) -> "Settings":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        required = ("TELEGRAM_TOKEN", "GEMINI_API_KEY", "GEMINI_MODEL")
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            raise ValueError("Missing settings: " + ", ".join(missing) + ". Edit .env first.")
        result = cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"].strip(),
            api_key=os.environ["GEMINI_API_KEY"].strip(),
            model=os.environ["GEMINI_MODEL"].strip(),
            database=os.getenv("DATABASE_PATH", "data/business.sqlite3"),
            daily_requests=int(os.getenv("DAILY_AI_REQUEST_LIMIT", "30")),
            max_tokens=int(os.getenv("AI_MAX_OUTPUT_TOKENS", "5000")),
            allowed_ids=frozenset(int(x.strip()) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()),
            timezone=os.getenv("TIMEZONE", "Asia/Phnom_Penh"),
        )
        if not 1 <= result.daily_requests <= 500 or not 500 <= result.max_tokens <= 12000:
            raise ValueError("Check DAILY_AI_REQUEST_LIMIT and AI_MAX_OUTPUT_TOKENS.")
        ZoneInfo(result.timezone)
        return result
