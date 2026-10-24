from __future__ import annotations

import asyncio
from io import BytesIO
import logging

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Inline
from telegram import ReplyKeyboardMarkup, Update
from telegram.error import TelegramError, RetryAfter

from .config import Settings
from .domain import UserError, telegram_chunks
from .repository import Repository, new_id
from .services import AgentService

log = logging.getLogger(__name__)

PLAN = "📝 បង្កើតផែនការ"
REVIEW = "🔎 ពិនិត្យផែនការ"
MARKET = "📣 រៀបចំការផ្សព្វផ្សាយ"
PROFILE = "🏢 អាជីវកម្មរបស់ខ្ញុំ"
HISTORY = "📂 ផែនការនិងឯកសារ"
TASKS = "✅ ការងារថ្ងៃនេះ"
USAGE = "⚙️ ការប្រើប្រាស់"
CANCEL = "❌ បោះបង់"
MENU = ReplyKeyboardMarkup([[PLAN, REVIEW], [MARKET, PROFILE], [HISTORY, TASKS], [USAGE, CANCEL]], resize_keyboard=True)
PLAN_TYPES = {"new": "អាជីវកម្មថ្មី", "growth": "ពង្រីកអាជីវកម្ម", "launch": "បើកវគ្គឬផលិតផលថ្មី", "week": "ផែនការប្រចាំសប្ដាហ៍", "month": "ផែនការប្រចាំខែ"}
MARKETING_TYPES = {"facebook": "ផុស Facebook", "telegram": "សារ Telegram", "video": "Script វីដេអូខ្លី", "calendar": "ប្រតិទិនមាតិកា", "campaign": "យុទ្ធនាការបើកវគ្គឬផលិតផល"}
KINDS = {"plan": "ផែនការ", "review": "ពិនិត្យផែនការ", "marketing": "ការផ្សព្វផ្សាយ"}

# key, Khmer question, optional. Everything below persists in SQLite.
QUESTIONS = {
    "profile": [
        ("name", "តើអាជីវកម្មរបស់អ្នកមានឈ្មោះអ្វី?", False),
        ("offer", "អ្នកលក់ឬផ្តល់សេវាអ្វី? បញ្ចូលតម្លៃ និងលក្ខខណ្ឌដែលមាន។", False),
        ("audience", "អតិថិជនគោលដៅរបស់អ្នកជាអ្នកណា?", False),
        ("budget", "ថវិកាប្រហែលប៉ុន្មាន? សូមបញ្ជាក់រូបិយប័ណ្ណ និងរយៈពេល។", True),
        ("goal", "គោលដៅសំខាន់របស់អាជីវកម្មអ្នកគឺអ្វី?", False),
        ("resources", "មានក្រុមការងារ ពេលវេលា សមត្ថភាពទទួលអតិថិជន និងបណ្តាញផ្សព្វផ្សាយអ្វីខ្លះ?", True),
    ],
    "plan": [
        ("goal", "ផែនការនេះចង់សម្រេចអ្វី? បញ្ចូលចំនួនគោលដៅ ប្រសិនបើមាន។", False),
        ("period", "ចាប់ផ្ដើមនិងបញ្ចប់នៅពេលណា? សូមប្រើថ្ងៃខែឆ្នាំច្បាស់ ប្រសិនបើមាន។", False),
        ("budget", "ថវិកាសម្រាប់ផែនការនេះប៉ុន្មាន? សូមបញ្ជាក់រូបិយប័ណ្ណ។", True),
        ("constraints", "មានលក្ខខណ្ឌ អ្វីដែលធ្វើមិនបាន ឬព័ត៌មានដែលមានប្រភពអ្វីខ្លះ?", True),
    ],
    "review": [
        ("plan_text", "សូមផ្ញើផែនការរបស់អ្នកជាអត្ថបទ ឬឯកសារ UTF-8 .txt/.md មិនលើស 100 KB។", False),
        ("focus", "ចង់ឱ្យពិនិត្យចំណុចណាជាពិសេស? ឬចុចរំលងដើម្បីពិនិត្យទាំងមូល។", True),
    ],
    "revision": [("changes", "តើអ្នកចង់កែផ្នែកណានៃផែនការនេះ?", False)],
    "marketing": [
        ("channel", "ចង់ប្រើបណ្តាញណា? ឧទាហរណ៍ Facebook, Telegram ឬ Facebook Reels។", False),
        ("tone", "ចង់បានស្ទាយបែបណា? ឧទាហរណ៍ រួសរាយ វិជ្ជាជីវៈ ឬកំប្លែងសមរម្យ។", False),
        ("details", "បញ្ចូលថ្ងៃផ្សព្វផ្សាយ រយៈពេល តម្លៃ លីងឬលេខទាក់ទង និងការណែនាំបន្ថែម។", True),
    ],
}


