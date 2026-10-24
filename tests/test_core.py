from __future__ import annotations

import asyncio
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from app.domain import AIReply, UserError, parse_output, telegram_chunks
from app.instance import single_instance
from app.repository import Repository
from app.services import AgentService


def output(text="ផែនការសាកល្បង", tasks=True, stop="end_turn"):
    data = {"content": text, "tasks": [{"title": "រៀបចំព័ត៌មានវគ្គរៀន", "due_date": "2026-10-24"}] if tasks else []}
    return AIReply(json.dumps(data, ensure_ascii=False), stop, 21, 34)


class FakeProvider:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    async def generate(self, system, request):
        self.calls.append((system, json.loads(request)))
        await asyncio.sleep(0)
        result = self.replies.pop(0) if self.replies else output()
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self):
        pass


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.sqlite3")
        self.repo = Repository(self.path)
        self.bid = self.repo.save_business(10, {"name": "សាលាម្ហូប", "budget": "100 USD"})

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, owner=10, bid=None):
        plan = self.repo.import_plan(owner, bid or self.bid, "ផែនការដើម")
        # Store validated generated tasks through the normal generation storage path.
        self.repo.reserve(owner, "seed:" + plan["id"], "2026-10-24", 30)
        tasks = [{"title": "ការងារថ្ងៃនេះ", "due_date": "2026-10-24"},
                 {"title": "ការងារថ្ងៃស្អែក", "due_date": "2026-10-25"}]
        return self.repo.finish(owner, "seed:" + plan["id"], bid or self.bid, "plan", plan["id"],
                                "ផែនការ", tasks, {}, "complete", "end_turn")

    def test_profiles_and_wizard_survive_restart(self):
        self.repo.set_session(10, self.bid, "plan", 2, {"goal": "បើកវគ្គថ្មី"})
        restarted = Repository(self.path)
        self.assertEqual(restarted.session(10)["data"]["goal"], "បើកវគ្គថ្មី")
        self.assertEqual(restarted.business(10, self.bid)["profile"]["name"], "សាលាម្ហូប")

    def test_owner_cannot_read_or_modify_another_business(self):
        for operation in (lambda: self.repo.business(99, self.bid),
                          lambda: self.repo.save_business(99, {"name": "hijack"}, self.bid),
                          lambda: self.repo.set_session(99, self.bid)):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(self.repo.businesses(99), [])

    def test_owner_cannot_read_export_or_modify_another_artifact(self):
        plan = self.plan()
        with self.assertRaises(UserError):
            self.repo.artifact(99, plan["id"])
        with self.assertRaises(UserError):
            self.repo.delivery(99, plan["id"], "sent")
        with self.assertRaises(UserError):
            self.repo.activate_tasks(99, plan["id"])

    def test_tasks_are_due_filtered_idempotent_and_owned(self):
        plan = self.plan()
        self.repo.activate_tasks(10, plan["id"])
        self.repo.activate_tasks(10, plan["id"])
        today = self.repo.tasks(10, self.bid, "2026-10-24")
        self.assertEqual(len(today), 1)
        self.assertEqual(len(self.repo.tasks(10, self.bid, "2026-10-24", True)), 2)
        with self.assertRaises(UserError):
            self.repo.complete_task(99, today[0]["id"])
        self.repo.complete_task(10, today[0]["id"])
        self.repo.activate_tasks(10, plan["id"])
        self.assertEqual(self.repo.tasks(10, self.bid, "2026-10-24"), [])

    def test_activating_revision_does_not_mix_two_task_lists(self):
        first, second = self.plan(), self.plan()
        self.repo.activate_tasks(10, first["id"])
        self.repo.activate_tasks(10, second["id"])
        rows = self.repo.tasks(10, self.bid, "2026-10-24", True)
        self.assertEqual({row["plan_id"] for row in rows}, {second["id"]})
        self.assertEqual(self.repo.artifact(10, first["id"])["content"], "ផែនការ")

    def test_two_businesses_have_separate_active_tasks(self):
        second = self.repo.save_business(10, {"name": "ហាងភេសជ្ជៈ"})
        plan1, plan2 = self.plan(), self.plan(bid=second)
        self.repo.activate_tasks(10, plan1["id"])
        self.repo.activate_tasks(10, plan2["id"])
        self.assertEqual(len(self.repo.tasks(10, self.bid, "2026-10-24", True)), 2)
        self.assertEqual(len(self.repo.tasks(10, second, "2026-10-24", True)), 2)

    def test_delete_business_cleans_tasks_artifacts_and_resets_session(self):
        plan = self.plan()
        self.repo.activate_tasks(10, plan["id"])
        with self.assertRaises(UserError):
            self.repo.delete_business(99, self.bid)
        name = self.repo.delete_business(10, self.bid)
        self.assertEqual(name, "សាលាម្ហូប")
        self.assertEqual(self.repo.businesses(10), [])
        self.assertIsNone(self.repo.session(10)["business_id"])
        with self.assertRaises(UserError):
            self.repo.artifact(10, plan["id"])

    def test_import_cannot_be_activated_without_generated_tasks(self):
        imported = self.repo.import_plan(10, self.bid, "អត្ថបទផែនការ")
        with self.assertRaises(UserError):
            self.repo.activate_tasks(10, imported["id"])

    def test_daily_limit_persists_and_restarts_mark_pending_interrupted(self):
        self.repo.reserve(10, "first", "2026-10-24", 1)
        restarted = Repository(self.path)
        with self.assertRaises(UserError):
            restarted.reserve(10, "second", "2026-10-24", 1)
        with self.assertRaises(UserError):
            restarted.reserve(10, "first", "2026-10-25", 1)
        self.assertIsNone(restarted.reserve(10, "second", "2026-10-25", 1))

    def test_database_lock_rejects_second_running_instance(self):
        with single_instance(self.path):
            with self.assertRaises(RuntimeError):
                with single_instance(self.path):
                    self.fail("second lock must not succeed")
        with single_instance(self.path):
            pass


