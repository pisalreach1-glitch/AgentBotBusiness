from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from zoneinfo import ZoneInfo

from .domain import UserError, parse_output
from .prompts import PROMPTS
from .repository import Repository, encode

log = logging.getLogger(__name__)


class AgentService:
    def __init__(self, repository: Repository, provider, daily_limit=30, timezone="Asia/Phnom_Penh"):
        self.repo, self.provider = repository, provider
        self.daily_limit, self.timezone = daily_limit, timezone
        self.slots = asyncio.Semaphore(3)

    def today(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).date().isoformat()

    async def generate(self, owner: int, business_id: str, kind: str, brief: dict,
                       request_key: str, source_id=None, review_id=None) -> dict:
        if kind not in PROMPTS:
            raise UserError("មុខងារនេះមិនត្រឹមត្រូវទេ។")
        business = self.repo.business(owner, business_id)
        request = {"today": self.today(), "profile": business["profile"], "brief": brief}
        parent_id = source_id
        if source_id:
            source = self.repo.artifact(owner, source_id)
            if source["business_id"] != business_id or source["status"] != "complete" or source["kind"] != "plan":
                raise UserError("សូមជ្រើសផែនការពេញលេញរបស់អាជីវកម្មនេះ។")
            request["profile"] = source["request"].get("profile", business["profile"])
            request["source_plan"] = {"id": source_id, "content": source["content"], "brief": source["request"].get("brief", {})}
        if review_id:
            review = self.repo.artifact(owner, review_id)
            if kind != "plan" or review["kind"] != "review" or review["parent_id"] != source_id or review["status"] != "complete":
                raise UserError("ការពិនិត្យនេះមិនត្រូវនឹងផែនការដែលបានជ្រើសទេ។")
            request["qa_review"] = review["content"]
            request["review_id"] = review_id
        return await self.generate_request(owner, business_id, kind, request, request_key, parent_id)

    async def generate_request(self, owner: int, business_id: str, kind: str,
                               request: dict, key: str, parent_id=None) -> dict:
        self.repo.business(owner, business_id)
        if kind not in PROMPTS:
            raise UserError("មុខងារនេះមិនត្រឹមត្រូវទេ។")
        day = self.today()
        # Reserve only after a worker is available; queued work survives no state mutation.
        async with self.slots:
            existing = self.repo.reserve(owner, key, day, self.daily_limit)
            if existing:
                return self.repo.artifact(owner, existing)
            try:
                reply = await asyncio.wait_for(self.provider.generate(PROMPTS[kind], encode(request)), timeout=100)
                self.repo.tokens(owner, day, reply.input_tokens, reply.output_tokens)
                if reply.stop_reason in {"max_tokens", "model_context_window_exceeded"}:
                    # Truncated JSON is deliberately not interpreted as a full plan.
                    try:
                        content = json.loads(reply.text)["content"]
                        if not isinstance(content, str):
                            content = reply.text
                    except (ValueError, KeyError, TypeError):
                        content = reply.text
                    result = {"content": content[:40000], "tasks": []}
                    status = "incomplete"
                elif reply.stop_reason == "end_turn":
                    result = parse_output(reply.text, kind)
                    status = "complete"
                else:
                    raise UserError("AI មិនបានបញ្ចប់ចម្លើយនេះទេ។ សូមកែសំណើ ឬសាកល្បងម្ដងទៀត។")
                return self.repo.finish(owner, key, business_id, kind, parent_id,
                    result["content"], result["tasks"], request, status, reply.stop_reason)
            except BaseException as exc:
                self.repo.failed_run(owner, key)
                if isinstance(exc, (UserError, asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                # Provider exception text can contain URLs or credentials. Never echo it.
                log.warning("AI request failed (%s)", type(exc).__name__)
                raise UserError("មិនអាចភ្ជាប់ AI បាន។ សូមពិនិត្យការតភ្ជាប់ API ឬសាកល្បងម្ដងទៀត។") from exc

    async def review(self, owner: int, plan_id: str, key: str) -> dict:
        plan = self.repo.artifact(owner, plan_id)
        cached = self.repo.cached_result(owner, f"review-result:{plan_id}")
        if cached:
            return cached
        result = await self.generate(owner, plan["business_id"], "review", {}, key, source_id=plan_id)
        if result["status"] == "complete":
            self.repo.cache_result(owner, f"review-result:{plan_id}", result["id"])
        return result

    async def revise_from_review(self, owner: int, review_id: str, key: str) -> dict:
        review = self.repo.artifact(owner, review_id)
        if review["kind"] != "review" or not review["parent_id"]:
            raise UserError("ការពិនិត្យផែនការដែលផ្ញើផ្ទាល់ ត្រូវចាប់ផ្ដើមជាផែនការថ្មីដើម្បីកែ។")
        cached = self.repo.cached_result(owner, f"revision-result:{review_id}")
        if cached:
            return cached
        result = await self.generate(owner, review["business_id"], "plan", {"revision": "Address the QA issues"},
                                     key, source_id=review["parent_id"], review_id=review_id)
        if result["status"] == "complete":
            self.repo.cache_result(owner, f"revision-result:{review_id}", result["id"])
        return result

    async def regenerate(self, owner: int, artifact_id: str, key: str) -> dict:
        source = self.repo.artifact(owner, artifact_id)
        request = dict(source["request"])
        request["today"] = self.today()
        return await self.generate_request(owner, source["business_id"], source["kind"],
                                           request, key, source["parent_id"])
