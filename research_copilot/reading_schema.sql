-- Additive optional feature. Core schema_meta=1 and all stage4.1/4.2 tables remain unchanged.
CREATE TABLE IF NOT EXISTS reading_cards (
    card_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    card_version INTEGER NOT NULL CHECK(card_version > 0),
    schema_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_id TEXT NOT NULL,
    card_status TEXT NOT NULL CHECK(card_status IN ('processing','draft','failed')),
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(document_version_id,card_version),
    FOREIGN KEY(document_version_id,paper_id) REFERENCES documents(document_version_id,paper_id),
    FOREIGN KEY(document_version_id,file_sha256) REFERENCES documents(document_version_id,file_sha256)
);
CREATE INDEX IF NOT EXISTS reading_scope ON reading_cards(paper_id,document_version_id,card_version);
