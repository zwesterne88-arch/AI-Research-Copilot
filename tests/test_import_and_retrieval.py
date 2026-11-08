import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research_copilot import CopilotService
from research_copilot.errors import CopilotError
from research_copilot.files import MAX_BYTES, writer_lock
from research_copilot import service as service_module
from research_copilot.pdf_parser import PARSER_VERSION

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("data,name,code", [
    (b"", "empty.pdf", "empty_file"),
    (b"ordinary text", "fake.pdf", "invalid_file"),
    (b"%PDF-1.7\nbroken\n%%EOF", "broken.pdf", "damaged_pdf"),
    (b"%PDF-1.7\ntruncated", "truncated.pdf", "damaged_pdf"),
    (b"x" * (MAX_BYTES + 1), "huge.pdf", "too_large"),
], ids=["empty", "fake_pdf", "broken_pdf", "truncated_pdf", "over_size_limit"])
def test_rejections_logged_without_documents(service, data, name, code):
    result = service.import_pdf(data, name)
    assert result["status"] == "rejected" and result["error"]["code"] == code
    assert service.list_documents() == []
    assert service.jobs()[0]["error_code"] == code
    assert list(service.store.objects.glob("*.pdf")) == []


def test_extension_not_only_check_and_page_limit(service, pdf_bytes):
    assert service.import_pdf(pdf_bytes(), "not_a_pdf.txt")["error"]["code"] == "invalid_file"
    too_many = service.import_pdf(pdf_bytes([""] * 41), "41pages.pdf")
    assert too_many["error"]["code"] == "too_many_pages"
    assert service.list_documents() == []


def test_encrypted_pdf_rejected(service, pdf_bytes):
    result = service.import_pdf(pdf_bytes(encrypted=True), "encrypted.pdf")
    assert result["error"]["code"] == "encrypted_pdf"


def test_exactly_40_pages_is_accepted_with_partial_scope(service, pdf_bytes):
    result = service.import_pdf(pdf_bytes(["reliable page text " * 30] + [""] * 39), "40pages.pdf")
    assert result["status"] == "partial" and result["data"]["page_count"] == 40
    assert {h["pdf_page"] for h in service.search(result["paper_id"], "reliable")["hits"]} == {1}


def test_parse_failure_keeps_file_and_retry_recovers(service, pdf_bytes, monkeypatch):
    real = service_module.parse_pdf
    def broken(*args):
        raise CopilotError("parse_failed", "Injected test parser failure", "parsing", True)
    monkeypatch.setattr(service_module, "parse_pdf", broken)
    result = service.import_pdf(pdf_bytes(), "failed_parse.pdf")
    assert result["status"] == "failed" and result["error"]["stage"] == "parsing"
    assert len(list(service.store.objects.glob("*.pdf"))) == 1
    monkeypatch.setattr(service_module, "parse_pdf", real)
    assert service.retry_document(result["document_version_id"])["status"] == "ready"
    assert len(service.list_documents()) == 1


def test_unknown_paper_cannot_create_version(service, pdf_bytes):
    result = service.import_pdf(pdf_bytes(), "unlinked.pdf", paper_id="paper_not_real")
    assert result["status"] == "rejected" and result["error"]["code"] == "paper_not_found"
    assert service.list_documents() == []


def test_duplicate_reuses_version_index_and_file(service, pdf_bytes, monkeypatch):
    data = pdf_bytes()
    first = service.import_pdf(data, "original.pdf")
    assert first["status"] == "ready"
    chunks = service.search(first["paper_id"], "intrusion")["hits"]
    monkeypatch.setattr(service_module, "parse_pdf", lambda *args: pytest.fail("Duplicate must not parse again"))
    duplicate = service.import_pdf(data, "renamed.pdf")
    assert duplicate["status"] == "duplicate"
    assert duplicate["document_version_id"] == first["document_version_id"]
    assert len(service.list_documents()) == len(list(service.store.objects.glob("*.pdf"))) == 1
    assert service.search(first["paper_id"], "intrusion")["hits"] == chunks


