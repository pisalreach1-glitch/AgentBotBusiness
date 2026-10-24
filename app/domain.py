from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json


class UserError(Exception):
    """An intentionally safe, Khmer message suitable for the user."""


@dataclass(frozen=True)
class AIReply:
    text: str
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0


def parse_output(text: str, kind: str) -> dict:
    """Validate before saving a complete document or accepting any tasks."""
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(cleaned)
        if not isinstance(obj, dict):
            raise ValueError("object required")
        content, tasks = obj.get("content"), obj.get("tasks", [])
        if not isinstance(content, str) or not content.strip() or len(content) > 40000:
            raise ValueError("invalid content")
        if not isinstance(tasks, list) or len(tasks) > 12:
            raise ValueError("invalid tasks")
        if kind == "plan" and not tasks:
            raise ValueError("a plan needs actionable tasks")
        checked = []
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError("invalid task")
            title, due = task.get("title"), task.get("due_date")
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300:
                raise ValueError("invalid title")
            if due is not None:
                if not isinstance(due, str) or date.fromisoformat(due).isoformat() != due:
                    raise ValueError("invalid date")
            checked.append({"title": title.strip(), "due_date": due})
        return {"content": content.strip(), "tasks": checked if kind == "plan" else []}
    except (ValueError, TypeError, AttributeError) as exc:
        raise UserError("AI ឆ្លើយទម្រង់មិនត្រឹមត្រូវ។ ព័ត៌មានដើមនៅរក្សាទុក សូមសាកល្បងម្ដងទៀត។") from exc


def telegram_chunks(text: str, limit: int = 3500) -> list[str]:
    """Conservative UTF-16 limits, without dropping spaces or splitting emoji."""
    if limit < 2:
        raise ValueError("limit must be at least 2")
    result = []
    while text:
        units, end, boundary = 0, 0, 0
        for index, char in enumerate(text):
            cost = 2 if ord(char) > 0xFFFF else 1
            if units + cost > limit:
                break
            units += cost
            end = index + 1
            if char in "\n ":
                boundary = end
        if end < len(text) and boundary >= end // 2:
            end = boundary
        result.append(text[:end])
        text = text[end:]
    return result or [" "]
