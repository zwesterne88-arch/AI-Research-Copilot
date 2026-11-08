"""Synthetic PDFs are marked test fixtures, never research results."""
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from research_copilot import CopilotService


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """Deny external sockets; Windows asyncio uses loopback for its local self-pipe."""
    import socket
    original = socket.socket.connect
    def denied(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original(sock, address)
        pytest.fail("Offline suite attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def service(tmp_path):
    return CopilotService(tmp_path / "library")


@pytest.fixture
def pdf_bytes():
    def create(texts=None, title="Synthetic test fixture", encrypted=False):
        writer = PdfWriter()
        for text in texts or ["alpha vehicle intrusion dataset accuracy " * 30]:
            page = writer.add_blank_page(width=600, height=800)
            if text:
                font = DictionaryObject({NameObject("/Type"):NameObject("/Font"), NameObject("/Subtype"):NameObject("/Type1"), NameObject("/BaseFont"):NameObject("/Helvetica")})
                page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
                stream = DecodedStreamObject()
                safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                stream.set_data(f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode("ascii"))
                page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_metadata({"/Title":title})
        if encrypted:
            writer.encrypt("test-password")
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()
    return create
