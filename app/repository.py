from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .domain import UserError


def encode(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database version; preserve the database and migrate it.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS businesses (
                    id TEXT PRIMARY KEY, owner INTEGER NOT NULL, profile TEXT NOT NULL,
                    created TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS businesses_owner ON businesses(owner);
                CREATE TABLE IF NOT EXISTS sessions (
                    owner INTEGER PRIMARY KEY, business_id TEXT, flow TEXT NOT NULL DEFAULT '',
                    step INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, owner INTEGER NOT NULL, business_id TEXT NOT NULL,
                    kind TEXT NOT NULL, parent_id TEXT, content TEXT NOT NULL,
                    tasks TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL,
                    stop_reason TEXT NOT NULL, delivery TEXT NOT NULL DEFAULT 'pending',
                    created TEXT NOT NULL,
                    FOREIGN KEY (business_id) REFERENCES businesses(id)
                );
                CREATE INDEX IF NOT EXISTS artifacts_owner ON artifacts(owner,business_id,created);
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL,
                    business_id TEXT NOT NULL, plan_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    title TEXT NOT NULL, due_date TEXT, done INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1, UNIQUE(plan_id,ordinal),
                    FOREIGN KEY (plan_id) REFERENCES artifacts(id)
                );
                CREATE TABLE IF NOT EXISTS usage (
                    owner INTEGER NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(owner,day)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    owner INTEGER NOT NULL, request_key TEXT NOT NULL, status TEXT NOT NULL,
                    artifact_id TEXT, created TEXT NOT NULL, PRIMARY KEY(owner,request_key)
                );
                PRAGMA user_version=1;
            """)
            # A previous process may have stopped after reserving a generation.
            # Never automatically repeat an outbound API call after a restart.
            db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def session(self, owner: int) -> dict:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO sessions(owner) VALUES(?)", (owner,))
            row = dict(db.execute("SELECT * FROM sessions WHERE owner=?", (owner,)).fetchone())
        row["data"] = json.loads(row["data"])
        return row

    def set_session(self, owner: int, business_id, flow="", step=0, data=None):
        if business_id is not None:
            self.business(owner, business_id)
        with self.connection() as db:
            db.execute("""INSERT INTO sessions(owner,business_id,flow,step,data) VALUES(?,?,?,?,?)
                ON CONFLICT(owner) DO UPDATE SET business_id=excluded.business_id,
                flow=excluded.flow,step=excluded.step,data=excluded.data""",
                (owner, business_id, flow, step, encode(data or {})))

    def save_business(self, owner: int, profile: dict, business_id=None) -> str:
        if business_id:
            self.business(owner, business_id)
        identifier = business_id or new_id()
        with self.connection() as db:
            if business_id:
                db.execute("UPDATE businesses SET profile=? WHERE id=? AND owner=?", (encode(profile), identifier, owner))
            else:
                db.execute("INSERT INTO businesses VALUES(?,?,?,?)", (identifier, owner, encode(profile), now()))
        self.set_session(owner, identifier)
        return identifier

    def business(self, owner: int, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM businesses WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if row is None:
            raise UserError("រកមិនឃើញអាជីវកម្មនេះក្នុងគណនីរបស់អ្នកទេ។")
        result = dict(row)
        result["profile"] = json.loads(result["profile"])
        return result

    def businesses(self, owner: int) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT id FROM businesses WHERE owner=? ORDER BY created DESC LIMIT 30", (owner,)).fetchall()
        return [self.business(owner, row["id"]) for row in rows]

    def delete_business(self, owner: int, identifier: str) -> str:
        biz = self.business(owner, identifier)
        name = biz["profile"].get("name", "អាជីវកម្ម")
        with self.connection() as db:
            db.execute("DELETE FROM tasks WHERE owner=? AND business_id=?", (owner, identifier))
            db.execute("DELETE FROM artifacts WHERE owner=? AND business_id=?", (owner, identifier))
            db.execute("DELETE FROM businesses WHERE id=? AND owner=?", (identifier, owner))
            sess = db.execute("SELECT business_id FROM sessions WHERE owner=?", (owner,)).fetchone()
            if sess and sess[0] == identifier:
                other = db.execute("SELECT id FROM businesses WHERE owner=? ORDER BY created DESC LIMIT 1", (owner,)).fetchone()
                other_id = other[0] if other else None
                db.execute("UPDATE sessions SET business_id=?, flow='', step=0, data='{}' WHERE owner=?", (other_id, owner))
        return name

    def artifact(self, owner: int, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if row is None:
            raise UserError("រកមិនឃើញឯកសារនេះក្នុងគណនីរបស់អ្នកទេ។")
        result = dict(row)
        for key in ("request", "tasks"):
            result[key] = json.loads(result[key])
        return result

    def artifacts(self, owner: int, business_id: str, kind=None, offset=0) -> list[dict]:
        self.business(owner, business_id)
        query = "SELECT id FROM artifacts WHERE owner=? AND business_id=?"
        params = [owner, business_id]
        if kind:
            query += " AND kind=? AND status='complete'"
            params.append(kind)
        query += " ORDER BY created DESC, rowid DESC LIMIT 8 OFFSET ?"
        params.append(offset)
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self.artifact(owner, row["id"]) for row in rows]

    def reserve(self, owner: int, key: str, day: str, limit: int) -> str | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM runs WHERE owner=? AND request_key=?", (owner, key)).fetchone()
            if previous:
                if previous["status"] == "succeeded":
                    return previous["artifact_id"]
                if previous["status"] == "running":
                    raise UserError("សំណើនេះកំពុងដំណើរការ សូមរង់ចាំ។")
                raise UserError("សំណើមុនមិនបានបញ្ចប់។ សូមចុចសាកល្បងម្ដងទៀត ឬចាប់ផ្ដើមសំណើថ្មី។")
            db.execute("INSERT OR IGNORE INTO usage(owner,day) VALUES(?,?)", (owner, day))
            count = db.execute("SELECT requests FROM usage WHERE owner=? AND day=?", (owner, day)).fetchone()[0]
            if count >= limit:
                raise UserError("អ្នកបានដល់ចំនួនសំណើ AI ប្រចាំថ្ងៃហើយ។ អាចបើកឯកសារចាស់បាន ឬសាកល្បងថ្ងៃស្អែក។")
            db.execute("UPDATE usage SET requests=requests+1 WHERE owner=? AND day=?", (owner, day))
            db.execute("INSERT INTO runs VALUES(?,?,'running',NULL,?)", (owner, key, now()))
        return None

    def failed_run(self, owner: int, key: str):
        with self.connection() as db:
            db.execute("UPDATE runs SET status='failed' WHERE owner=? AND request_key=? AND status='running'", (owner, key))

    def cached_result(self, owner: int, key: str):
        with self.connection() as db:
            row = db.execute("SELECT artifact_id FROM runs WHERE owner=? AND request_key=? AND status='succeeded'", (owner, key)).fetchone()
        return self.artifact(owner, row[0]) if row else None

    def cache_result(self, owner: int, key: str, artifact_id: str):
        self.artifact(owner, artifact_id)
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES(?,?,'succeeded',?,?)", (owner, key, artifact_id, now()))

    def tokens(self, owner: int, day: str, incoming: int, outgoing: int):
        with self.connection() as db:
            db.execute("UPDATE usage SET input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE owner=? AND day=?",
                       (max(0, incoming), max(0, outgoing), owner, day))

    def finish(self, owner: int, key: str, business_id: str, kind: str, parent_id,
               content: str, tasks: list, request: dict, status: str, stop_reason: str) -> dict:
        self.business(owner, business_id)
        if parent_id:
            parent = self.artifact(owner, parent_id)
            if parent["business_id"] != business_id:
                raise UserError("ឯកសារទាំងនេះមិនមែនជាអាជីវកម្មដូចគ្នាទេ។")
        identifier = new_id()
        with self.connection() as db:
            db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)",
                       (identifier, owner, business_id, kind, parent_id, content, encode(tasks), encode(request), status, stop_reason, now()))
            db.execute("UPDATE runs SET status='succeeded',artifact_id=? WHERE owner=? AND request_key=?",
                       (identifier, owner, key))
        return self.artifact(owner, identifier)

    def import_plan(self, owner: int, business_id: str, content: str) -> dict:
        profile = self.business(owner, business_id)["profile"]
        if not content.strip() or len(content) > 60000:
            raise UserError("សូមផ្ញើផែនការជាអត្ថបទមិនលើស ៦០០០០ តួអក្សរ។")
        identifier = new_id()
        request = {"profile": profile, "brief": {}, "imported": True}
        with self.connection() as db:
            db.execute("INSERT INTO artifacts VALUES(?,?,?,'plan',NULL,?,'[]',?,'complete','user_import','pending',?)",
                       (identifier, owner, business_id, content, encode(request), now()))
        return self.artifact(owner, identifier)

    def delivery(self, owner: int, identifier: str, status: str):
        if status not in {"pending", "partial", "sent", "failed"}:
            raise ValueError("invalid delivery status")
        self.artifact(owner, identifier)
        with self.connection() as db:
            db.execute("UPDATE artifacts SET delivery=? WHERE owner=? AND id=?", (status, owner, identifier))

    def activate_tasks(self, owner: int, identifier: str):
        plan = self.artifact(owner, identifier)
        if plan["kind"] != "plan" or plan["status"] != "complete":
            raise UserError("សូមជ្រើសផែនការដែលបង្កើតបានពេញលេញ។")
        if not plan["tasks"]:
            raise UserError("ផែនការនេះមិនទាន់មានបញ្ជីការងារ។ សូមកែផែនការដោយ Planner ជាមុន។")
        with self.connection() as db:
            db.execute("UPDATE tasks SET active=0 WHERE owner=? AND business_id=?", (owner, plan["business_id"]))
            for index, task in enumerate(plan["tasks"]):
                db.execute("""INSERT INTO tasks(owner,business_id,plan_id,ordinal,title,due_date)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,ordinal) DO UPDATE SET active=1""",
                    (owner, plan["business_id"], identifier, index, task["title"], task["due_date"]))

    def tasks(self, owner: int, business_id: str, today: str, all_tasks=False) -> list[dict]:
        self.business(owner, business_id)
        query = "SELECT * FROM tasks WHERE owner=? AND business_id=? AND active=1 AND done=0"
        params = [owner, business_id]
        if not all_tasks:
            query += " AND (due_date IS NULL OR due_date<=?)"
            params.append(today)
        query += " ORDER BY COALESCE(due_date,'9999'),id"
        with self.connection() as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def complete_task(self, owner: int, task_id: int):
        with self.connection() as db:
            changed = db.execute("UPDATE tasks SET done=1 WHERE owner=? AND id=? AND active=1", (owner, task_id)).rowcount
        if not changed:
            raise UserError("រកមិនឃើញការងារនេះក្នុងគណនីរបស់អ្នកទេ។")

    def usage(self, owner: int, day: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM usage WHERE owner=? AND day=?", (owner, day)).fetchone()
        return dict(row) if row else {"requests": 0, "input_tokens": 0, "output_tokens": 0}
