from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "library.sqlite3"
        with self.connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            if con.execute("SELECT version FROM schema_meta").fetchone()[0] != 1:
                raise RuntimeError("Unsupported database schema version; refusing to overwrite.")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def rows(self, sql, params=()):
        with self.connection() as con:
            return [dict(x) for x in con.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def update(self, table, key, value, **fields):
        allowed = {"documents": {"source_status", "parse_status", "index_status", "parser_version", "chunker_version", "index_version", "error_code", "error_stage", "error_message"},
                   "jobs": {"document_version_id", "status", "stage", "processed_pages", "error_code", "error_message"}}
        if table not in allowed or not fields.keys() <= allowed[table] or key != {"documents":"document_version_id","jobs":"job_id"}[table]:
            raise ValueError("Unsupported update columns")
        fields["updated_at"] = now()
        with self.connection() as con:
            con.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) + f" WHERE {key}=?", (*fields.values(), value))
