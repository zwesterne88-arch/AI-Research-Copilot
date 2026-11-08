"""Additive QA history table; leaves the stage4.1 core schema and records intact."""
import json

from .repository import now
from .errors import CopilotError


class QAHistory:
    def __init__(self, repository):
        self.db = repository
        with self.db.connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS qa_runs (
                run_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, document_version_id TEXT NOT NULL,
                question TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(document_version_id,paper_id) REFERENCES documents(document_version_id,paper_id))""")
            con.execute("CREATE INDEX IF NOT EXISTS qa_scope ON qa_runs(paper_id,document_version_id,created_at)")

    def save(self, result):
        with self.db.connection() as con:
            con.execute("""INSERT INTO qa_runs VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,result_json=excluded.result_json,updated_at=excluded.updated_at""",
                (result["run_id"],result["paper_id"],result["document_version_id"],result["question"],result["status"],
                 json.dumps(result,ensure_ascii=False),result["created_at"],now()))

    def list(self, paper_id, version_id, limit=10):
        return [json.loads(row["result_json"]) for row in self.db.rows(
            "SELECT result_json FROM qa_runs WHERE paper_id=? AND document_version_id=? ORDER BY created_at DESC LIMIT ?",
            (paper_id,version_id,min(max(int(limit),1),50)))]

    def get(self,run_id):
        row=self.db.one("SELECT result_json FROM qa_runs WHERE run_id=?",(run_id,))
        if not row:
            raise CopilotError("qa_run_not_found","问答记录不存在。","candidate_resume")
        return json.loads(row["result_json"])

    def cached_plan(self,doc,question,model,planner_version):
        for run in self.list(doc["paper_id"],doc["document_version_id"],limit=50):
            if run["question"] != question.strip() or run.get("execution_mode") != "real_api":
                continue
            legacy = planner_version == "research-query-v1" and run.get("prompt_version") == "research-logic-evidence-qa-v1"
            compatible = run.get("planner_version") == planner_version or legacy
            queries = run.get("retrieval_queries")
            sources = run.get("sources",[])
            same_pipeline = sources and all(sources[0].get(k)==doc[k] for k in ("file_sha256","parser_version","chunker_version","index_version"))
            same_model = run.get("model_info",{}).get("model")==model or any(t.get("model")==model and t.get("stage")=="query_planning" and t.get("status")=="completed" for t in run.get("request_traces",[]))
            if compatible and queries and same_pipeline and same_model:
                return {"queries_en":queries,"source_run_id":run["run_id"]}
        return None
