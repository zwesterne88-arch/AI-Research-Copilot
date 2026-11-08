"""Strict local data access. SQL and file paths are controlled by the evaluator."""
from contextlib import contextmanager
import hashlib,json,re,socket,sqlite3
from pathlib import Path
from unittest.mock import patch

class EvaluationError(ValueError): pass

def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def confined(root,relative):
    root=Path(root).resolve(); p=(root/relative).resolve()
    if not p.is_relative_to(root) or p==root: raise EvaluationError('Path escapes the declared root')
    return p

def read_json(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as e:raise EvaluationError('Missing or invalid JSON: '+str(path)) from e

def read_jsonl(path):
    try:return [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    except (OSError,ValueError) as e:raise EvaluationError('Missing or invalid JSONL: '+str(path)) from e

def write_json(path,obj):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n')

def write_jsonl(path,rows):
    with Path(path).open('x',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')

class ReadOnlyDB:
    def __init__(self,path):self.path=Path(path)
    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        try:yield c
        finally:c.close()
    def rows(self,sql,args=()):
        with self.connection() as c:return [dict(r) for r in c.execute(sql,args)]
    def one(self,sql,args=()):
        data=self.rows(sql,args);return data[0] if data else None
    def update(self,*a,**kw):raise EvaluationError('Evaluation never modifies a document or database')

class ReadOnlyObjects:
    def __init__(self,root):self.root=Path(root);self.checked=set()
    def check(self,sha):
        from research_copilot.errors import CopilotError
        if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha): raise EvaluationError('Invalid file hash')
        p=confined(self.root,'objects/'+sha+'.pdf')
        if not p.is_file() or (sha not in self.checked and file_hash(p)!=sha):
            raise CopilotError('source_unavailable','Evaluation source PDF is absent or changed','evaluation')
        self.checked.add(sha);return p

class ReadOnlyService:
    """Reuse production search/inspection without its initializing/recovery writes."""
    def __init__(self,root):
        self.root=Path(root);self.db=ReadOnlyDB(self.root/'library.sqlite3');self.store=ReadOnlyObjects(self.root)
    from research_copilot.service import CopilotService as _Service
    search=_Service.search
    get_document=_Service.get_document
    get_pages=_Service.get_pages
    list_documents=_Service.list_documents

@contextmanager
def offline_only():
    def deny(*args,**kwargs):raise EvaluationError('Network is disabled for offline evaluation')
    with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny):yield