def test_raw_offsets_and_per_page_provenance(service, pdf_bytes):
    data = pdf_bytes(["Pageone alpha protocol \n" * 150, "Pagetwo beta network " * 100])
    result = service.import_pdf(data, "two.pdf")
    assert result["data"]["page_count"] == 2
    pages = {p["pdf_page"]:p for p in service.get_pages(result["document_version_id"])}
    chunks = service.db.rows("SELECT * FROM chunks")
    assert len(chunks) > 2 and {c["pdf_page"] for c in chunks} == {1, 2}
    for c in chunks:
        assert pages[c["pdf_page"]]["raw_text"][c["char_start"]:c["char_end"]] == c["chunk_text"]
        assert c["file_sha256"] == hashlib.sha256(data).hexdigest()
        assert c["paper_id"] == result["paper_id"]
        assert c["document_version_id"] == result["document_version_id"]
        assert c["parser_version"] and c["chunker_version"] and c["index_version"]


def test_scoped_retrieval_and_explicit_versions(service, pdf_bytes):
    a = service.import_pdf(pdf_bytes(["common alphaonly " * 40],title="Same title"),"same.pdf")
    b = service.import_pdf(pdf_bytes(["common betaonly " * 40],title="Same title"),"same.pdf")
    assert a["paper_id"] != b["paper_id"]  # title/name never auto-merge
    a2 = service.import_pdf(pdf_bytes(["common revisedonly " * 40]),"revision.pdf",paper_id=a["paper_id"])
    assert a2["paper_id"] == a["paper_id"] and a2["document_version_id"] != a["document_version_id"]
    assert service.search(a["paper_id"],"betaonly")["hits"] == []
    assert service.search(a["paper_id"],"alphaonly")["hits"] == []  # latest version only
    old = service.search(a["paper_id"],"alphaonly",document_version_id=a["document_version_id"])
    assert old["hits"] and all(h["document_version_id"] == a["document_version_id"] for h in old["hits"])
    with pytest.raises(CopilotError, match="不属于"):
        service.search(a["paper_id"],"common",document_version_id=b["document_version_id"])
    assert service.import_pdf(pdf_bytes(["common betaonly " * 40],title="Same title"),"same.pdf",paper_id=a["paper_id"])["error"]["code"] == "duplicate_other_paper"


def test_partial_pages_and_full_no_text_failure(service, pdf_bytes):
    partial = service.import_pdf(pdf_bytes(["usable reliable words " * 50, ""]),"partial.pdf")
    assert partial["status"] == "partial" and partial["data"]["index_status"] == "ready"
    assert {c["pdf_page"] for c in service.search(partial["paper_id"],"usable")["hits"]} == {1}
    assert service.get_pages(partial["document_version_id"])[1]["status"] == "low_text"
    failed = service.import_pdf(pdf_bytes([""]),"blank.pdf")
    assert failed["status"] == "failed" and failed["error"]["code"] == "no_reliable_text"
    assert service.get_document(failed["document_version_id"])["index_status"] != "ready"


