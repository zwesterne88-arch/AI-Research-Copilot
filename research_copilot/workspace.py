"""Separate append-only workspace storage. Does not migrate the paper library."""
from contextlib import contextmanager
from decimal import Decimal
import json,sqlite3,uuid
from .repository import now
from .errors import CopilotError

class Workspace:
    def __init__(self,root):
        self.path=root/'workspace.sqlite3'
        with self.connection() as c:
            c.execute('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,document_version_id TEXT,cache_key TEXT,created_at TEXT NOT NULL,payload TEXT NOT NULL)')
            c.execute('CREATE INDEX IF NOT EXISTS by_cache ON records(kind,cache_key)')
            c.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,scope TEXT NOT NULL,max_calls INTEGER,cap TEXT,calls INTEGER,reserved TEXT,closed INTEGER,consent INTEGER,created_at TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS admissions(id TEXT PRIMARY KEY,session_id TEXT,stage TEXT,fingerprint TEXT,created_at TEXT,reservation TEXT,UNIQUE(session_id,fingerprint))')
    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        try:yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def add(self,kind,version,payload,key=None):
        item={'id':uuid.uuid4().hex,'kind':kind,'document_version_id':version,'created_at':now(),**payload}
        with self.connection() as c:c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(item['id'],kind,version,key,item['created_at'],json.dumps(item,ensure_ascii=False)))
        return item
    def records(self,kind,version=None):
        with self.connection() as c:
            rows=c.execute('SELECT payload FROM records WHERE kind=?'+(' AND document_version_id=?' if version else '')+' ORDER BY created_at DESC',(kind,version) if version else (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]
    def cached(self,key):
        with self.connection() as c:r=c.execute("SELECT payload FROM records WHERE kind='translation' AND cache_key=? ORDER BY created_at DESC LIMIT 1",(key,)).fetchone()
        return json.loads(r[0]) if r else None
    def session(self,sid):
        with self.connection() as c:r=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        if not r:raise CopilotError('session_missing','请先创建使用会话。','authorization')
        return dict(r)
    def create_session(self,versions,calls,cap,consent=False):
        if not isinstance(calls,int) or isinstance(calls,bool) or not 1<=calls<=100 or not Decimal('0')<Decimal(str(cap))<=20 or not versions:
            raise CopilotError('invalid_session','请求数需为1—100，预算需为0—20元内的正数。','budget')
        sid=uuid.uuid4().hex
        with self.connection() as c:c.execute('INSERT INTO sessions VALUES(?,?,?,?,0,?,0,?,?)',(sid,json.dumps(versions),calls,str(Decimal(str(cap))),'0',int(consent),now()))
        return self.session(sid)

class UsageBudget:
    """New user session only. Legacy ledgers are not consulted or modified."""
    def __init__(self,store,sid,version,stage,fingerprint,enabled=False):
        self.store,self.sid,self.version,self.stage,self.fingerprint,self.enabled=store,sid,version,stage,fingerprint,enabled
    def reserve(self,input_bytes,output_limit):
        if not self.enabled:raise CopilotError('paid_disabled','本次启动未启用收费调用；可使用Mock或查看历史。','authorization')
        if not 1<=input_bytes<=60000 or not 200<=output_limit<=2000:raise CopilotError('budget_input_large','请求超过保守Token预算，未发送。','budget')
        upper=input_bytes+4096;amount=2*(Decimal(upper)*2+Decimal(output_limit)*8)/1_000_000
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM sessions WHERE id=?',(self.sid,)).fetchone()
            if not r or r['closed'] or not r['consent'] or self.version not in json.loads(r['scope']):raise CopilotError('session_scope','当前论文未在会话中单独授权。','authorization')
            if r['calls']>=r['max_calls'] or Decimal(r['reserved'])+amount>Decimal(r['cap']):raise CopilotError('session_exhausted','次数或保守费用预算不足，未发送。','budget')
            if c.execute('SELECT 1 FROM admissions WHERE session_id=? AND fingerprint=?',(self.sid,self.fingerprint)).fetchone():raise CopilotError('duplicate_request','相同请求已尝试，请查看记录；不自动重试。','budget')
            total=Decimal(r['reserved'])+amount
            c.execute('INSERT INTO admissions VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,self.sid,self.stage,self.fingerprint,now(),str(amount)))
            c.execute('UPDATE sessions SET calls=calls+1,reserved=? WHERE id=?',(str(total),self.sid))
        return {'session_id':self.sid,'call_number':r['calls']+1,'reservation_cny':str(amount),'total_reserved_cny':str(total),'remaining_calls':r['max_calls']-r['calls']-1,'remaining_cny':str(Decimal(r['cap'])-total)}
    def estimate(self,usage):
        if not all(isinstance(usage.get(k),int) for k in ('prompt_tokens','completion_tokens')):return None
        return str((Decimal(usage['prompt_tokens'])*2+Decimal(usage['completion_tokens'])*8)/1_000_000)
