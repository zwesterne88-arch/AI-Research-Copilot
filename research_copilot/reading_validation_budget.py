"""Explicitly authorized 4.3 admission profile; original QA budget is never reset.

All input UTF-8 bytes +4096 are reserved as an upper token estimate, all outputs
at the configured cap, at verified peak uncached prices, with an extra 2x margin.
Reservations are never refunded, even if an HTTP request fails.
"""
from decimal import Decimal
import json
from pathlib import Path

from .errors import CopilotError
from .llm_budget import DeepSeekValidationBudget
from .repository import now


class Stage43ReadingBudget(DeepSeekValidationBudget):
    SESSION = "stage4_3_p07_deepseek_flash_11calls_4cny"
    MAX_CALLS = 11
    MAX_RESERVED_CNY = Decimal("4")

    def __init__(self, repository):
        policy = json.loads((Path(__file__).resolve().parents[1]/"config/stage4_3_validation_authorization.json").read_text(encoding="utf-8"))
        if (policy["session_id"] != self.SESSION or policy["max_calls"] != 11 or Decimal(policy["max_reserved_cny"]) != Decimal("4")
                or policy["peak_input_cny_per_million"] != "2" or policy["peak_output_cny_per_million"] != "8"):
            raise CopilotError("validation_policy_mismatch", "4.3授权／计费配置不匹配，未发送请求。", "budget")
        self.policy = policy
        super().__init__(repository)
        with self.db.connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS reading_validation_admissions (
                session_id TEXT NOT NULL, call_number INTEGER NOT NULL, stage TEXT NOT NULL,
                input_bytes INTEGER NOT NULL, input_token_upper INTEGER NOT NULL, output_token_upper INTEGER NOT NULL,
                reserved_cny TEXT NOT NULL, cumulative_reserved_cny TEXT NOT NULL,
                remaining_calls INTEGER NOT NULL, remaining_cny TEXT NOT NULL, file_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(session_id,call_number))""")
        self.stage = "unbound"

    def bind_stage(self, stage):
        allowed = {"reading_planning"} | {f"reading_{action}:{module}" for action in ("extract", "review")
            for module in ("metadata", "framing", "approach", "validation", "findings")}
        if stage not in allowed:
            raise CopilotError("validation_scope_mismatch", "本轮额度仅用于已授权的精读验证阶段。", "budget")
        self.stage = stage

    def reserve(self, input_bytes, output_limit):
        if self.stage == "unbound":
            raise CopilotError("validation_scope_mismatch", "本次请求尚未绑定精读阶段。", "budget")
        if not 1 <= input_bytes <= 60000 or not 200 <= output_limit <= 2000:
            raise CopilotError("request_budget_too_large", "输入／输出超过本轮保守Token预算，未发送。", "budget")
        upper = input_bytes + 4096
        amount = 2*((Decimal(upper)*2 + Decimal(output_limit)*8)/1_000_000)
        with self.db.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM qa_api_budget WHERE session_id=?", (self.SESSION,)).fetchone()
            count, total = row["attempted_calls"]+1, Decimal(row["reserved_cny"])+amount
            if count > self.MAX_CALLS or total > self.MAX_RESERVED_CNY:
                raise CopilotError("validation_budget_exhausted", "4.3新增11次／4元额度不足，已停止；不自动扩大授权。", "budget")
            remaining_calls, remaining_cny = self.MAX_CALLS-count, self.MAX_RESERVED_CNY-total
            con.execute("UPDATE qa_api_budget SET attempted_calls=?,reserved_cny=?,updated_at=? WHERE session_id=?",
                        (count, str(total), now(), self.SESSION))
            con.execute("INSERT INTO reading_validation_admissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.SESSION,count,self.stage,input_bytes,upper,output_limit,str(amount),str(total),remaining_calls,str(remaining_cny),self.policy["file_sha256"],now()))
        return {"session_id":self.SESSION, "call_number":count, "max_calls":11, "input_bytes":input_bytes,
                "input_token_upper":upper, "output_token_upper":output_limit, "reservation_cny":str(amount),
                "total_reserved_cny":str(total), "remaining_calls":remaining_calls, "remaining_cny":str(remaining_cny),
                "price_check_date":self.policy["rates_checked_date"], "price_source":self.policy["rates_source"]}
