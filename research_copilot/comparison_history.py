"""Append-only comparison snapshots in a separate local SQLite file."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from .errors import CopilotError
from .repository import now


class ComparisonHistory:
    def __init__(self, root):
        self.path=Path(root)/"comparisons.sqlite3"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS comparison_snapshots (
                comparison_id TEXT NOT NULL, comparison_version INTEGER NOT NULL,
                schema_version TEXT NOT NULL, created_at TEXT NOT NULL, record_json TEXT NOT NULL,
                PRIMARY KEY(comparison_id,comparison_version))""")

    @contextmanager
    def connection(self):
        con=sqlite3.connect(self.path,timeout=10);con.row_factory=sqlite3.Row
        try:
            yield con;con.commit()
        except BaseException:
            con.rollback();raise
        finally:con.close()

    def save(self, record, *, comparison_id=None):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if comparison_id and not con.execute("SELECT 1 FROM comparison_snapshots WHERE comparison_id=?",(comparison_id,)).fetchone():
                raise CopilotError("comparison_not_found","旧比较不存在，不能追加版本。","comparison_storage")
            identifier=comparison_id or "comparison_"+uuid.uuid4().hex
            version=con.execute("SELECT COALESCE(MAX(comparison_version),0)+1 FROM comparison_snapshots WHERE comparison_id=?",(identifier,)).fetchone()[0]
            result={**record,"comparison_id":identifier,"comparison_version":version,"created_at":now()}
            con.execute("INSERT INTO comparison_snapshots VALUES(?,?,?,?,?)",(identifier,version,result["schema_version"],result["created_at"],json.dumps(result,ensure_ascii=False)))
        return result

    def get(self, comparison_id, version):
        with self.connection() as con:
            r=con.execute("SELECT record_json FROM comparison_snapshots WHERE comparison_id=? AND comparison_version=?",(comparison_id,version)).fetchone()
        if not r:raise CopilotError("comparison_not_found","指定比较快照不存在。","comparison_storage")
        return json.loads(r[0])

    def list(self, limit=30):
        with self.connection() as con:
            rows=con.execute("SELECT record_json FROM comparison_snapshots ORDER BY created_at DESC,comparison_version DESC LIMIT ?",(min(max(int(limit),1),100),)).fetchall()
        return [json.loads(r[0]) for r in rows]
