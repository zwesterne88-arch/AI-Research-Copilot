"""POC make_chunks rule: per-page raw offsets, 1600 chars, overlap 200."""
CHUNKER_VERSION = "page-1600-overlap200-v1"


def make_chunks(document: dict, pages: list[dict]) -> list[dict]:
    chunks = []
    for page in pages:
        if page["status"] != "text_present":
            continue
        text, start = page["raw_text"], 0
        while start < len(text):
            end = min(start + 1600, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + 1000, end)
                if boundary > start:
                    end = boundary + 1
            chunks.append({"chunk_id": f'{document["document_version_id"]}:p{page["pdf_page"]}:{start}-{end}:{CHUNKER_VERSION}',
                           "paper_id": document["paper_id"],
                           "document_version_id": document["document_version_id"],
                           "file_sha256": document["file_sha256"], "pdf_page": page["pdf_page"],
                           "char_start": start, "char_end": end, "chunk_text": text[start:end],
                           "parser_version": page["parser_version"], "chunker_version": CHUNKER_VERSION})
            if end == len(text):
                break
            start = max(start + 1, end - 200)
    return chunks
