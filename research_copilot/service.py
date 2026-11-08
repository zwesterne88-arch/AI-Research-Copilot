"""UI-independent application service. All document queries bind paper AND version."""
from pathlib import Path
import json
import uuid

from .errors import CopilotError
from .files import FileStore, digest, display_name, validate_bytes, writer_lock
from .pdf_parser import inspect_pdf, parse_pdf, PARSER_VERSION
from .chunking import make_chunks, CHUNKER_VERSION
from .retrieval import rank, tokens, term_counts, INDEX_VERSION
from .repository import Repository, now

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class CopilotService:
    def __init__(self, data_dir=None):
        self.root = Path(data_dir or DEFAULT_DATA_DIR).resolve()
        self.store = FileStore(self.root)
        self.db = Repository(self.root)
        self.recover_interrupted()

    def answer_question(self, paper_id, question, *, document_version_id, allow_remote=False, progress=None):
        from .rag import PaperQA
        return PaperQA(self).answer_question(paper_id,question,document_version_id=document_version_id,
                                            allow_remote=allow_remote,progress=progress)

    def generate_reading_card(self, paper_id, *, document_version_id, allow_remote=False, regenerate=False, resume_card_id=None, progress=None):
        from .reading import StructuredReading
        return StructuredReading(self).generate(paper_id, document_version_id=document_version_id, allow_remote=allow_remote,
            regenerate=regenerate, resume_card_id=resume_card_id, progress=progress)

    def list_documents(self):
        return self.db.rows("""SELECT d.*,p.title,
            (SELECT count(*) FROM chunks c WHERE c.document_version_id=d.document_version_id) chunk_count,
            (SELECT count(*) FROM pages g WHERE g.document_version_id=d.document_version_id AND g.status='text_present') indexed_page_count
            FROM documents d JOIN papers p USING(paper_id) ORDER BY d.created_at DESC,d.document_version_id""")

    def get_document(self, version_id):
        doc = self.db.one("SELECT d.*,p.title FROM documents d JOIN papers p USING(paper_id) WHERE document_version_id=?", (version_id,))
        if not doc:
            raise CopilotError("document_not_found", "文件版本不存在。", "selection")
        return doc

    def get_pages(self, version_id):
        self.get_document(version_id)
        return self.db.rows("SELECT * FROM pages WHERE document_version_id=? ORDER BY pdf_page", (version_id,))

    def jobs(self, limit=20):
        return self.db.rows("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (min(max(int(limit), 1), 100),))

    def _job(self, name, sha256=None, version_id=None):
        job = "job_" + uuid.uuid4().hex
        with self.db.connection() as con:
            con.execute("""INSERT INTO jobs(job_id,document_version_id,source_file,file_sha256,status,stage,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""", (job, version_id, name, sha256, "processing", "validation", now(), now()))
        return job

    def _error(self, job, exc, version_id=None):
        self.db.update("jobs", "job_id", job, status="rejected" if exc.stage == "validation" else "failed",
                       stage=exc.stage, error_code=exc.code, error_message=exc.message)
        if version_id:
            fields = {"error_code": exc.code, "error_stage": exc.stage, "error_message": exc.message}
            if exc.stage == "parsing":
                fields.update(parse_status="failed", index_status="not_started")
            elif exc.stage == "storage":
                fields.update(source_status=exc.code, index_status="stale")
            else:
                fields.update(index_status="failed")
            self.db.update("documents", "document_version_id", version_id, **fields)
        return {"job_id": job, "status": "rejected" if exc.stage == "validation" else "failed",
                "document_version_id": version_id, "error": exc.as_dict()}

    def import_pdf(self, data: bytes, filename: str, *, paper_id=None, progress=None):
        """Import actual bytes. Optional paper_id explicitly links a revised file.

        Duplicate SHA returns the existing version without re-parsing/re-indexing.
        Re-uploading exact bytes can repair a missing/corrupt controlled source.
        """
        with writer_lock(self.root):
            name = display_name(filename)
            sha256 = digest(data)
            job = self._job(name, sha256)
            version = None
            try:
                if progress:
                    progress("validation", 0, 0)
                validate_bytes(data, name)
                if paper_id and not self.db.one("SELECT paper_id FROM papers WHERE paper_id=?", (paper_id,)):
                    raise CopilotError("paper_not_found", "指定论文不存在，不能关联文件版本。", "validation")
                existing = self.db.one("SELECT * FROM documents WHERE file_sha256=?", (sha256,))
                if existing:
                    if paper_id and paper_id != existing["paper_id"]:
                        raise CopilotError("duplicate_other_paper", "相同文件已属于另一论文，请打开已有记录。", "validation")
                    self.store.save(data, sha256)
                    version = existing["document_version_id"]
                    self.db.update("documents", "document_version_id", version, source_status="stored")
                    self.db.update("jobs", "job_id", job, document_version_id=version, status="duplicate", stage="completed")
                    return {"job_id": job, "status": "duplicate", "document_version_id": version,
                            "paper_id": existing["paper_id"], "data": self.get_document(version), "error": None}
                page_count, metadata_title = inspect_pdf(data)
                path = self.store.save(data, sha256)
                paper_id = paper_id or "paper_" + uuid.uuid4().hex
                version = "sha256:" + sha256
                with self.db.connection() as con:
                    con.execute("INSERT OR IGNORE INTO papers VALUES(?,?,?,?)", (paper_id, metadata_title or name, now(), now()))
                    con.execute("""INSERT INTO documents(document_version_id,paper_id,file_sha256,source_file,stored_path,
                        size_bytes,page_count,metadata_title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (version, paper_id, sha256, name, path.relative_to(self.root).as_posix(), len(data), page_count, metadata_title, now(), now()))
                    con.execute("UPDATE papers SET updated_at=? WHERE paper_id=?", (now(), paper_id))
                    con.execute("UPDATE jobs SET document_version_id=?,stage='stored',updated_at=? WHERE job_id=?", (version, now(), job))
                return self._process(version, job, progress)
            except CopilotError as exc:
                # A failed registration must not refer to a non-existent FK.
                recorded = version if version and self.db.one("SELECT document_version_id FROM documents WHERE document_version_id=?", (version,)) else None
                return self._error(job, exc, recorded)
            except Exception as exc:
                recorded = version if version and self.db.one("SELECT document_version_id FROM documents WHERE document_version_id=?", (version,)) else None
                return self._error(job, CopilotError("import_failed", f"导入未完成（{type(exc).__name__}），可查看任务阶段并重试。", "storage", True), recorded)

    def retry_document(self, version_id, progress=None):
        with writer_lock(self.root):
            doc = self.get_document(version_id)
            job = self._job(doc["source_file"], doc["file_sha256"], version_id)
            try:
                return self._process(version_id, job, progress)
            except CopilotError as exc:
                return self._error(job, exc, version_id)

    def _process(self, version_id, job, progress):
        doc = self.get_document(version_id)
        stage = "storage"
        try:
            path = self.store.check(doc["file_sha256"])
            compatible = (doc["parser_version"] == PARSER_VERSION and doc["chunker_version"] == CHUNKER_VERSION
                          and doc["index_version"] == INDEX_VERSION)
            if doc["index_status"] == "ready" and compatible:
                self.db.update("jobs", "job_id", job, status="completed", stage="completed")
                return {"job_id": job, "status": "ready", "document_version_id": version_id,
                        "paper_id": doc["paper_id"], "data": doc, "reused_index": True, "error": None}
            if doc["parse_status"] not in ("parsed", "partial") or doc["parser_version"] != PARSER_VERSION:
                stage = "parsing"
                self.db.update("documents", "document_version_id", version_id, parse_status="parsing", index_status="not_started")
                self.db.update("jobs", "job_id", job, stage=stage)

                def on_page(current, total):
                    self.db.update("jobs", "job_id", job, processed_pages=current)
                    if progress:
                        progress("parsing", current, total)

                pages = parse_pdf(path, on_page)
                good = sum(p["status"] == "text_present" for p in pages)
                parse_status = "parsed" if good == len(pages) else "partial" if good else "failed"
                with self.db.connection() as con:
                    con.execute("DELETE FROM chunks WHERE document_version_id=?", (version_id,))
                    con.execute("DELETE FROM pages WHERE document_version_id=?", (version_id,))
                    con.executemany("INSERT INTO pages VALUES(?,?,?,?,?,?)", [(version_id, p["pdf_page"], p["raw_text"], p["status"], p["error_message"], p["parser_version"]) for p in pages])
                    con.execute("UPDATE documents SET parse_status=?,parser_version=?,index_status='not_started',updated_at=? WHERE document_version_id=?", (parse_status, PARSER_VERSION, now(), version_id))
                if not good:
                    raise CopilotError("no_reliable_text", "没有达到100字符门槛的可提取文字页；当前不支持OCR，原页文本与原因已保存。", "parsing", True)
            else:
                pages = self.get_pages(version_id)
            stage = "indexing"
            self.db.update("documents", "document_version_id", version_id, index_status="indexing")
            self.db.update("jobs", "job_id", job, stage=stage)
            if progress:
                progress(stage, 0, len(pages))
            chunks = make_chunks(doc, pages)
            indexed = [(c, term_counts(c["chunk_text"])) for c in chunks]
            if not any(counts for _, counts in indexed):
                raise CopilotError("no_searchable_tokens", "没有可用于当前英文／数字词法检索的词元，暂不支持中文语义检索。", "indexing", True)
            # Publication is atomic: persisted term counts and ready state commit together.
            with self.db.connection() as con:
                con.execute("DELETE FROM chunks WHERE document_version_id=?", (version_id,))
                con.executemany("""INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                    (c["chunk_id"], c["paper_id"], version_id, c["file_sha256"], c["pdf_page"], c["char_start"], c["char_end"], c["chunk_text"],
                     c["parser_version"], c["chunker_version"], INDEX_VERSION, json.dumps(counts, ensure_ascii=False), sum(counts.values()))
                    for c, counts in indexed])
                con.execute("""UPDATE documents SET index_status='ready',source_status='stored',chunker_version=?,index_version=?,
                    error_code=NULL,error_stage=NULL,error_message=NULL,updated_at=? WHERE document_version_id=?""", (CHUNKER_VERSION, INDEX_VERSION, now(), version_id))
                con.execute("UPDATE jobs SET status='completed',stage='completed',updated_at=? WHERE job_id=?", (now(), job))
            final = self.get_document(version_id)
            return {"job_id": job, "status": "partial" if final["parse_status"] == "partial" else "ready",
                    "document_version_id": version_id, "paper_id": final["paper_id"], "data": final,
                    "chunk_count": len(chunks), "error": None}
        except CopilotError:
            raise
        except Exception as exc:
            raise CopilotError(stage + "_failed", f"{stage}阶段未完成（{type(exc).__name__}），已保留原文件及已提交内容。", stage, True) from exc

    def search(self, paper_id: str, query: str, *, document_version_id=None, top_k=3):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise CopilotError("invalid_query", "请输入1—2000字符的检索词。", "retrieval")
        if not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise CopilotError("invalid_top_k", "Top-K需为1—20。", "retrieval")
        if not tokens(query):
            raise CopilotError("unsupported_query", "当前仅支持英文／数字词法检索，请使用论文中的英文术语；尚无中文语义映射。", "retrieval")
        if document_version_id:
            doc = self.db.one("SELECT * FROM documents WHERE paper_id=? AND document_version_id=?", (paper_id, document_version_id))
        else:
            doc = self.db.one("SELECT * FROM documents WHERE paper_id=? ORDER BY created_at DESC,document_version_id DESC LIMIT 1", (paper_id,))
        if not doc:
            raise CopilotError("scope_not_found", "指定论文／文件版本不存在或不属于该论文。", "retrieval")
        if doc["index_status"] != "ready" or (doc["parser_version"],doc["chunker_version"],doc["index_version"]) != (PARSER_VERSION,CHUNKER_VERSION,INDEX_VERSION):
            raise CopilotError("index_not_ready", "当前文件版本索引未就绪或已过期，请重试处理。", "retrieval", True)
        try:
            self.store.check(doc["file_sha256"])
        except CopilotError as exc:
            self.db.update("documents", "document_version_id", doc["document_version_id"], index_status="stale", source_status=exc.code,
                           error_code=exc.code, error_stage=exc.stage, error_message=exc.message)
            raise
        # Filtering occurs in SQL BEFORE computing corpus statistics and ranking.
        chunks = self.db.rows("SELECT * FROM chunks WHERE paper_id=? AND document_version_id=? AND file_sha256=?", (paper_id,doc["document_version_id"],doc["file_sha256"]))
        for c in chunks:
            c["term_counts"] = json.loads(c["term_counts_json"])
        return {"paper_id": paper_id, "document_version_id": doc["document_version_id"], "file_sha256": doc["file_sha256"],
                "source_file": doc["source_file"], "query": query, "method": INDEX_VERSION,
                "parse_status": doc["parse_status"], "hits": rank(chunks,query,top_k),
                "warning": "词法命中不是AI回答，也不代表数值或结论已获语义核实。"}

    def recover_interrupted(self):
        """Only recover if no live writer owns the OS lock. Never auto-reparse."""
        try:
            with writer_lock(self.root):
                for doc in self.list_documents():
                    version = doc["document_version_id"]
                    if doc["parse_status"] in ("queued", "parsing") or doc["index_status"] == "indexing":
                        fields = {"error_code":"interrupted", "error_stage":"processing", "error_message":"上次处理被中断，请重试；已提交内容保留。", "index_status":"stale"}
                        if doc["parse_status"] in ("queued", "parsing"):
                            fields["parse_status"] = "failed"
                        self.db.update("documents", "document_version_id", version, **fields)
                    if doc["index_status"] == "ready" and (doc["parser_version"],doc["chunker_version"],doc["index_version"]) != (PARSER_VERSION,CHUNKER_VERSION,INDEX_VERSION):
                        self.db.update("documents", "document_version_id", version, index_status="stale", error_code="pipeline_changed", error_stage="indexing", error_message="解析／切分／索引版本已变化，请重试。")
                    try:
                        self.store.check(doc["file_sha256"])
                    except CopilotError as exc:
                        self.db.update("documents", "document_version_id", version, index_status="stale", source_status=exc.code, error_code=exc.code, error_stage=exc.stage, error_message=exc.message)
                for job in self.db.rows("SELECT job_id FROM jobs WHERE status='processing'"):
                    self.db.update("jobs", "job_id", job["job_id"], status="failed", stage="processing", error_code="interrupted", error_message="上次进程退出或处理被中断，可重试或重新导入。")
        except CopilotError as exc:
            if exc.code != "busy":
                raise
