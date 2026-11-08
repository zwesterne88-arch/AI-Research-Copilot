"""Explicit stage4.5 grant and one authoritative ledger across all local libraries.

Provider configuration stays outside RAG. No key, PDF, or historical budget is
copied into the grant. Merely reading the UI does not create/reserve a request.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from .errors import CopilotError
from .repository import now

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/stage4_5_validation_authorization.json"
LEDGER_PATH = ROOT / "verification/stage4_5/private/live_budget.sqlite3"
ARTIFACT_ROOT = ROOT / "verification/stage4_5/private/real_api"
SESSION = "stage4_5_p05_e2e_17calls_5cny"
READING_STAGES = {"reading_planning"} | {f"reading_{step}:{m}" for step in ("extract", "review") for m in ("metadata", "framing", "approach", "validation", "findings")}
QA_STAGES = {"query_planning", "answering", "support_verification"}


def policy():
    if not POLICY_PATH.is_file():
        return None
    obj = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if obj.get("status") != "approved":
        return None
    expected = {"session_id": SESSION, "max_calls": 17, "max_reserved_cny": "5", "max_reading_calls": 11,
                "max_qa_calls": 6, "peak_input_cny_per_million": "2", "peak_output_cny_per_million": "8"}
    if any(obj.get(k) != v for k, v in expected.items()):
        raise CopilotError("validation_policy_mismatch", "4.5授权与预算配置不一致，未发送。", "budget")
    return obj


def approved_hashes():
    p = policy()
    return {p["file_sha256"]} if p else set()


def applies(doc):
    return doc["file_sha256"] in approved_hashes()


def summary():
    if not LEDGER_PATH.is_file():
        return {"session_id": SESSION, "attempted_calls": 0, "reserved_cny": "0", "closed": 0}
    with sqlite3.connect(LEDGER_PATH.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM session_budget WHERE session_id=?", (SESSION,)).fetchone()
        return dict(row) if row else {"session_id": SESSION, "attempted_calls": 0, "reserved_cny": "0", "closed": 0}


def ui_state(doc):
    if not applies(doc):
        return None
    row = summary()
    return {**row, "exhausted": bool(row["closed"] or row["attempted_calls"] >= 17 or Decimal(row["reserved_cny"]) >= 5),
            "max_resume_depth": RecordedSessionClient.max_resume_depth,
            "notice": f"4.5独立P05授权：已用{row['attempted_calls']}/17次，预算预占{row['reserved_cny']}/5元；精读与问答共用，不重置历史额度。"}


class Stage45Budget:
    SESSION = SESSION
    MAX_CALLS = 17
    MAX_RESERVED_CNY = Decimal("5")

    def __init__(self):
        self.policy = policy()
        if not self.policy:
            raise CopilotError("source_consent_required", "4.5尚未获明确授权。", "authorization")
        self.path = LEDGER_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS session_budget(session_id TEXT PRIMARY KEY,attempted_calls INTEGER NOT NULL,reserved_cny TEXT NOT NULL,closed INTEGER NOT NULL,updated_at TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS admissions(call_number INTEGER PRIMARY KEY,stage TEXT NOT NULL,request_fingerprint TEXT NOT NULL UNIQUE,input_bytes INTEGER NOT NULL,input_token_upper INTEGER NOT NULL,output_token_upper INTEGER NOT NULL,reservation_cny TEXT NOT NULL,cumulative_cny TEXT NOT NULL,file_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)")
            con.execute("INSERT OR IGNORE INTO session_budget VALUES(?,0,'0',0,?)", (SESSION, now()))
            con.execute("CREATE TABLE IF NOT EXISTS manual_retry_permits(failed_call INTEGER PRIMARY KEY,reason TEXT NOT NULL,created_at TEXT NOT NULL,used INTEGER NOT NULL)")
        self.stage = None
        self.fingerprint = None

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def bind(self, stage, fingerprint):
        if stage not in READING_STAGES | QA_STAGES:
            raise CopilotError("validation_scope_mismatch", "请求阶段不在4.5授权范围。", "budget")
        self.stage, self.fingerprint = stage, fingerprint

    def reserve(self, input_bytes, output_limit):
        if self.stage is None or not self.fingerprint or not 1 <= input_bytes <= 60000 or not 200 <= output_limit <= 2000:
            raise CopilotError("request_budget_too_large", "请求未绑定阶段，或超过60000字节／2000输出token，未发送。", "budget")
        upper = input_bytes + 4096
        amount = 2 * (Decimal(upper) * 2 + Decimal(output_limit) * 8) / 1_000_000
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM session_budget WHERE session_id=?", (SESSION,)).fetchone()
            total = Decimal(row["reserved_cny"]) + amount
            if row["closed"] or row["attempted_calls"] >= 17 or total > 5:
                raise CopilotError("validation_budget_exhausted", "4.5独立调用额度或5元预算不足／已结束，未发送。", "budget")
            prior = con.execute("SELECT stage,request_fingerprint FROM admissions").fetchall()
            same_stage = sum(r["stage"] == self.stage for r in prior)
            fingerprint = self.fingerprint
            retry_of = None
            if any(r["request_fingerprint"] == self.fingerprint for r in prior) or same_stage >= (1 if self.stage in READING_STAGES else 2):
                permit = con.execute("SELECT * FROM manual_retry_permits WHERE failed_call=1 AND used=0").fetchone()
                if not permit or self.stage != "reading_planning" or same_stage != 1:
                    raise CopilotError("validation_stage_already_attempted", "本轮该阶段或相同输入已调用，未自动重复收费。", "budget")
                retry_of = 1
                fingerprint += ":manual-retry-of-1"
                con.execute("UPDATE manual_retry_permits SET used=1 WHERE failed_call=1")
            count = row["attempted_calls"] + 1
            con.execute("UPDATE session_budget SET attempted_calls=?,reserved_cny=?,updated_at=? WHERE session_id=?", (count, str(total), now(), SESSION))
            con.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,?,?)", (count, self.stage, fingerprint, input_bytes, upper, output_limit, str(amount), str(total), self.policy["file_sha256"], now()))
        return {"session_id": SESSION, "call_number": count, "max_calls": 17, "input_bytes": input_bytes, "input_token_upper": upper,
                "output_token_upper": output_limit, "reservation_cny": str(amount), "total_reserved_cny": str(total),
                "remaining_calls": 17-count, "remaining_cny": str(Decimal(5)-total), "price_check_date": self.policy["rates_checked_date"], "manual_retry_of": retry_of}

    def permit_one_manual_network_retry(self, reason):
        """Explicit operator action for the first local connection failure only.

        Never invoked by the UI or adapter. Original attempt stays charged to the
        17-count/5-CNY admission ledger; a second failure cannot obtain a permit.
        """
        trace = json.loads((ARTIFACT_ROOT/"trace_01.json").read_text(encoding="utf-8"))
        if trace.get("status") != "failed" or trace.get("stage") != "reading_planning" or trace.get("usage") or trace.get("http_status") or not reason:
            raise CopilotError("validation_retry_not_allowed", "不是首个无模型响应的网络失败，未允许重试。", "budget")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM session_budget WHERE session_id=?", (SESSION,)).fetchone()
            if row["attempted_calls"] != 1 or row["closed"] or con.execute("SELECT 1 FROM manual_retry_permits").fetchone():
                raise CopilotError("validation_retry_not_allowed", "仅允许一次显式网络修复后重试，不增加总额度。", "budget")
            con.execute("INSERT INTO manual_retry_permits VALUES(1,?,?,0)", (reason, now()))

    def estimate(self, usage):
        if not all(isinstance(usage.get(k), int) for k in ("prompt_tokens", "completion_tokens")):
            return None
        return str((Decimal(usage["prompt_tokens"])*2 + Decimal(usage["completion_tokens"])*8)/1_000_000)

    def summary(self):
        with self.connection() as con:
            return dict(con.execute("SELECT * FROM session_budget WHERE session_id=?", (SESSION,)).fetchone())

    def close(self):
        with self.connection() as con:
            con.execute("UPDATE session_budget SET closed=1,updated_at=? WHERE session_id=?", (now(), SESSION))


class RecordedSessionClient:
    """Audit wrapper over the existing adapter, with no independent HTTP path."""
    is_test_double = False
    # One local-network failure plus two bounded contract repairs in this grant.
    # All stages still share the SAME 17-attempt ledger; no automatic retries.
    max_resume_depth = 3

    def __init__(self, adapter, doc):
        self.adapter, self.config, self.calls, self.budget = adapter, adapter.config, adapter.calls, adapter.budget
        p = self.budget.policy
        if doc["file_sha256"] != p["file_sha256"] or any(getattr(self.config, k) != p[k] for k in ("provider", "model", "base_url")):
            raise CopilotError("validation_scope_mismatch", "资料或模型不属于4.5授权，未发送。", "authorization")
        self.doc = doc
        self.current = None
        self.adapter.response_observer = self._response

    def dump(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, ensure_ascii=False, indent=2).replace(self.config.api_key, "[REDACTED]")
        # Exclusive creation: original requests/responses/traces are never replaced.
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def _response(self, stage, raw):
        number = self.budget.summary()["attempted_calls"]
        self.dump(ARTIFACT_ROOT/f"response_{number:02d}.json", {"stage": stage, "request_fingerprint": self.current,
                  "body_utf8": raw.decode("utf-8", errors="replace")})

    def complete_json(self, stage, system, user):
        request = {"stage": stage, "file_sha256": self.doc["file_sha256"], "model_info": self.config.public_info(), "system": system, "user": user}
        key = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.budget.bind(stage, key)
        self.current = key
        request_path = ARTIFACT_ROOT/"requests"/(key+".json")
        if request_path.exists() and json.loads(request_path.read_text(encoding="utf-8")) == json.loads(json.dumps(request, ensure_ascii=False)):
            # Replay only an exact completed response from this same authorized
            # session/model/input. This is local reuse, never another HTTP request.
            for path in sorted(ARTIFACT_ROOT.glob("response_*.json")):
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("request_fingerprint") != key:
                    continue
                number = int(path.stem.split("_")[-1])
                trace = json.loads((ARTIFACT_ROOT/f"trace_{number:02d}.json").read_text(encoding="utf-8"))
                if trace.get("status") != "completed" or trace.get("scope_sha256") != self.doc["file_sha256"]:
                    continue
                body = json.loads(saved["body_utf8"])
                content = body["choices"][0]["message"]["content"].strip()
                if content.startswith("```json\n") and content.endswith("```"):
                    content = content[8:-3].strip()
                result = json.loads(content)
                if not isinstance(result, dict):
                    continue
                self.calls.append({"stage":stage, "model":self.config.model, "status":"not_sent", "response_reused":True,
                                   "original_session_id":SESSION, "original_call_number":number, "request_fingerprint":key})
                return deepcopy(result)
        if not request_path.exists():
            self.dump(request_path, request)
        start = len(self.calls)
        try:
            return self.adapter.complete_json(stage, system, user)
        finally:
            for trace in self.calls[start:]:
                trace.update(request_fingerprint=key, scope_sha256=self.doc["file_sha256"])
                if "budget_reservation" in trace:
                    number = trace["budget_reservation"]["call_number"]
                    self.dump(ARTIFACT_ROOT/f"trace_{number:02d}.json", trace)


def client_for_document(config, repository, doc):
    from .llm import create_client
    adapter = create_client(config, repository, validation_profile=SESSION)
    return RecordedSessionClient(adapter, doc)
