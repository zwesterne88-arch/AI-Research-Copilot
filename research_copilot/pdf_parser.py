"""Promoted from poc/run_feasibility.py:parse_file; no table semantics claimed."""
from io import BytesIO
import importlib.metadata

from pypdf import PdfReader

from .errors import CopilotError
from .files import MAX_PAGES

PARSER_VERSION = "pypdf-" + importlib.metadata.version("pypdf") + ":text-v1"
MIN_PAGE_CHARS = 100  # Same low-text diagnostic as the stage-2 POC.


def inspect_pdf(data: bytes):
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise CopilotError("encrypted_pdf", "当前版本不处理加密PDF，请提供合法可访问的未加密文本版。", "validation")
        count = len(reader.pages)
        if not count:
            raise CopilotError("no_pages", "PDF没有可读取页面。", "validation")
        if count > MAX_PAGES:
            raise CopilotError("too_many_pages", "超过首版40页限制，不自动截断。", "validation")
        title = str((reader.metadata or {}).get("/Title", "")).strip()[:400]
        return count, title
    except CopilotError:
        raise
    except Exception as exc:
        raise CopilotError("damaged_pdf", f"PDF结构无法读取（{type(exc).__name__}）。", "validation") from exc


def parse_pdf(path, progress=None):
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise CopilotError("encrypted_pdf", "文件已加密，不能解析。", "parsing")
        pages = []
        for number in range(1, len(reader.pages) + 1):
            try:
                text = reader.pages[number - 1].extract_text() or ""
                status = "text_present" if len(text.strip()) >= MIN_PAGE_CHARS else "low_text"
                error = None if status == "text_present" else "文字少于100字符；未纳入检索，当前不支持OCR。"
            except Exception as exc:
                text, status, error = "", "failed", f"页面提取失败（{type(exc).__name__}）。"
            pages.append({"pdf_page": number, "raw_text": text, "status": status,
                          "error_message": error, "parser_version": PARSER_VERSION})
            if progress:
                progress(number, len(reader.pages))
        return pages
    except CopilotError:
        raise
    except Exception as exc:
        raise CopilotError("parse_failed", f"正文读取失败（{type(exc).__name__}）。", "parsing", True) from exc
