"""Exact local provenance checks; location is independent of semantic support."""
import re
import unicodedata

from .errors import CopilotError

SUPPORT_LEVELS = {
    "supported": "明确支持",
    "partial": "部分支持",
    "background_only": "仅背景相关",
    "conflicting_or_unsupported": "引用冲突或不支持",
    "unverifiable": "无法确认",
}


def nfkc_offsets(text):
    """Map expanded ligatures back to raw character intervals, retaining line breaks."""
    chars, spans = [], []
    for i, char in enumerate(text):
        expanded = unicodedata.normalize("NFKC", char)
        chars.extend(expanded)
        spans.extend([(i, i + 1)] * len(expanded))
    return "".join(chars), spans


def locate_quote(text, quote):
    pos = text.find(quote)
    if pos >= 0:
        return pos, pos + len(quote)
    canonical, offsets = nfkc_offsets(text)
    chars, mapping = [], []
    for i, char in enumerate(canonical):
        if char.isspace():
            if chars and chars[-1] == " ":
                mapping[-1] = (mapping[-1][0], offsets[i][1])
                continue
            char = " "
        chars.append(char)
        mapping.append(offsets[i])
    needle = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", quote)).strip()
    pos = "".join(chars).find(needle)
    if not needle or pos < 0:
        return None
    return mapping[pos][0], mapping[pos + len(needle) - 1][1]


def validate_source_locations(service, doc, sources):
    pages = {p["pdf_page"]: p for p in service.get_pages(doc["document_version_id"])}
    chunks = {c["chunk_id"]: c for c in service.db.rows(
        "SELECT * FROM chunks WHERE paper_id=? AND document_version_id=? AND file_sha256=?",
        (doc["paper_id"], doc["document_version_id"], doc["file_sha256"]))}
    ids = set()
    for source in sources:
        c = chunks.get(source.get("chunk_id"))
        p = pages.get(source.get("pdf_page"))
        valid = c and p and isinstance(source.get("source_id"),str) and bool(re.fullmatch(r"E[1-9]\d*",source["source_id"])) and source["source_id"] not in ids
        valid = valid and all(source.get(k) == doc[k] for k in
                              ("paper_id", "document_version_id", "file_sha256", "parser_version", "chunker_version", "index_version"))
        a, b = source.get("char_start"), source.get("char_end")
        valid = valid and isinstance(a, int) and isinstance(b, int) and c["pdf_page"] == source["pdf_page"]
        valid = valid and c["char_start"] <= a < b <= c["char_end"] and p["raw_text"][a:b] == source["chunk_text"]
        if not valid:
            raise CopilotError("invalid_source_locator", "来源版本、页码或正文偏移校验失败，未发送回答请求。", "evidence_validation", True)
        ids.add(source["source_id"])


def support_payload(question, claims):
    """A reviewer sees ONLY each claim's own quotes, never a shared evidence pool."""
    return {"question": question, "claims": [
        {"claim_id": c["claim_id"], "text": c["text"], "kind": c["kind"],
         "evidence": [{"source_id": e["source_id"], "pdf_page": e["pdf_page"], "quote": e["quote"]}
                      for e in c["citations"]]} for c in claims]}
