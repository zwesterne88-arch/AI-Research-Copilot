"""Same core service for scripts and tests: python -m research_copilot.cli ..."""
import argparse
import json
from pathlib import Path
import sys

from .errors import CopilotError
from .service import CopilotService
from .files import MAX_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("pdf", type=Path)
    imp.add_argument("--paper-id", help="Explicitly attach a different file as a version of an existing paper")
    sub.add_parser("list")
    retry = sub.add_parser("retry")
    retry.add_argument("version_id")
    search = sub.add_parser("search")
    search.add_argument("paper_id")
    search.add_argument("query")
    search.add_argument("--version-id")
    search.add_argument("--top-k", type=int, default=3)
    ask = sub.add_parser("ask")
    ask.add_argument("paper_id")
    ask.add_argument("question")
    ask.add_argument("--version-id", required=True)
    ask.add_argument("--allow-remote-paper-text", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        svc = CopilotService(args.data_dir)
        if args.command == "import":
            with args.pdf.open("rb") as handle:
                result = svc.import_pdf(handle.read(MAX_BYTES + 1), args.pdf.name, paper_id=args.paper_id)
        elif args.command == "list":
            result = svc.list_documents()
        elif args.command == "retry":
            result = svc.retry_document(args.version_id)
        elif args.command == "search":
            result = svc.search(args.paper_id,args.query,document_version_id=args.version_id,top_k=args.top_k)
        else:
            result = svc.answer_question(args.paper_id,args.question,document_version_id=args.version_id,allow_remote=args.allow_remote_paper_text)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if isinstance(result,dict) and result.get("status") in ("failed","rejected") else 0
    except (CopilotError, OSError) as exc:
        print(json.dumps({"error":exc.as_dict() if isinstance(exc,CopilotError) else {"code":"io_error","message":str(exc)}},ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
