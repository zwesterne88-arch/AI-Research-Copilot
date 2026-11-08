"""POC retrieve/tokens score, now using persisted per-chunk term counts."""
from collections import Counter
import math
import re
import unicodedata

INDEX_VERSION = "lexical-bm25like-k1_1.5-b_0.75-v1"


def tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", normalized)


def term_counts(text: str) -> dict:
    return dict(Counter(tokens(text)))


def rank(chunks: list[dict], query: str, top_k: int) -> list[dict]:
    if not chunks:
        return []
    average = sum(c["token_count"] for c in chunks) / len(chunks) or 1
    words = set(tokens(query))
    df = {w: sum(w in c["term_counts"] for c in chunks) for w in words}
    hits = []
    for chunk in chunks:
        score = 0.0
        for word in words:
            tf = chunk["term_counts"].get(word, 0)
            if tf:
                idf = math.log(1 + (len(chunks) - df[word] + .5) / (df[word] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * chunk["token_count"] / average))
        if score > 0:
            hit = {k: v for k, v in chunk.items() if k not in ("term_counts", "term_counts_json")}
            hits.append({**hit, "score": round(score, 6)})
    return sorted(hits, key=lambda x: (-x["score"], x["pdf_page"], x["char_start"]))[:top_k]
