"""Conservative, persistent admission budget for this user-authorized validation.

Peak uncached rates checked 2026-10-26: 2 CNY/M input, 8 CNY/M output.
This is a local request/token ceiling and cost reservation, NOT an account invoice.
"""
from decimal import Decimal

from .errors import CopilotError
from .repository import now


class DeepSeekValidationBudget:
    SESSION = "stage4_2_deepseek_flash_10cny"
    MAX_CALLS = 30
    MAX_RESERVED_CNY = Decimal("10")

    def __init__(self, repository):
        self.db = repository
        with self.db.connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS qa_api_budget (
                session_id TEXT PRIMARY KEY, attempted_calls INTEGER NOT NULL,
                reserved_cny TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            con.execute("INSERT OR IGNORE INTO qa_api_budget VALUES(?,?,?,?)",(self.SESSION,0,"0",now()))

    def reserve(self, input_bytes, output_limit):
        if not 1 <= input_bytes <= 60_000 or not 200 <= output_limit <= 2000:
            raise CopilotError("request_budget_too_large","本轮每次请求上限60000字节、2000输出token，未发送。","budget")
        # UTF-8 byte count + generous protocol overhead, then a 2x price/token margin.
        amount = 2 * ((Decimal(input_bytes+4096)*2 + Decimal(output_limit)*8)/1_000_000)
        with self.db.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM qa_api_budget WHERE session_id=?",(self.SESSION,)).fetchone()
            total = Decimal(row["reserved_cny"]) + amount
            if row["attempted_calls"] >= self.MAX_CALLS or total > self.MAX_RESERVED_CNY:
                raise CopilotError("validation_budget_exhausted","本轮累计调用次数或预算预占达到上限；不会自动扩大预算。","budget")
            con.execute("UPDATE qa_api_budget SET attempted_calls=attempted_calls+1,reserved_cny=?,updated_at=? WHERE session_id=?",(str(total),now(),self.SESSION))
        return {"reservation_cny":str(amount),"total_reserved_cny":str(total),"call_number":row["attempted_calls"]+1,
                "max_calls":self.MAX_CALLS,"price_check_date":"2026-10-26"}

    def estimate(self, usage):
        if not isinstance(usage.get("prompt_tokens"),int) or not isinstance(usage.get("completion_tokens"),int):
            return None
        return str((Decimal(usage["prompt_tokens"])*2+Decimal(usage["completion_tokens"])*8)/1_000_000)

    def summary(self):
        return self.db.one("SELECT * FROM qa_api_budget WHERE session_id=?",(self.SESSION,))
