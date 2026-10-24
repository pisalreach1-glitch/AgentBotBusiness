from __future__ import annotations

from .config import Settings
from .domain import AIReply, UserError


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "tasks": {"type": "array", "maxItems": 12, "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "due_date": {"type": ["string", "null"]}},
            "required": ["title", "due_date"],
            "additionalProperties": False,
        }},
    },
    "required": ["content", "tasks"],
    "additionalProperties": False,
}


class GeminiProvider:
    def __init__(self, settings: Settings):
        from google import genai
        from google.genai import types
        self.settings = settings
        self.client = genai.Client(
            api_key=settings.api_key, enterprise=False,
            http_options=types.HttpOptions(timeout=90000, retry_options=types.HttpRetryOptions(attempts=1)),
        )

    async def generate(self, system: str, request: str) -> AIReply:
        # No automatic second model call or hidden JSON-repair request.
        from google.genai import types, errors
        try:
            response = await self.client.aio.models.generate_content(
                model=self.settings.model,
                contents=request,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=OUTPUT_SCHEMA,
                    max_output_tokens=self.settings.max_tokens,
                ),
            )
        except errors.APIError as exc:
            if exc.code == 429:
                raise UserError("Gemini ដល់ដែនកំណត់ប្រើប្រាស់ ឬ quota របស់គម្រោង។ សូមរង់ចាំ ឬពិនិត្យក្នុង Google AI Studio។") from exc
            if exc.code in (401, 403):
                raise UserError("មិនអាចចូល Gemini API បាន។ សូមពិនិត្យ API Key និងសិទ្ធិគម្រោងក្នុង Google AI Studio។") from exc
            if exc.code == 404:
                raise UserError("រកមិនឃើញ Gemini Model នេះសម្រាប់គម្រោងរបស់អ្នក។ សូមពិនិត្យ GEMINI_MODEL ក្នុង .env។") from exc
            raise
        candidates = response.candidates or []
        candidate = candidates[0] if candidates else None
        reason = getattr(candidate.finish_reason, "value", candidate.finish_reason) if candidate else "NO_CANDIDATE"
        blocked = bool(response.prompt_feedback and response.prompt_feedback.block_reason)
        stop = "end_turn" if reason == "STOP" else ("max_tokens" if reason == "MAX_TOKENS" else "refusal" if blocked or reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"} else "unknown")
        parts = candidate.content.parts if candidate and candidate.content else []
        text = "".join(part.text for part in (parts or []) if part.text and not part.thought)
        usage = response.usage_metadata
        return AIReply(
            text=text,
            stop_reason=stop,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0,
        )

    async def close(self):
        await self.client.aio.aclose()
        self.client.close()
