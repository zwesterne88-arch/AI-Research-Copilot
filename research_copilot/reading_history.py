"""Append versions; checkpoint only a running card. Original model outputs are retained."""
import json
from pathlib import Path
import uuid

from .errors import CopilotError
from .files import writer_lock
from .repository import now


class ReadingHistory:
    def __init__(self, repository):
        self.db = repository
        with self.db.connection() as con:
            con.executescript(Path(__file__).with_name("reading_schema.sql").read_text(encoding="utf-8"))

    def list(self, paper_id, version_id, limit=20):
        return [json.loads(r["record_json"]) for r in self.db.rows(
            "SELECT record_json FROM reading_cards WHERE paper_id=? AND document_version_id=? ORDER BY card_version DESC LIMIT ?",
            (paper_id, version_id, min(max(int(limit), 1), 100)))]

    def get(self, card_id):
        row = self.db.one("SELECT record_json FROM reading_cards WHERE card_id=?", (card_id,))
        if not row:
            raise CopilotError("card_not_found", "阅读卡不存在。", "reading_selection")
        return json.loads(row["record_json"])

    def create(self, record):
        with self.db.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            version = con.execute("SELECT COALESCE(MAX(card_version),0)+1 FROM reading_cards WHERE document_version_id=?", (record["document_version_id"],)).fetchone()[0]
            record.update(card_id="card_" + uuid.uuid4().hex, card_version=version, created_at=now(), updated_at=now())
            con.execute("INSERT INTO reading_cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (record["card_id"], record["paper_id"], record["document_version_id"], record["file_sha256"], version,
                         record["schema_version"], record["prompt_version"], record["model_id"], record["card_status"],
                         json.dumps(record, ensure_ascii=False), record["created_at"], record["updated_at"]))
        return record

    def save(self, record):
        record["updated_at"] = now()
        with self.db.connection() as con:
            row = con.execute("SELECT card_status,record_json FROM reading_cards WHERE card_id=?", (record["card_id"],)).fetchone()
            if not row or row["card_status"] != "processing":
                raise CopilotError("card_immutable", "已结束卡片不可覆盖，请建立新版本。", "reading_storage")
            old = json.loads(row["record_json"])
            if any(record.get(k) != old.get(k) for k in ("paper_id", "document_version_id", "file_sha256", "page_count", "pipeline", "card_version", "schema_version", "prompt_version", "planner_version", "retrieval_version", "execution_mode", "model_id", "model_info", "created_at")):
                raise CopilotError("card_immutable", "卡片来源或版本元数据不可修改。", "reading_storage")
            # Once an output is recorded, a checkpoint cannot silently replace it.
            if any(record.get("raw_outputs", {}).get(k) != v for k, v in old.get("raw_outputs", {}).items()):
                raise CopilotError("card_immutable", "原始模型输出不可覆盖。", "reading_storage")
            con.execute("UPDATE reading_cards SET card_status=?,record_json=?,updated_at=? WHERE card_id=?",
                        (record["card_status"], json.dumps(record, ensure_ascii=False), record["updated_at"], record["card_id"]))

    def configure(self, record, info, model_id):
        """One initialization assignment, before any model output; cannot change a running model."""
        with self.db.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT record_json FROM reading_cards WHERE card_id=?", (record["card_id"],)).fetchone()
            old = json.loads(row["record_json"])
            if old["card_status"] != "processing" or old["stage"] != "configuration" or old["raw_outputs"] or old.get("configured"):
                raise CopilotError("card_immutable", "不能更换已开始提取任务的模型。", "reading_storage")
            record.update(model_id=model_id, model_info=info, configured=True)
            con.execute("UPDATE reading_cards SET model_id=?,record_json=? WHERE card_id=?", (model_id, json.dumps(record, ensure_ascii=False), record["card_id"]))

    def recover_interrupted(self, root):
        try:
            with writer_lock(root):
                rows = self.db.rows("SELECT record_json FROM reading_cards WHERE card_status='processing'")
                for row in rows:
                    record = json.loads(row["record_json"])
                    record.update(card_status="failed", stage="interrupted", error={"code":"reading_interrupted", "stage":"interrupted",
                                  "message":"上次精读任务被中断，可手动续跑为新版本；不会自动调用模型。", "retryable":True})
                    record.update(failure_kind="technical_interruption", failure_module=record.get("active_module"))
                    self.save(record)
        except CopilotError as exc:
            if exc.code != "busy": raise