def test_page_exception_saved_and_other_pages_indexed(service, pdf_bytes, monkeypatch):
    from pypdf._page import PageObject
    real = PageObject.extract_text
    calls = 0
    def extract(page, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("injected test page failure")
        return real(page,*args,**kwargs)
    monkeypatch.setattr(PageObject,"extract_text",extract)
    result = service.import_pdf(pdf_bytes(["first usable page " * 50, "second page " * 50]),"pages.pdf")
    assert result["status"] == "partial"
    assert service.get_pages(result["document_version_id"])[1]["status"] == "failed"
    assert {h["pdf_page"] for h in service.search(result["paper_id"],"first")["hits"]} == {1}


def test_failed_index_retry_reuses_saved_pages(service, pdf_bytes, monkeypatch):
    real = service_module.make_chunks
    def broken(*args):
        raise RuntimeError("injected indexing failure")
    monkeypatch.setattr(service_module,"make_chunks",broken)
    result = service.import_pdf(pdf_bytes(),"recover.pdf")
    assert result["error"]["stage"] == "indexing"
    assert service.get_document(result["document_version_id"])["parse_status"] == "parsed"
    assert service.get_document(result["document_version_id"])["index_status"] == "failed"
    monkeypatch.setattr(service_module,"make_chunks",real)
    monkeypatch.setattr(service_module,"parse_pdf",lambda *args:pytest.fail("Retry should reuse pages"))
    retry = service.retry_document(result["document_version_id"])
    assert retry["status"] == "ready"
    assert len(service.list_documents()) == 1


def test_index_publication_rolls_back(service, pdf_bytes):
    result = service.import_pdf(pdf_bytes(),"atomic.pdf")
    vid = result["document_version_id"]
    count = service.db.one("SELECT count(*) n FROM chunks")["n"]
    service.db.update("documents","document_version_id",vid,index_status="stale")
    with service.db.connection() as con:
        con.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON chunks BEGIN SELECT RAISE(ABORT,'test failure'); END")
    failed = service.retry_document(vid)
    assert failed["status"] == "failed"
    assert service.db.one("SELECT count(*) n FROM chunks")["n"] == count
    assert service.get_document(vid)["index_status"] == "failed"
    with service.db.connection() as con:
        con.execute("DROP TRIGGER injected_failure")
    assert service.retry_document(vid)["status"] == "ready"


def test_interrupted_job_recovery_and_retry(service, pdf_bytes):
    result = service.import_pdf(pdf_bytes(),"interrupted.pdf")
    service.db.update("documents","document_version_id",result["document_version_id"],index_status="indexing")
    job = service._job("interrupted.pdf",version_id=result["document_version_id"])
    reopened = CopilotService(service.root)
    assert reopened.get_document(result["document_version_id"])["index_status"] == "stale"
    assert reopened.db.one("SELECT * FROM jobs WHERE job_id=?",(job,))["error_code"] == "interrupted"
    assert reopened.retry_document(result["document_version_id"])["status"] == "ready"


def test_source_loss_reupload_repairs_without_new_version(service, pdf_bytes):
    data = pdf_bytes()
    result = service.import_pdf(data,"original.pdf")
    service.store.path(result["data"]["file_sha256"]).unlink()  # disposable test store only
    with pytest.raises(CopilotError) as exc:
        service.search(result["paper_id"],"intrusion")
    assert exc.value.code == "source_missing"
    assert service.get_document(result["document_version_id"])["index_status"] == "stale"
    assert service.import_pdf(data,"restored.pdf")["status"] == "duplicate"
    assert service.retry_document(result["document_version_id"])["status"] == "ready"
    assert len(service.list_documents()) == 1


def test_writer_lock_blocks_other_writer(service):
    with writer_lock(service.root):
        with pytest.raises(CopilotError) as exc:
            with writer_lock(service.root):
                pytest.fail("Must not enter")
    assert exc.value.code == "busy"


def test_filename_cannot_escape_storage(service,pdf_bytes):
    result = service.import_pdf(pdf_bytes(),"../../outside.pdf")
    doc = result["data"]
    assert doc["source_file"] == "outside.pdf"
    assert (service.root/doc["stored_path"]).resolve().is_relative_to(service.root)
    assert not (service.root.parent/"outside.pdf").exists()


@pytest.mark.parametrize("query,top_k,code",[("",3,"invalid_query"),("中文问题",3,"unsupported_query"),("intrusion",0,"invalid_top_k")])
def test_invalid_queries_explicit(service,pdf_bytes,query,top_k,code):
    result = service.import_pdf(pdf_bytes(),"query.pdf")
    with pytest.raises(CopilotError) as exc:
        service.search(result["paper_id"],query,top_k=top_k)
    assert exc.value.code == code


def test_new_process_persistence(service,pdf_bytes):
    result = service.import_pdf(pdf_bytes(),"restart.pdf")
    run = subprocess.run([sys.executable,"-X","utf8","-m","research_copilot.cli","--data-dir",str(service.root),"search",result["paper_id"],"intrusion"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert run.returncode == 0, run.stderr
    restored = json.loads(run.stdout)
    assert restored["hits"] and restored["document_version_id"] == result["document_version_id"]


def test_real_non_poc_pdf_dynamic_import(service):
    # P03 was never in POC's default three or its extra P14 CLI sample.
    path = ROOT/"input_papers/1-s2.0-S016740482400381X-main.pdf"
    if not path.exists():
        pytest.skip("Private real-paper integration fixture not present on this machine")
    result = service.import_pdf(path.read_bytes(),path.name)
    assert result["status"] == "ready" and result["data"]["page_count"] == 14
    hits = service.search(result["paper_id"],"dynamic graph intrusion detection")["hits"]
    assert hits and all(h["paper_id"] == result["paper_id"] and 1<=h["pdf_page"]<=14 for h in hits)
    assert service.db.rows("PRAGMA foreign_key_check") == []
