"""Explicit 4.5.2 grant: independent durable admissions and exact real-response reuse.

Uses the existing LLM adapter. No HTTP implementation, key, or provider behavior
belongs here. Old ledgers and responses are opened read-only, never reset.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from .errors import CopilotError
from .repository import now

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT/'config/stage4_5_2_validation_authorization.json'
LEDGER_PATH = ROOT/'verification/stage4_5_2/private/live_budget.sqlite3'
ARTIFACT_ROOT = ROOT/'verification/stage4_5_2/private/real_api'
OLD_ROOT = ROOT/'verification/stage4_5/private/real_api'
SESSION = 'stage4_5_2_p05_retest_11calls_4cny'
REUSED_STAGES = {'reading_planning','reading_extract:metadata','reading_review:metadata'}
READING_STAGES = {f'reading_{step}:{m}' for step in ('extract','review') for m in ('framing','approach','validation','findings')}
QA_STAGES = {'answering','support_verification','scope_verification'}

def policy():
    if not POLICY_PATH.is_file(): return None
    p=json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    if p.get('status')!='approved': return None
    required={'session_id':SESSION,'max_calls':11,'max_reserved_cny':'4','peak_input_cny_per_million':'2','peak_output_cny_per_million':'8'}
    if any(p.get(k)!=v for k,v in required.items()):
        raise CopilotError('validation_policy_mismatch','4.5.2授权配置不一致，未发送。','budget')
    return p

def applies(doc):
    p=policy()
    return bool(p and doc['file_sha256']==p['file_sha256'])

def summary():
    if not LEDGER_PATH.is_file(): return {'session_id':SESSION,'attempted_calls':0,'reserved_cny':'0','closed':0,'qa_enabled':0}
    with sqlite3.connect(LEDGER_PATH.resolve().as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        return dict(con.execute('SELECT * FROM session_budget WHERE session_id=?',(SESSION,)).fetchone())

def ui_state(doc):
    if not applies(doc): return None
    row=summary()
    return {**row,'max_resume_depth':2,'exhausted':bool(row['closed'] or row['attempted_calls']>=11 or Decimal(row['reserved_cny'])>=4),
            'notice':f"4.5.2独立授权：已用{row['attempted_calls']}/11次，预算预占{row['reserved_cny']}/4元；无自动重试，旧额度不变。"}

class RetestBudget:
    MAX_CALLS=11
    MAX_RESERVED_CNY=Decimal('4')
    def __init__(self):
        self.policy=policy()
        if not self.policy: raise CopilotError('source_consent_required','4.5.2没有明确授权。','authorization')
        self.path=LEDGER_PATH; self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as c:
            c.execute('CREATE TABLE IF NOT EXISTS session_budget(session_id TEXT PRIMARY KEY,attempted_calls INTEGER NOT NULL,reserved_cny TEXT NOT NULL,closed INTEGER NOT NULL,qa_enabled INTEGER NOT NULL,updated_at TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS admissions(call_number INTEGER PRIMARY KEY,stage TEXT NOT NULL UNIQUE,request_fingerprint TEXT NOT NULL UNIQUE,input_bytes INTEGER NOT NULL,input_token_upper INTEGER NOT NULL,output_token_upper INTEGER NOT NULL,reservation_cny TEXT NOT NULL,cumulative_cny TEXT NOT NULL,file_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)')
            c.execute("INSERT OR IGNORE INTO session_budget VALUES(?,0,'0',0,0,?)",(SESSION,now()))
        self.stage=self.fingerprint=None

    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=10); c.row_factory=sqlite3.Row
        try: yield c; c.commit()
        except BaseException: c.rollback(); raise
        finally: c.close()

    def bind(self,stage,fingerprint):
        if stage not in READING_STAGES|QA_STAGES:
            raise CopilotError('validation_scope_mismatch','该阶段没有新增收费授权；历史步骤必须精确复用。','budget')
        self.stage,self.fingerprint=stage,fingerprint

    def reserve(self,input_bytes,output_limit):
        if not self.stage or not self.fingerprint or not 1<=input_bytes<=60000 or not 200<=output_limit<=2000:
            raise CopilotError('request_budget_too_large','超过60000字节／2000输出token或未绑定阶段，未发送。','budget')
        upper=input_bytes+4096
        amount=2*(Decimal(upper)*2+Decimal(output_limit)*8)/1_000_000
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM session_budget WHERE session_id=?',(SESSION,)).fetchone()
            total=Decimal(row['reserved_cny'])+amount
            if row['closed'] or row['attempted_calls']>=11 or total>4:
                raise CopilotError('validation_budget_exhausted','4.5.2次数／4元预算不足或已关闭，未发送。','budget')
            if self.stage in QA_STAGES and not row['qa_enabled']:
                raise CopilotError('validation_phase_not_ready','先完成真实精读及双卡比较，再启用有限问答复测。','budget')
            if c.execute('SELECT 1 FROM admissions WHERE stage=? OR request_fingerprint=?',(self.stage,self.fingerprint)).fetchone():
                raise CopilotError('validation_stage_already_attempted','本阶段已尝试，禁止自动重复收费。','budget')
            number=row['attempted_calls']+1
            c.execute('UPDATE session_budget SET attempted_calls=?,reserved_cny=?,updated_at=? WHERE session_id=?',(number,str(total),now(),SESSION))
            c.execute('INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,?,?)',(number,self.stage,self.fingerprint,input_bytes,upper,output_limit,str(amount),str(total),self.policy['file_sha256'],now()))
        return {'session_id':SESSION,'call_number':number,'max_calls':11,'input_bytes':input_bytes,'input_token_upper':upper,'output_token_upper':output_limit,
                'reservation_cny':str(amount),'total_reserved_cny':str(total),'remaining_calls':11-number,'remaining_cny':str(Decimal(4)-total),'price_check_date':self.policy['rates_checked_date']}

    def estimate(self,usage):
        if not all(isinstance(usage.get(k),int) for k in ('prompt_tokens','completion_tokens')): return None
        return str((Decimal(usage['prompt_tokens'])*2+Decimal(usage['completion_tokens'])*8)/1_000_000)

    def close(self):
        with self.connection() as c: c.execute('UPDATE session_budget SET closed=1,updated_at=? WHERE session_id=?',(now(),SESSION))


class RetestClient:
    is_test_double=False
    max_resume_depth=2
    allow_scope_audit=False
    def __init__(self,adapter,doc):
        self.adapter,self.config,self.calls,self.budget=adapter,adapter.config,adapter.calls,adapter.budget
        p=self.budget.policy
        if doc['file_sha256']!=p['file_sha256'] or any(getattr(self.config,k)!=p[k] for k in ('provider','model','base_url')):
            raise CopilotError('validation_scope_mismatch','资料／模型不属于4.5.2授权，未发送。','authorization')
        self.doc=doc; self.current=None; adapter.response_observer=self._response

    def dump(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        text=json.dumps(value,ensure_ascii=False,indent=2).replace(self.config.api_key,'[REDACTED]')
        with path.open('x',encoding='utf-8') as f: f.write(text+'\n')

    def _response(self,stage,raw):
        number=summary()['attempted_calls']
        self.dump(ARTIFACT_ROOT/f'response_{number:02d}.json',{'stage':stage,'request_fingerprint':self.current,'body_utf8':raw.decode('utf-8',errors='replace')})

    def completed(self,root,request,key,session):
        path=root/'requests'/(key+'.json')
        if not path.is_file() or json.loads(path.read_text(encoding='utf-8'))!=json.loads(json.dumps(request,ensure_ascii=False)): return None
        for p in sorted(root.glob('response_*.json')):
            saved=json.loads(p.read_text(encoding='utf-8'))
            if saved.get('request_fingerprint')!=key: continue
            number=int(p.stem.split('_')[-1]); tracepath=root/f'trace_{number:02d}.json'
            if not tracepath.is_file(): continue
            trace=json.loads(tracepath.read_text(encoding='utf-8'))
            if trace.get('status')!='completed' or trace.get('scope_sha256')!=self.doc['file_sha256'] or trace.get('request_fingerprint')!=key: continue
            body=json.loads(saved['body_utf8']); choice=body['choices'][0]
            if choice.get('finish_reason') not in ('stop',None): continue
            content=choice['message']['content'].strip()
            if content.startswith('```json\n') and content.endswith('```'): content=content[8:-3].strip()
            result=json.loads(content)
            if not isinstance(result,dict): continue
            audit={'stage':request['stage'],'model':self.config.model,'status':'not_sent','response_reused':True,'original_session_id':session,
                   'original_call_number':number,'request_fingerprint':key,'scope_sha256':self.doc['file_sha256'],
                   'original_response_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            self.calls.append(audit)
            return deepcopy(result)
        return None

    def complete_json(self,stage,system,user):
        request={'stage':stage,'file_sha256':self.doc['file_sha256'],'model_info':self.config.public_info(),'system':system,'user':user}
        key=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.current=key
        if stage in REUSED_STAGES:
            result=self.completed(OLD_ROOT,request,key,'stage4_5_p05_e2e_17calls_5cny')
            if result is None:
                raise CopilotError('historical_reuse_mismatch','历史步骤输入／Prompt／来源不一致，停止且不付费补跑。',stage)
            auditpath=ARTIFACT_ROOT/'reused'/(key+'.json')
            if not auditpath.exists(): self.dump(auditpath,self.calls[-1])
            return result
        result=self.completed(ARTIFACT_ROOT,request,key,SESSION)
        if result is not None: return result
        self.budget.bind(stage,key)
        path=ARTIFACT_ROOT/'requests'/(key+'.json')
        if not path.exists(): self.dump(path,request)
        start=len(self.calls)
        try: return self.adapter.complete_json(stage,system,user)
        finally:
            for trace in self.calls[start:]:
                trace.update(request_fingerprint=key,scope_sha256=self.doc['file_sha256'])
                if 'budget_reservation' in trace:
                    self.dump(ARTIFACT_ROOT/f"trace_{trace['budget_reservation']['call_number']:02d}.json",trace)

def client_for_document(config,repository,doc):
    from .llm import create_client
    return RetestClient(create_client(config,repository,validation_profile=SESSION),doc)

def enable_qa_after_comparison(engine,comparison_id,version):
    """Explicit operator transition, only after a saved genuine two-card result.

    No model call; default UI remains opt-out of independent scope auditing.
    """
    result=engine.load(comparison_id,version)
    cols=result['columns']
    if result['stale'] or len(cols)!=2 or not all(c['source_kind']=='saved_card' and c['execution_mode']=='real_api' for c in cols):
        raise CopilotError('validation_phase_not_ready','必须先保存且恢复两张真实卡的有效比较。','budget')
    if not any(c['file_sha256']==policy()['file_sha256'] for c in cols):
        raise CopilotError('validation_scope_mismatch','比较未包含获准的P05。','budget')
    budget=RetestBudget()
    with budget.connection() as c:
        stages={r[0] for r in c.execute('SELECT stage FROM admissions')}
        if not READING_STAGES.issubset(stages):
            raise CopilotError('validation_phase_not_ready','剩余四个精读模块尚未完成。','budget')
        c.execute('UPDATE session_budget SET qa_enabled=1,updated_at=? WHERE session_id=? AND closed=0',(now(),SESSION))
    path=ARTIFACT_ROOT.parent/'qa_phase_enabled.json'
    if not path.exists():
        with path.open('x',encoding='utf-8') as f: json.dump({'at':now(),'comparison_id':comparison_id,'version':version,'scope_audit_default':False},f,ensure_ascii=False,indent=2)
