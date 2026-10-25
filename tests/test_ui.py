from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from telegram.error import TelegramError

from app.config import Settings
from app.domain import UserError
from app.main import build_application
from app.repository import Repository
from app.services import AgentService
from app.ui import BotUI, PROFILE, PLAN, REVIEW, MARKET, QUESTIONS
from test_core import FakeProvider, output


class FakeMessage:
    def __init__(self, text="", fail_on=()):
        self.text, self.document = text, None
        self.sent, self.documents = [], []
        self.attempts, self.fail_on = 0, set(fail_on)
        self.bot = SimpleNamespace(send_chat_action=AsyncMock())

    async def reply_text(self, text, **kwargs):
        self.attempts += 1
        if self.attempts in self.fail_on:
            raise TelegramError("delivery failed")
        self.sent.append((text, kwargs))

    async def reply_document(self, document, filename):
        self.documents.append((document.getvalue(), filename))

    def get_bot(self):
        return self.bot


def update(text="", owner=1, chat="private", callback=None, event=100, message=None):
    msg = message or FakeMessage(text)
    query = SimpleNamespace(data=callback, answer=AsyncMock()) if callback is not None else None
    return SimpleNamespace(update_id=event, effective_user=SimpleNamespace(id=owner),
        effective_chat=SimpleNamespace(id=owner, type=chat), effective_message=msg,
        message=None if query else msg, callback_query=query)


class UITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.temp.name) / "state.sqlite3"))
        self.bid = self.repo.save_business(1, {"name": "សាលាម្ហូប", "offer": "វគ្គម្ហូប", "audience": "សិស្ស", "budget": "100 USD", "goal": "ចុះឈ្មោះ", "resources": "មនុស្ស២នាក់"})
        self.provider = FakeProvider()
        self.service = AgentService(self.repo, self.provider)
        self.service.today = lambda: "2026-10-24"
        self.settings = Settings("123456:TEST_TOKEN_NEVER_USED", "test-no-real-key", "test-model", database=self.repo.path)
        self.ui = BotUI(self.settings, self.repo, self.service)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def make_plan(self):
        return await self.service.generate(1, self.bid, "plan", {}, "seed")

    async def test_application_builds_with_real_telegram_types_without_network(self):
        app = build_application(self.settings, self.provider, self.repo)
        self.assertEqual(len(app.handlers[0]), 2)
        self.assertIn("ui", app.bot_data)
        self.assertEqual(app.update_processor.max_concurrent_updates, 8)

    async def test_group_request_never_loads_private_session_or_calls_ai(self):
        group = update("private secret question", chat="group")
        await self.ui.handle(group, None)
        self.assertEqual(group.effective_message.sent, [])
        self.assertEqual(self.provider.calls, [])
        start = update("/start", chat="group")
        await self.ui.handle(start, None)
        self.assertNotIn("សាលាម្ហូប", start.effective_message.sent[0][0])

    async def test_edited_message_is_ignored(self):
        edited = update("changed")
        edited.message = None
        await self.ui.handle(edited, None)
        self.assertEqual(edited.effective_message.sent, [])
        self.assertEqual(self.provider.calls, [])

    async def test_allowlist_denies_before_access(self):
        limited = Settings("token", "key", "model", allowed_ids=frozenset({99}))
        ui = BotUI(limited, self.repo, self.service)
        incoming = update(PROFILE)
        await ui.handle(incoming, None)
        self.assertIn("មិនទាន់មានសិទ្ធិ", incoming.effective_message.sent[0][0])
        self.assertEqual(self.provider.calls, [])

    async def test_profile_wizard_persists_one_question_at_a_time(self):
        await self.ui.handle(update(callback="biz:new"), None)
        values = ["ហាងថ្មី", "កាហ្វេ", "បុគ្គលិកការិយាល័យ", "50 USD", "បង្កើនអ្នកទិញ", "មនុស្សម្នាក់"]
        for number, value in enumerate(values):
            await self.ui.handle(update(value, event=200 + number), None)
        state = self.repo.session(1)
        self.assertEqual(state["flow"], "")
        profile = self.repo.business(1, state["business_id"])["profile"]
        self.assertEqual(profile["name"], "ហាងថ្មី")
        self.assertNotIn("_wizard_id", profile)
        self.assertEqual(self.provider.calls, [])

    async def test_profile_edit_keep_preserves_values(self):
        await self.ui.handle(update(callback="biz:edit"), None)
        for _ in QUESTIONS["profile"]:
            state = self.repo.session(1)
            callback = f"keep:{state['data']['_wizard_id']}:{state['step']}"
            await self.ui.handle(update(callback=callback), None)
        self.assertEqual(self.repo.business(1, self.bid)["profile"]["budget"], "100 USD")

    async def test_delete_business_flow_and_confirmation(self):
        del_prompt = update(callback=f"biz:del:{self.bid}")
        await self.ui.handle(del_prompt, None)
        self.assertIn("តើអ្នកពិតជាចង់លុប", del_prompt.effective_message.sent[0][0])
        del_confirm = update(callback=f"biz:delyes:{self.bid}")
        await self.ui.handle(del_confirm, None)
        self.assertIn("បានលុបអាជីវកម្ម", del_confirm.effective_message.sent[0][0])
        self.assertEqual(self.repo.businesses(1), [])

    async def test_stale_skip_button_cannot_answer_next_question(self):
        await self.ui.begin(update(), 1, "plan", {"plan_type": "monthly"}, self.bid)
        await self.ui.answer(update(), 1, "Goal")
        await self.ui.answer(update(), 1, "November 2026")
        state = self.repo.session(1)
        stale = f"skip:{state['data']['_wizard_id']}:{state['step']}"
        await self.ui.callback(update(), 1, stale)
        with self.assertRaises(UserError):
            await self.ui.callback(update(), 1, stale)
        self.assertEqual(self.repo.session(1)["step"], 3)

    async def test_full_button_wizard_saves_plan_and_handoff_buttons(self):
        await self.ui.handle(update(callback="ptype:launch"), None)
        for index, text in enumerate(["បើកវគ្គភេសជ្ជៈ", "2026-10-24 ដល់ 2026-11-10", "100 USD", "ទទួលសិស្ស២០នាក់"]):
            incoming = update(text, event=300 + index)
            await self.ui.handle(incoming, None)
        plans = self.repo.artifacts(1, self.bid, "plan")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["delivery"], "sent")
        keyboard = incoming.effective_message.sent[-1][1]["reply_markup"]
        actions = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("qa:" + plans[0]["id"], actions)
        self.assertIn("market:" + plans[0]["id"], actions)
        self.assertTrue(all(len(action.encode()) <= 64 for action in actions))

    async def test_failed_delivery_is_saved_and_resend_does_not_call_ai(self):
        plan = await self.make_plan()
        failed = update(message=FakeMessage(fail_on={1}))
        await self.ui.deliver(failed, 1, plan)
        self.assertEqual(self.repo.artifact(1, plan["id"])["delivery"], "failed")
        retry = update(callback="show:" + plan["id"])
        await self.ui.handle(retry, None)
        self.assertEqual(self.repo.artifact(1, plan["id"])["delivery"], "sent")
        self.assertEqual(len(self.provider.calls), 1)

    async def test_partial_delivery_does_not_claim_complete(self):
        self.provider.replies = [output("ក" * 10000)]
        plan = await self.make_plan()
        partial = update(message=FakeMessage(fail_on={2}))
        await self.ui.deliver(partial, 1, plan)
        self.assertEqual(self.repo.artifact(1, plan["id"])["delivery"], "partial")

    async def test_typing_action_failure_does_not_block_ai(self):
        await self.ui.begin(update(), 1, "plan", {}, self.bid)
        state = self.repo.session(1)
        state["data"].update(goal="Launch", period="2026-11", budget="100 USD", constraints="capacity")
        self.repo.set_session(1, self.bid, "plan", len(QUESTIONS["plan"]), state["data"])
        incoming = update()
        incoming.effective_message.bot.send_chat_action.side_effect = TelegramError("typing unavailable")
        await self.ui.run_wizard(incoming, 1)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.repo.session(1)["flow"], "")

    async def test_export_is_utf8_and_does_not_call_ai(self):
        plan = await self.make_plan()
        incoming = update(callback="export:" + plan["id"])
        await self.ui.handle(incoming, None)
        content, name = incoming.effective_message.documents[0]
        self.assertIn("ផែនការ", content.decode("utf-8-sig"))
        self.assertTrue(name.endswith(".txt"))
        self.assertEqual(len(self.provider.calls), 1)

    async def test_foreign_callback_cannot_export_or_generate_from_someone_elses_plan(self):
        plan = await self.make_plan()
        for action in ("export", "show", "qa", "fix", "market", "regen", "activate"):
            incoming = update(owner=2, callback=f"{action}:{plan['id']}")
            await self.ui.handle(incoming, None)
            self.assertEqual(incoming.effective_message.documents, [])
            self.assertNotIn(plan["content"], " ".join(text for text, _ in incoming.effective_message.sent))
        self.assertEqual(len(self.provider.calls), 1)

    async def test_plain_text_file_review_and_revision_work(self):
        await self.ui.handle(update(callback="review:upload"), None)
        incoming = update()
        file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray("ផែនការផ្ទាល់ខ្លួន".encode())))
        incoming.message.document = SimpleNamespace(file_name="plan.txt", file_size=80, get_file=AsyncMock(return_value=file))
        await self.ui.handle(incoming, None)
        state = self.repo.session(1)
        await self.ui.handle(update(callback=f"skip:{state['data']['_wizard_id']}:1", event=501), None)
        review = self.repo.artifacts(1, self.bid, "review")[0]
        self.assertIsNotNone(review["parent_id"])
        result = await self.service.revise_from_review(1, review["id"], "revision-import")
        self.assertEqual(result["kind"], "plan")

    async def test_unsupported_file_does_not_read_remote_bytes(self):
        await self.ui.handle(update(callback="review:upload"), None)
        incoming = update()
        get_file = AsyncMock()
        incoming.message.document = SimpleNamespace(file_name="malware.exe", file_size=12, get_file=get_file)
        await self.ui.handle(incoming, None)
        get_file.assert_not_awaited()
        self.assertEqual(self.provider.calls, [])

    async def test_marketing_submenu_includes_five_deliverables(self):
        incoming = update(callback="market:profile")
        await self.ui.handle(incoming, None)
        keyboard = incoming.effective_message.sent[0][1]["reply_markup"]
        self.assertEqual(len(keyboard.inline_keyboard), 5)

    async def test_user_lock_prevents_duplicate_callback_during_generation(self):
        incoming = update(callback="ptype:launch")
        lock = asyncio.Lock()
        self.ui.locks[1] = lock
        async with lock:
            await self.ui.handle(incoming, None)
        self.assertIn("សំណើមុន", incoming.effective_message.sent[0][0])
        self.assertEqual(self.provider.calls, [])

    async def test_incomplete_draft_has_no_review_or_task_activation_button(self):
        self.provider.replies = [output("Draft", stop="max_tokens")]
        draft = await self.make_plan()
        incoming = update()
        await self.ui.deliver(incoming, 1, draft)
        keyboard = incoming.effective_message.sent[-1][1]["reply_markup"]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertFalse(any(data.startswith(("qa:", "activate:", "market:")) for data in callbacks))
        self.assertIn("មិនទាន់ពេញលេញ", incoming.effective_message.sent[0][0])

    def test_health_check_server_responds_ok(self):
        import urllib.request
        from app.main import start_health_check_server
        self.assertIsNone(start_health_check_server(None))
        server = start_health_check_server(0)
        self.assertIsNotNone(server)
        port = server.server_port
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read(), b"OK")
            req = urllib.request.Request(f"http://127.0.0.1:{port}/", method="HEAD")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            server.shutdown()
            server.server_close()