class DomainTests(unittest.TestCase):
    def test_splitting_preserves_khmer_emoji_and_all_whitespace(self):
        source = ("ខ្មែរសួស្តី 👩‍🍳\n" * 1500) + "បញ្ចប់"
        chunks = telegram_chunks(source)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(len(piece.encode("utf-16-le")) // 2 <= 3500 for piece in chunks))

    def test_split_long_unbroken_text_and_exact_limit(self):
        self.assertEqual(telegram_chunks("😀" * 3501), ["😀" * 1750, "😀" * 1750, "😀"])
        self.assertEqual(telegram_chunks("ក" * 3500), ["ក" * 3500])

    def test_valid_plan_json_and_optional_fence(self):
        reply = output()
        parsed = parse_output("```json\n" + reply.text + "\n```", "plan")
        self.assertEqual(parsed["tasks"][0]["due_date"], "2026-10-24")

    def test_malformed_output_dates_empty_content_or_tasks_rejected(self):
        cases = ["not JSON", "[]", '{"content":"", "tasks":[]}',
                 '{"content":"plan", "tasks":[]}',
                 '{"content":"plan", "tasks":[{"title":"task","due_date":"2026-02-30"}]}',
                 '{"content":"plan", "tasks":[{"title":"task","due_date":true}]}']
        for text in cases:
            with self.subTest(text=text), self.assertRaises(UserError):
                parse_output(text, "plan")

    def test_review_cannot_inject_tasks_into_owner_checklist(self):
        self.assertEqual(parse_output(output().text, "review")["tasks"], [])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.temp.name) / "state.sqlite3"))
        self.bid = self.repo.save_business(1, {"name": "School", "budget": "100 USD"})
        self.provider = FakeProvider()
        self.service = AgentService(self.repo, self.provider)
        self.service.today = lambda: "2026-10-24"

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_full_plan_review_revision_marketing_preserves_source_and_profile_snapshot(self):
        self.provider.replies = [output("Original"), output("QA issues", False), output("Revised"), output("Campaign", False)]
        plan = await self.service.generate(1, self.bid, "plan", {"goal": "Open a course"}, "p")
        self.repo.save_business(1, {"name": "Edited profile", "budget": "999 USD"}, self.bid)
        review = await self.service.review(1, plan["id"], "q")
        revision = await self.service.revise_from_review(1, review["id"], "r")
        marketing = await self.service.generate(1, self.bid, "marketing", {"channel": "Facebook"}, "m", source_id=revision["id"])
        self.assertEqual(self.provider.calls[1][1]["source_plan"]["content"], "Original")
        self.assertEqual(self.provider.calls[1][1]["profile"]["budget"], "100 USD")
        self.assertEqual(self.provider.calls[2][1]["qa_review"], "QA issues")
        self.assertEqual(self.provider.calls[3][1]["source_plan"]["content"], "Revised")
        self.assertEqual(revision["parent_id"], plan["id"])
        self.assertEqual(marketing["parent_id"], revision["id"])
        self.assertEqual(self.repo.artifact(1, plan["id"])["content"], "Original")
        self.assertEqual(self.repo.tasks(1, self.bid, "2026-10-24", True), [])

    async def test_same_update_is_not_billed_twice(self):
        one = await self.service.generate(1, self.bid, "plan", {}, "same-update")
        two = await self.service.generate(1, self.bid, "plan", {}, "same-update")
        self.assertEqual(one["id"], two["id"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.repo.usage(1, "2026-10-24")["requests"], 1)

    async def test_repeated_qa_and_fix_buttons_reuse_successful_result(self):
        plan = await self.service.generate(1, self.bid, "plan", {}, "p")
        review = await self.service.review(1, plan["id"], "qa-update-1")
        again = await self.service.review(1, plan["id"], "qa-update-2")
        revision = await self.service.revise_from_review(1, review["id"], "fix-1")
        repeated = await self.service.revise_from_review(1, review["id"], "fix-2")
        self.assertEqual(review["id"], again["id"])
        self.assertEqual(revision["id"], repeated["id"])
        self.assertEqual(len(self.provider.calls), 3)

    async def test_failed_qa_can_retry_on_a_new_explicit_click(self):
        plan = await self.service.generate(1, self.bid, "plan", {}, "p")
        self.provider.replies = [RuntimeError("unavailable"), output("QA fixed", False)]
        with self.assertLogs("app.services", "WARNING"), self.assertRaises(UserError):
            await self.service.review(1, plan["id"], "qa-failed-update")
        result = await self.service.review(1, plan["id"], "qa-new-update")
        self.assertEqual(result["content"], "QA fixed")

    async def test_api_failure_does_not_leak_secrets_or_commit_success(self):
        self.provider.replies = [RuntimeError("sk-SECRET https://api/?secret=PASSWORD")]
        with self.assertLogs("app.services", "WARNING") as logs:
            with self.assertRaises(UserError) as caught:
                await self.service.generate(1, self.bid, "plan", {}, "failure")
        self.assertNotIn("SECRET", str(caught.exception) + str(logs.output))
        self.assertEqual(self.repo.artifacts(1, self.bid), [])
        self.assertEqual(self.repo.usage(1, "2026-10-24")["requests"], 1)

    async def test_token_limit_saves_incomplete_draft_without_tasks_or_handoff(self):
        self.provider.replies = [AIReply('{"content":"incomplete', "max_tokens", 10, 5000)]
        draft = await self.service.generate(1, self.bid, "plan", {}, "truncated")
        self.assertEqual(draft["status"], "incomplete")
        self.assertEqual(draft["tasks"], [])
        with self.assertRaises(UserError):
            await self.service.review(1, draft["id"], "qa")
        self.assertEqual(len(self.provider.calls), 1)

    async def test_refusal_is_not_a_successful_plan(self):
        self.provider.replies = [AIReply("Cannot comply", "refusal")]
        with self.assertRaises(UserError):
            await self.service.generate(1, self.bid, "plan", {}, "refused")
        self.assertEqual(self.repo.artifacts(1, self.bid), [])

    async def test_invalid_json_is_not_silently_repaired_by_a_paid_second_call(self):
        self.provider.replies = [AIReply("broken JSON")]
        with self.assertRaises(UserError):
            await self.service.generate(1, self.bid, "plan", {}, "invalid-json")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.repo.artifacts(1, self.bid), [])

    async def test_daily_limit_prevents_outbound_call(self):
        self.service.daily_limit = 1
        await self.service.generate(1, self.bid, "plan", {}, "first")
        with self.assertRaises(UserError):
            await self.service.generate(1, self.bid, "plan", {}, "second")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.repo.usage(1, "2026-10-24")["output_tokens"], 34)

    async def test_foreign_plan_and_business_mismatch_rejected_before_ai_call(self):
        foreign = self.repo.save_business(2, {"name": "Other"})
        plan = self.repo.import_plan(2, foreign, "private source")
        with self.assertRaises(UserError):
            await self.service.review(1, plan["id"], "steal")
        second = self.repo.save_business(1, {"name": "Own other business"})
        own = self.repo.import_plan(1, self.bid, "own source")
        with self.assertRaises(UserError):
            await self.service.generate(1, second, "marketing", {}, "wrong", source_id=own["id"])
        self.assertEqual(self.provider.calls, [])

    async def test_imported_plan_can_be_reviewed_and_revised(self):
        imported = self.repo.import_plan(1, self.bid, "My existing business plan")
        review = await self.service.review(1, imported["id"], "qa")
        revised = await self.service.revise_from_review(1, review["id"], "fix")
        self.assertEqual(revised["parent_id"], imported["id"])
        self.assertTrue(revised["tasks"])

    async def test_regenerate_uses_current_date_and_keeps_previous_document(self):
        old = await self.service.generate(1, self.bid, "plan", {}, "old")
        self.service.today = lambda: "2026-10-25"
        new = await self.service.regenerate(1, old["id"], "again")
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(self.provider.calls[-1][1]["today"], "2026-10-25")
        self.assertEqual(self.repo.artifact(1, old["id"])["request"]["today"], "2026-10-24")

    async def test_duplicate_concurrent_event_calls_provider_once(self):
        results = await asyncio.gather(
            self.service.generate(1, self.bid, "plan", {}, "same"),
            self.service.generate(1, self.bid, "plan", {}, "same"),
            return_exceptions=True,
        )
        self.assertEqual(len(self.provider.calls), 1)
        self.assertTrue(any(isinstance(result, dict) for result in results))
