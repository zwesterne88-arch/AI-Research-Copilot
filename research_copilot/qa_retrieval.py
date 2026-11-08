"""Bounded, source-scoped evidence retrieval; no discipline or answer-specific terms.

Closing-section headings are navigation hints, NOT evidence of a limitation.
The persisted stage4.1 index and its public lexical search contract stay unchanged.
"""
import re
import unicodedata
from .evidence import nfkc_offsets

RETRIEVAL_VERSION = "scoped-rrf20-closing-context-dedup-v3"
MAX_CONTEXT_CHARS = 12000
MAX_PASSAGE_CHARS = 440
PROVENANCE_KEYS = ("chunk_id", "paper_id", "document_version_id", "file_sha256", "pdf_page",
                   "parser_version", "chunker_version", "index_version")
HEADING = re.compile(r"(?m)^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
                     r"((?:limitations?|future\s+(?:work|directions)|discussion|conclusions?|"
                     r"benefits?\s+and\s+risks?)[^\n]{0,100})\s*$", re.I)
NEXT_HEADING = re.compile(r"(?m)^\s*(?:\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{1,100}|"
                          r"References|Acknowledg(?:e)?ments|Declaration[^\n]*|CRediT[^\n]*|Data availability[^\n]*)\s*$")


def _key(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def closing_windows(pages):
    windows = []
    for page in pages:
        if page["status"] != "text_present":
            continue
        text = page["raw_text"]
        canonical, offsets = nfkc_offsets(text)
        for match in HEADING.finditer(canonical):
            start = offsets[match.start()][0]
            following = NEXT_HEADING.search(canonical, match.end())
            end = min(offsets[following.start()][0] if following else len(text), start + 1800)
            title = match.group(1).strip()
            priority = 0 if re.search(r"limit|risk|future", title, re.I) else 1 if title.lower().startswith("discussion") else 2
            windows.append((priority, page["pdf_page"], start, end, title))
    # At most two navigation windows; bounded even for papers with many subheadings.
    return sorted(windows)[:2]


def _uncovered(start, end, intervals):
    for left, right in sorted(intervals):
        if right <= start:
            continue
        if left >= end:
            break
        if left > start:
            yield start, left
        start = max(start, right)
    if start < end:
        yield start, end


def retrieve_context(service, doc, queries, max_sources=40, *, question="", diagnostics=None):
    # Preserve the original question. English terms in it can participate directly;
    # Chinese meaning is supplied by the (cached) model plan, never a fixed QA table.
    expressions = list(dict.fromkeys([q.strip() for q in queries]))
    if re.search(r"[A-Za-z]{2,}", question) and question.strip() not in expressions:
        expressions.insert(0, question.strip())
    scored, candidates = {}, {}
    per_query = []
    for query in expressions:
        found = service.search(doc["paper_id"], query, document_version_id=doc["document_version_id"], top_k=20)
        per_query.append({"query": query, "chunk_ids": [c["chunk_id"] for c in found["hits"]]})
        for pos, chunk in enumerate(found["hits"], 1):
            candidates[chunk["chunk_id"]] = chunk
            scored[chunk["chunk_id"]] = scored.get(chunk["chunk_id"], 0) + 1 / (60 + pos)
    # Same immutable scope for navigation/neighbor context, never a library-wide query.
    chunks = service.db.rows("""SELECT * FROM chunks WHERE paper_id=? AND document_version_id=? AND file_sha256=?
        ORDER BY pdf_page,char_start""", (doc["paper_id"], doc["document_version_id"], doc["file_sha256"]))
    pages = service.get_pages(doc["document_version_id"])
    windows = closing_windows(pages)
    selections = []
    for _, page, start, end, title in windows:
        for chunk in chunks:
            if chunk["pdf_page"] == page and chunk["char_end"] > start and chunk["char_start"] < end:
                selections.append((chunk, max(start, chunk["char_start"]), min(end, chunk["char_end"]), "section_context", title))
    ordered = sorted(candidates.values(), key=lambda c: (-scored[c["chunk_id"]], c["pdf_page"], c["char_start"]))
    # Preserve each expression's strongest evidence: RRF alone rewards generic chunks
    # appearing in several lists over a highly relevant single-expression result.
    seeds = []
    for pos in range(3):
        for result in per_query:
            if pos < len(result["chunk_ids"]):
                seeds.append(candidates[result["chunk_ids"][pos]])
    used = set()
    page_counts = {}
    for chunk in seeds + ordered:
        if chunk["chunk_id"] not in used and page_counts.get(chunk["pdf_page"], 0) < 3:
            selections.append((chunk, chunk["char_start"], chunk["char_end"], "query_seed" if chunk in seeds else "lexical", None))
            used.add(chunk["chunk_id"])
            page_counts[chunk["pdf_page"]] = page_counts.get(chunk["pdf_page"], 0) + 1
    if chunks:
        selections.append((chunks[0], chunks[0]["char_start"], chunks[0]["char_end"], "opening_context", None))
    passages, covered, fingerprints = [], {}, set()
    total = 0
    for chunk, start, end, reason, section in selections:
        page = chunk["pdf_page"]
        for left, right in list(_uncovered(start, end, covered.get(page, []))):
            pos = left
            while pos < right and len(passages) < max_sources and total < MAX_CONTEXT_CHARS:
                stop = min(pos + MAX_PASSAGE_CHARS, right, pos + MAX_CONTEXT_CHARS - total)
                text = chunk["chunk_text"]
                lo, hi = pos - chunk["char_start"], stop - chunk["char_start"]
                # Prefer complete sentences, retaining raw offsets. Hard cuts are explicit metadata.
                boundary = list(re.finditer(r"[.!?](?:\s|$)", text[lo:hi]))
                complete = bool(boundary and boundary[-1].end() >= min(180, hi - lo))
                if stop < right and complete:
                    stop = pos + boundary[-1].end()
                raw = text[lo:stop - chunk["char_start"]]
                leading = len(raw) - len(raw.lstrip())
                trailing = len(raw.rstrip())
                exact = raw[leading:trailing]
                fingerprint = _key(exact)
                if len(exact) >= 20 and fingerprint not in fingerprints:
                    passages.append({"source_id": f"E{len(passages)+1}", **{k: chunk[k] for k in PROVENANCE_KEYS},
                                     "char_start": pos + leading, "char_end": pos + trailing, "chunk_text": exact,
                                     "retrieval_reason": reason, "section_hint": section,
                                     "boundary_may_cut_sentence": stop < right and not complete})
                    total += len(exact)
                    fingerprints.add(fingerprint)
                covered.setdefault(page, []).append((pos, stop))
                pos = stop
    if diagnostics is not None:
        diagnostics.update(original_question=question, search_expressions=expressions, per_query=per_query,
                           candidate_count=len(candidates), candidate_pages=sorted({c["pdf_page"] for c in candidates.values()}),
                           navigation_windows=[{"pdf_page": p, "char_start": a, "char_end": b, "heading": h} for _, p, a, b, h in windows],
                           context_characters=total, context_limit=MAX_CONTEXT_CHARS,
                           exhaustive_full_text_review=False)
    return passages
