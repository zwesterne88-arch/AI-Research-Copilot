CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
INSERT INTO schema_meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    document_version_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(paper_id),
    file_sha256 TEXT NOT NULL UNIQUE,
    source_file TEXT NOT NULL, stored_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL,
    metadata_title TEXT, source_status TEXT NOT NULL DEFAULT 'stored',
    parse_status TEXT NOT NULL DEFAULT 'queued',
    index_status TEXT NOT NULL DEFAULT 'not_started',
    parser_version TEXT, chunker_version TEXT, index_version TEXT,
    error_code TEXT, error_stage TEXT, error_message TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(document_version_id, paper_id),
    UNIQUE(document_version_id, file_sha256)
);
CREATE TABLE IF NOT EXISTS pages (
    document_version_id TEXT NOT NULL REFERENCES documents(document_version_id),
    pdf_page INTEGER NOT NULL CHECK(pdf_page > 0), raw_text TEXT NOT NULL,
    status TEXT NOT NULL, error_message TEXT, parser_version TEXT NOT NULL,
    PRIMARY KEY(document_version_id, pdf_page)
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL, file_sha256 TEXT NOT NULL,
    pdf_page INTEGER NOT NULL, char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL CHECK(char_end > char_start), chunk_text TEXT NOT NULL,
    parser_version TEXT NOT NULL, chunker_version TEXT NOT NULL,
    index_version TEXT NOT NULL, term_counts_json TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    FOREIGN KEY(document_version_id,paper_id) REFERENCES documents(document_version_id,paper_id),
    FOREIGN KEY(document_version_id,file_sha256) REFERENCES documents(document_version_id,file_sha256),
    FOREIGN KEY(document_version_id,pdf_page) REFERENCES pages(document_version_id,pdf_page)
);
CREATE INDEX IF NOT EXISTS chunks_scope ON chunks(paper_id, document_version_id);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, document_version_id TEXT REFERENCES documents(document_version_id),
    source_file TEXT NOT NULL, file_sha256 TEXT,
    status TEXT NOT NULL, stage TEXT NOT NULL, processed_pages INTEGER NOT NULL DEFAULT 0,
    error_code TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_document ON jobs(document_version_id, created_at);