def buttons(rows):
    return Inline([[Button(label, callback_data=data) for label, data in row] for row in rows])


class BotUI:
    def __init__(self, settings: Settings, repo: Repository, service: AgentService):
        self.settings, self.repo, self.service = settings, repo, service
        self.locks: dict[int, asyncio.Lock] = {}

    async def say(self, update, text, markup=None):
        message = update.effective_message
        parts = telegram_chunks(text)
        for index, part in enumerate(parts):
            kwargs = {"reply_markup": markup} if index == len(parts) - 1 and markup is not None else {}
            try:
                await message.reply_text(part, **kwargs)
            except RetryAfter as exc:
                seconds = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else float(exc.retry_after)
                if seconds > 15:
                    raise
                await asyncio.sleep(seconds + 0.2)
                await message.reply_text(part, **kwargs)

    async def permitted(self, update) -> bool:
        if update.effective_user is None or update.effective_chat is None:
            return False
        if update.effective_chat.type != "private":
            # Do not load a profile or session for a group update.
            if update.message and (update.message.text or "").startswith("/start"):
                await self.say(update, "សូមប្រើ Bot នេះក្នុង Private Chat ដើម្បីចូលទៅព័ត៌មានអាជីវកម្មរបស់អ្នក។")
            return False
        if self.settings.allowed_ids and update.effective_user.id not in self.settings.allowed_ids:
            await self.say(update, "គណនីនេះមិនទាន់មានសិទ្ធិប្រើ Bot នេះទេ។")
            return False
        return True

    async def handle(self, update: Update, context):
        # Old edited messages never launch another AI request.
        if update.message is None and update.callback_query is None:
            return
        owner = update.effective_user.id if update.effective_user else None
        try:
            if update.callback_query:
                try:
                    await update.callback_query.answer()
                except TelegramError:
                    pass
            if not await self.permitted(update):
                return
            lock = self.locks.setdefault(owner, asyncio.Lock())
            if lock.locked():
                await self.say(update, "កំពុងដំណើរការសំណើមុន សូមរង់ចាំបន្តិច។")
                return
            async with lock:
                if update.callback_query:
                    await self.callback(update, owner, update.callback_query.data or "")
                elif update.message.document:
                    await self.document(update, owner)
                elif update.message.text:
                    await self.text(update, owner, update.message.text)
                else:
                    await self.say(update, "សូមជ្រើសប៊ូតុងខាងក្រោម។ កំណែនេះទទួលអត្ថបទ និងឯកសារ .txt/.md។", MENU)
        except UserError as exc:
            markup = self.retry_markup(owner) if owner else MENU
            await self.safe_say(update, str(exc), markup)
        except TelegramError as exc:
            log.warning("Telegram delivery failed (%s)", type(exc).__name__)
        except Exception as exc:
            log.error("Handler failed (%s)", type(exc).__name__)
            await self.safe_say(update, "មានបញ្ហាពេលដំណើរការ។ ទិន្នន័យដែលបានរក្សាទុកនៅដដែល សូមចុច /start។", MENU)

    async def safe_say(self, update, text, markup=None):
        try:
            await self.say(update, text, markup)
        except TelegramError:
            pass

    def retry_markup(self, owner):
        state = self.repo.session(owner)
        if state["flow"] in QUESTIONS and state["step"] >= len(QUESTIONS[state["flow"]]):
            return buttons([[("🔄 សាកល្បងម្ដងទៀត", "retry:wizard")], [("❌ បោះបង់", "cancel")]])
        return MENU

    def business_id(self, owner):
        identifier = self.repo.session(owner)["business_id"]
        if not identifier:
            raise UserError("សូមចុច «🏢 អាជីវកម្មរបស់ខ្ញុំ» រួចបង្កើតព័ត៌មានអាជីវកម្មជាមុន។")
        self.repo.business(owner, identifier)
        return identifier

    async def text(self, update, owner, text):
        command = text.split()[0].split("@")[0].lower()
        if command in {"/start", "/menu"}:
            state = self.repo.session(owner)
            await self.say(update, "សួស្តី! ជ្រើសមុខងារខាងក្រោម។\nចាប់ផ្ដើមពី «អាជីវកម្មរបស់ខ្ញុំ» ប្រសិនបើអ្នកមិនទាន់បញ្ចូលព័ត៌មាន។\nព័ត៌មាន និងផែនការរបស់អ្នកត្រូវបានរក្សាទុក។", MENU)
            if state["flow"] in QUESTIONS:
                if state["step"] < len(QUESTIONS[state["flow"]]):
                    await self.question(update, owner)
                else:
                    await self.say(update, "មានសំណើដែលបានបំពេញព័ត៌មានរួច តែមិនទាន់បញ្ចប់។", self.retry_markup(owner))
            return
        if command == "/id":
            await self.say(update, f"Telegram user ID របស់អ្នក៖ {owner}")
            return
        if command in {"/help", "/agents"}:
            await self.say(update, "📝 Planner៖ រៀបចំនិងកែផែនការ\n🔎 QA៖ ពិនិត្យផែនការ\n📣 Marketing៖ បង្កើតមាតិកាផ្សព្វផ្សាយ\n\n/start បើកម៉ឺនុយ\n/cancel ឈប់ការបញ្ចូលព័ត៌មានបច្ចុប្បន្ន\n/skip រំលងសំណួរដែលអាចរំលងបាន\n/id បង្ហាញលេខសម្គាល់អ្នក\n\nគ្មានការផុសទៅបណ្តាញផ្សេងដោយស្វ័យប្រវត្តិ។", MENU)
            return
        if command in {"/cancel", "/reset"} or text == CANCEL:
            await self.cancel(update, owner)
            return
        if command == "/agent":
            aliases = {"planner": PLAN, "qa": REVIEW, "marketing": MARKET}
            text = aliases.get(text.split()[-1].lower(), "")
        if text == PROFILE:
            rows = [[("➕ បង្កើតអាជីវកម្ម", "biz:new")]]
            current = self.repo.session(owner)["business_id"]
            if current:
                profile = self.repo.business(owner, current)["profile"]
                labels = {"name": "ឈ្មោះ", "offer": "ផលិតផល/សេវា", "audience": "អតិថិជន", "budget": "ថវិកា", "goal": "គោលដៅ", "resources": "ធនធាន"}
                await self.say(update, "ព័ត៌មានអាជីវកម្មបច្ចុប្បន្ន៖\n" + "\n".join(f"{labels.get(key, key)}: {value}" for key, value in profile.items()))
                rows.append([("✏️ កែព័ត៌មានបច្ចុប្បន្ន", "biz:edit")])
            for item in self.repo.businesses(owner):
                rows.append([(item["profile"]["name"][:50], "biz:" + item["id"])])
            await self.say(update, "ជ្រើសឬបង្កើតអាជីវកម្ម៖", buttons(rows))
            return
        if text == PLAN:
            self.business_id(owner)
            await self.say(update, "តើចង់បង្កើតផែនការប្រភេទណា?", buttons([[(label, "ptype:" + key)] for key, label in PLAN_TYPES.items()]))
            return
        if text == REVIEW:
            self.business_id(owner)
            await self.say(update, "ជ្រើសផែនការដែលចង់ពិនិត្យ៖", buttons([[("📂 ផែនការដែលបានរក្សាទុក", "list:review:0")], [("📄 ផ្ញើផែនការផ្ទាល់ខ្លួន", "review:upload")]]))
            return
        if text == MARKET:
            self.business_id(owner)
            await self.say(update, "ប្រើព័ត៌មានពីណា?", buttons([[("📂 ពីផែនការដែលរក្សាទុក", "list:market:0")], [("🏢 ពីព័ត៌មានអាជីវកម្ម", "market:profile")]]))
            return
        if text == HISTORY:
            await self.list_artifacts(update, owner, "history", 0)
            return
        if text == TASKS:
            await self.show_tasks(update, owner)
            return
        if text == USAGE:
            usage = self.repo.usage(owner, self.service.today())
            await self.say(update, f"ថ្ងៃនេះ ({self.settings.timezone})\nសំណើ AI៖ {usage['requests']}/{self.settings.daily_requests}\nInput tokens៖ {usage['input_tokens']}\nOutput tokens៖ {usage['output_tokens']}\nModel៖ {self.settings.model}\n\nសំណើដែលបានចាប់ផ្ដើមហើយបរាជ័យក៏រាប់ក្នុងដែនកំណត់។ ការបើកឬផ្ញើឯកសារចាស់ឡើងវិញ មិនហៅ AI ថ្មីទេ។", MENU)
            return
        if self.repo.session(owner)["flow"]:
            await self.answer(update, owner, None if command == "/skip" else text)
            return
        await self.say(update, "សូមជ្រើសមុខងារខាងក្រោម ដើម្បីចាប់ផ្ដើម។", MENU)

    async def begin(self, update, owner, flow, data=None, business_id=None):
        state = self.repo.session(owner)
        details = dict(data or {})
        details["_wizard_id"] = new_id()
        self.repo.set_session(owner, business_id or state["business_id"], flow, 0, details)
        await self.question(update, owner)

    async def question(self, update, owner):
        state = self.repo.session(owner)
        _, question, optional = QUESTIONS[state["flow"]][state["step"]]
        rows = []
        if optional:
            rows.append([("រំលង / ប្រើព័ត៌មានដែលមាន", f"skip:{state['data'].get('_wizard_id')}:{state['step']}")])
        if state["flow"] == "profile" and state["data"].get("edit_id"):
            rows.append([("រក្សាតម្លៃដើម", f"keep:{state['data'].get('_wizard_id')}:{state['step']}")])
        rows.append([("❌ បោះបង់", "cancel")])
        await self.say(update, f"សំណួរ {state['step'] + 1}/{len(QUESTIONS[state['flow']])}\n{question}", buttons(rows))

    async def answer(self, update, owner, value):
        state = self.repo.session(owner)
        flow = state["flow"]
        if flow not in QUESTIONS:
            raise UserError("សូមចាប់ផ្ដើមពីម៉ឺនុយវិញ។")
        if state["step"] >= len(QUESTIONS[flow]):
            await self.say(update, "សូមចុចសាកល្បងម្ដងទៀត ឬបោះបង់សំណើនេះ។", self.retry_markup(owner))
            return
        key, _, optional = QUESTIONS[flow][state["step"]]
        if value is None:
            if not optional:
                raise UserError("សំណួរនេះត្រូវការចម្លើយ។ សូមបញ្ចូលព័ត៌មាន ឬចុចបោះបង់។")
            value = "មិនទាន់កំណត់; ប្រើព័ត៌មានអាជីវកម្មដែលមាន ប្រសិនបើពាក់ព័ន្ធ"
        limit = 60000 if key == "plan_text" else (80 if key == "name" else 6000)
        if not value.strip() or len(value) > limit:
            raise UserError(f"សូមបញ្ចូលអត្ថបទមិនទទេ និងមិនលើស {limit} តួអក្សរ។")
        state["data"][key] = value.strip()
        state["step"] += 1
        self.repo.set_session(owner, state["business_id"], flow, state["step"], state["data"])
        if state["step"] < len(QUESTIONS[flow]):
            await self.question(update, owner)
        elif flow == "profile":
            data = dict(state["data"])
            edit_id = data.pop("edit_id", None)
            data.pop("_wizard_id", None)
            self.repo.save_business(owner, data, edit_id)
            await self.say(update, "✅ បានរក្សាទុកព័ត៌មានអាជីវកម្ម។", MENU)
        else:
            await self.run_wizard(update, owner)

    async def document(self, update, owner):
        state = self.repo.session(owner)
        if state["flow"] != "review" or state["step"] != 0:
            raise UserError("សូមជ្រើស «ពិនិត្យផែនការ → ផ្ញើផែនការផ្ទាល់ខ្លួន» ជាមុន។")
        doc = update.message.document
        if not (doc.file_name or "").lower().endswith((".txt", ".md")) or (doc.file_size or 0) > 102400:
            raise UserError("សូមប្រើឯកសារ UTF-8 .txt ឬ .md ដែលមានទំហំមិនលើស 100 KB។")
        file = await doc.get_file()
        raw = await file.download_as_bytearray()
        if len(raw) > 102400:
            raise UserError("ឯកសារធំពេក។")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UserError("មិនអាចអានឯកសារនេះបាន។ សូមរក្សាទុកជា UTF-8 ឬចម្លងអត្ថបទផ្ញើមក។") from exc
        await self.answer(update, owner, text)

    async def run_wizard(self, update, owner):
        state = self.repo.session(owner)
        data, flow, bid = dict(state["data"]), state["flow"], state["business_id"]
        if flow not in QUESTIONS or state["step"] < len(QUESTIONS[flow]):
            raise UserError("សូមបំពេញសំណួរជាមុន។")
        if flow == "review" and not data.get("source_id"):
            imported = self.repo.import_plan(owner, bid, data["plan_text"])
            data["source_id"] = imported["id"]
            data.pop("plan_text", None)
            self.repo.set_session(owner, bid, flow, state["step"], data)
        kind = "plan" if flow == "revision" else flow
        await self.working(update)
        artifact = await self.service.generate(owner, bid, kind, data, f"wizard:{update.update_id}", source_id=data.get("source_id"))
        self.repo.set_session(owner, bid)
        await self.deliver(update, owner, artifact)

    async def working(self, update):
        await self.say(update, "កំពុងរៀបចំចម្លើយ សូមរង់ចាំបន្តិច…")
        try:
            await update.effective_message.get_bot().send_chat_action(update.effective_chat.id, "typing")
        except TelegramError:
            pass

    async def cancel(self, update, owner):
        state = self.repo.session(owner)
        self.repo.set_session(owner, state["business_id"])
        await self.say(update, "បានបញ្ឈប់ការបញ្ចូលព័ត៌មានបច្ចុប្បន្ន។ ឯកសារដែលបានរក្សាទុកនៅដដែល។", MENU)

    async def list_artifacts(self, update, owner, purpose, offset):
        bid = self.business_id(owner)
        items = self.repo.artifacts(owner, bid, None if purpose == "history" else "plan", offset)
        rows = []
        for item in items:
            title = f"{KINDS[item['kind']]} · {item['created'][:10]} · {item['id'][-4:]}"
            prefix = {"history": "show", "review": "qa", "market": "market"}[purpose]
            rows.append([(title, f"{prefix}:{item['id']}")])
        if offset > 0:
            rows.append([("◀️ មុន", f"list:{purpose}:{max(0, offset - 8)}")])
        if len(items) == 8:
            rows.append([("បន្ទាប់ ▶️", f"list:{purpose}:{offset + 8}")])
        await self.say(update, "ជ្រើសឯកសារ៖" if items else "មិនទាន់មានឯកសារ។ សូមបង្កើតផែនការ ឬផ្ញើផែនការផ្ទាល់ខ្លួន។", buttons(rows) if rows else MENU)

    async def market_types(self, update, owner, source):
        if source != "profile":
            artifact = self.repo.artifact(owner, source)
            if artifact["kind"] != "plan" or artifact["status"] != "complete":
                raise UserError("សូមជ្រើសផែនការពេញលេញ។")
        await self.say(update, "ចង់បានមាតិកាប្រភេទណា?", buttons([[(label, f"mtype:{source}:{key}")] for key, label in MARKETING_TYPES.items()]))

    async def show_tasks(self, update, owner, all_tasks=False):
        items = self.repo.tasks(owner, self.business_id(owner), self.service.today(), all_tasks)
        visible = items if all_tasks else items[:3]
        text = "ការងារទាំងអស់៖" if all_tasks else "ការងារអាទិភាពសម្រាប់ថ្ងៃនេះ (រួមទាំងការងារហួសកំណត់)៖"
        rows = []
        for index, task in enumerate(visible, 1):
            text += f"\n{index}. {task['title']}\nកំណត់៖ {task['due_date'] or 'មិនទាន់កំណត់'}"
            rows.append([(f"✅ ការងារ {index} រួចរាល់", f"done:{task['id']}")])
        if not items:
            text += "\nគ្មានការងារក្នុងបញ្ជីនេះ។ អាចបើកផែនការ ហើយចុច «ប្រើបញ្ជីការងារ»។"
        if not all_tasks:
            rows.append([("មើលការងារទាំងអស់", "tasks:all")])
        await self.say(update, text, buttons(rows) if rows else MENU)

    async def deliver(self, update, owner, artifact):
        identifier = artifact["id"]
        header = f"{KINDS[artifact['kind']]} · #{identifier[-6:]}"
        if artifact["status"] != "complete":
            header += "\n⚠️ ចម្លើយមិនទាន់ពេញលេញ ដោយសារដែនកំណត់ token/context។ សូមកាត់បន្ថយវិសាលភាព ឬសាកល្បងបង្កើតឡើងវិញ។"
        text = header + "\n\n" + artifact["content"]
        rows = []
        if artifact["status"] == "complete" and artifact["kind"] == "plan":
            rows.extend([[("🔎 ពិនិត្យផែនការនេះ", f"qa:{identifier}"), ("✏️ កែផែនការ", f"revise:{identifier}")], [("📣 រៀបចំការផ្សព្វផ្សាយ", f"market:{identifier}")]])
            if artifact["tasks"]:
                rows.append([("✅ ប្រើបញ្ជីការងារនេះ", f"activate:{identifier}")])
        if artifact["status"] == "complete" and artifact["kind"] == "review" and artifact["parent_id"]:
            rows.append([("✏️ កែផែនការតាមយោបល់", f"fix:{identifier}")])
        rows.append([("⬇️ ទាញយក TXT", f"export:{identifier}"), ("🔄 បង្កើតឡើងវិញ", f"regen:{identifier}")])
        if artifact["parent_id"]:
            rows.append([("📂 បើកផែនការដើម", f"show:{artifact['parent_id']}")])
        sent = 0
        chunks = telegram_chunks(text)
        try:
            self.repo.delivery(owner, identifier, "pending")
            for index, part in enumerate(chunks):
                await self.say(update, part, buttons(rows) if index == len(chunks) - 1 else None)
                sent += 1
            self.repo.delivery(owner, identifier, "sent")
        except TelegramError:
            self.repo.delivery(owner, identifier, "partial" if sent else "failed")
            await self.safe_say(update, "ចម្លើយបានរក្សាទុក ប៉ុន្តែផ្ញើមិនបានពេញលេញ។ ចុចខាងក្រោមដើម្បីផ្ញើឡើងវិញ ដោយមិនហៅ AI ថ្មី។", buttons([[("📩 ផ្ញើចម្លើយចាស់ឡើងវិញ", f"show:{identifier}")]]))

    async def callback(self, update, owner, data):
        if len(data.encode("utf-8")) > 64:
            raise UserError("ប៊ូតុងមិនត្រឹមត្រូវ។ សូមចុច /start។")
        parts = data.split(":")
        action = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        if action == "cancel":
            await self.cancel(update, owner)
        elif action in {"skip", "keep"}:
            state = self.repo.session(owner)
            if len(parts) != 3 or arg != state["data"].get("_wizard_id") or parts[2] != str(state["step"]):
                raise UserError("ប៊ូតុងនេះជារបស់សំណួរចាស់។ សូមឆ្លើយសំណួរបច្ចុប្បន្ន ឬចុច /start។")
            if action == "skip":
                await self.answer(update, owner, None)
            else:
                if state["flow"] != "profile" or not state["data"].get("edit_id") or state["step"] >= len(QUESTIONS["profile"]):
                    raise UserError("មិនមានតម្លៃដើមសម្រាប់សំណួរនេះទេ។")
                key = QUESTIONS["profile"][state["step"]][0]
                value = self.repo.business(owner, state["data"]["edit_id"])["profile"].get(key)
                await self.answer(update, owner, value)
        elif action == "biz":
            if arg in {"new", "edit"}:
                await self.begin(update, owner, "profile", {"edit_id": self.business_id(owner)} if arg == "edit" else {})
            else:
                business = self.repo.business(owner, arg)
                self.repo.set_session(owner, arg)
                await self.say(update, "✅ បានជ្រើស៖ " + business["profile"]["name"], MENU)
        elif action == "ptype" and arg in PLAN_TYPES:
            await self.begin(update, owner, "plan", {"plan_type": PLAN_TYPES[arg]}, self.business_id(owner))
        elif action == "review" and arg == "upload":
            await self.begin(update, owner, "review", business_id=self.business_id(owner))
        elif action == "list" and len(parts) == 3 and arg in {"history", "review", "market"} and parts[2].isdigit():
            await self.list_artifacts(update, owner, arg, min(10000, int(parts[2])))
        elif action == "show":
            await self.deliver(update, owner, self.repo.artifact(owner, arg))
        elif action == "qa":
            self.repo.artifact(owner, arg)
            await self.working(update)
            artifact = await self.service.review(owner, arg, f"review:{arg}:{update.update_id}")
            await self.deliver(update, owner, artifact)
        elif action == "fix":
            self.repo.artifact(owner, arg)
            await self.working(update)
            artifact = await self.service.revise_from_review(owner, arg, f"fix:{arg}:{update.update_id}")
            await self.deliver(update, owner, artifact)
        elif action == "revise":
            artifact = self.repo.artifact(owner, arg)
            if artifact["kind"] != "plan" or artifact["status"] != "complete":
                raise UserError("សូមជ្រើសផែនការពេញលេញ។")
            await self.begin(update, owner, "revision", {"source_id": arg}, artifact["business_id"])
        elif action == "market":
            await self.market_types(update, owner, arg)
        elif action == "mtype" and len(parts) == 3 and parts[2] in MARKETING_TYPES:
            bid = self.business_id(owner) if arg == "profile" else self.repo.artifact(owner, arg)["business_id"]
            details = {"deliverable": MARKETING_TYPES[parts[2]]}
            if arg != "profile":
                details["source_id"] = arg
            await self.begin(update, owner, "marketing", details, bid)
        elif action == "activate":
            self.repo.activate_tasks(owner, arg)
            artifact = self.repo.artifact(owner, arg)
            self.repo.set_session(owner, artifact["business_id"])
            await self.say(update, "✅ បានជ្រើសបញ្ជីការងារពីផែនការនេះ។ បញ្ជីការងារកំណែមុននៅរក្សាទុក តែមិនបង្ហាញជាបញ្ជីសកម្មទៀតទេ។", MENU)
        elif action == "done" and arg.isdigit():
            self.repo.complete_task(owner, int(arg))
            await self.show_tasks(update, owner)
        elif action == "tasks":
            await self.show_tasks(update, owner, all_tasks=arg == "all")
        elif action == "export":
            artifact = self.repo.artifact(owner, arg)
            content = f"{KINDS[artifact['kind']]}\nID: {arg}\nStatus: {artifact['status']}\nCreated (UTC): {artifact['created']}\n\n{artifact['content']}\n"
            if artifact["tasks"]:
                content += "\nការងារ៖\n" + "\n".join(f"- {task['title']} | {task['due_date'] or 'មិនទាន់កំណត់'}" for task in artifact["tasks"])
            file = BytesIO(content.encode("utf-8-sig"))
            await update.effective_message.reply_document(document=file, filename=f"{artifact['kind']}_{arg}.txt")
        elif action == "regen":
            self.repo.artifact(owner, arg)
            await self.working(update)
            artifact = await self.service.regenerate(owner, arg, f"regenerate:{update.update_id}")
            await self.deliver(update, owner, artifact)
        elif action == "retry" and arg == "wizard":
            await self.run_wizard(update, owner)
        else:
            raise UserError("ប៊ូតុងនេះមិនត្រឹមត្រូវទេ។ សូមចុច /start។")
