"""Controlled content-addressed storage. Filenames never determine destinations."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import os
import re
import uuid

from .errors import CopilotError

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 40


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def display_name(name: str) -> str:
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    return name[:180] or "unnamed.pdf"


def validate_bytes(data: bytes, name: str):
    if not data:
        raise CopilotError("empty_file", "文件为空，请重新选择。", "validation")
    if len(data) > MAX_BYTES:
        raise CopilotError("too_large", "超过首版25 MiB文件限制。", "validation")
    if Path(name).suffix.lower() != ".pdf" or b"%PDF-" not in data[:1024]:
        raise CopilotError("invalid_file", "文件扩展名或PDF内容标识未通过检查。", "validation")
    if b"%%EOF" not in data[-4096:]:
        raise CopilotError("damaged_pdf", "PDF缺少完整结束标记，可能已损坏或截断。", "validation")


class FileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)

    def path(self, sha256: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise CopilotError("invalid_hash", "文件哈希格式无效。", "storage")
        path = (self.objects / f"{sha256}.pdf").resolve()
        if not path.is_relative_to(self.root):
            raise CopilotError("unsafe_path", "文件路径超出受控目录。", "storage")
        return path

    def check(self, sha256: str):
        path = self.path(sha256)
        if not path.is_file():
            raise CopilotError("source_missing", "受控原文件丢失；请重新上传相同文件恢复。", "storage", True)
        if digest(path.read_bytes()) != sha256:
            raise CopilotError("source_corrupt", "受控原文件哈希不一致；请重新上传相同文件恢复。", "storage", True)
        return path

    def save(self, data: bytes, sha256: str) -> Path:
        if digest(data) != sha256:
            raise CopilotError("hash_mismatch", "文件与期望哈希不一致。", "storage")
        path = self.path(sha256)
        if path.exists() and digest(path.read_bytes()) == sha256:
            return path
        # Callers hold writer_lock; atomic replacement also repairs an exact-hash re-upload.
        temporary = self.objects / f"{uuid.uuid4().hex}.pending"
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path


@contextmanager
def writer_lock(root: Path):
    """One local writer/process at a time; OS releases the lock on process death."""
    handle = (root / "writer.lock").open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CopilotError("busy", "另一项导入或恢复正在处理，请稍后重试。", "processing", True) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()
