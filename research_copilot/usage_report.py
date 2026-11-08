"""Read-only, deduplicated usage totals from actual request records and historical traces."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

ZONE=timezone(timedelta(hours=8))


def number(value):
    return int(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0 else 0


def report(service,store,query):
    entries=[];seen=set();native={}
    docs={d['document_version_id']:d for d in service.list_documents()}

    def model(trace,record,provider=None):
        if trace.get('status') in ('mock','not_sent') or not trace.get('model'):return
        identity=trace.get('usage_record_id') or trace.get('budget_reservation') or [record.get('run_id',record.get('card_id',record.get('id'))),trace]
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        if key in seen:return
        seen.add(key);usage=trace.get('usage') or {}
        input_tokens=number(usage.get('prompt_tokens',usage.get('input_tokens')))
        output_tokens=number(usage.get('completion_tokens',usage.get('output_tokens')))
        entries.append({'id':key,'kind':'llm','created_at':record.get('created_at',''),'provider':trace.get('provider') or provider or 'unknown',
                        'model':trace['model'],'stage':trace.get('stage',''),'status':trace.get('status','unknown'),
                        'input_tokens':input_tokens,'output_tokens':output_tokens,'total_tokens':number(usage.get('total_tokens',input_tokens+output_tokens)),
                        'usage_reported':bool(usage),'characters':0,'document_version_id':record.get('document_version_id')})

    for row in store.records('model_usage'):model(row,row)
    for table,column in (('qa_runs','result_json'),('reading_cards','record_json')):
        if not service.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(table,)):continue
        for row in service.db.rows(f'SELECT {column} FROM {table}'):
            record=json.loads(row[column])
            if record.get('execution_mode')!='real_api':continue
            for trace in record.get('request_traces',[]):
                if not trace.get('usage_record_id'):model(trace,record,record.get('model_info',{}).get('provider','deepseek'))
    for kind in ('translation','translation_failure'):
        for record in store.records(kind):
            for trace in record.get('request_traces',record.get('traces',[])):
                if trace.get('admission_id'):native[trace['admission_id']]=trace
                elif not trace.get('usage_record_id'):model(trace,record,record.get('model_info',{}).get('provider','deepseek'))
    success={row['admission_id'] for row in store.records('aliyun_usage_result') if row.get('status')=='success'}
    for row in store.records('translation_admission'):
        trace=native.get(row['id'],{})
        ok=row['id'] in success or trace.get('status')=='returned'
        entries.append({'id':row['id'],'kind':'translation','created_at':row['created_at'],'provider':row['provider'],'model':row.get('api',''),
                        'stage':'translation','status':'completed' if ok else 'submitted','characters':number(row['characters']),
                        'input_tokens':0,'output_tokens':0,'total_tokens':0,'usage_reported':True,'document_version_id':row.get('document_version_id')})
    period=query.get('period',['month'])[0];provider=query.get('provider',['all'])[0]
    now=datetime.now(ZONE);cutoff=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0) if period=='month' else now-timedelta(days=7) if period=='week' else None
    filtered=[]
    for row in entries:
        try:
            stamp=datetime.fromisoformat(row['created_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
            row['date']=stamp.astimezone(ZONE).strftime('%Y-%m-%d')
            if cutoff and stamp<cutoff:continue
        except ValueError:
            row['date']='未知日期'
            if cutoff:continue
        if provider!='all' and row['provider']!=provider:continue
        doc=docs.get(row['document_version_id'],{})
        row['paper_title']=doc.get('title') or doc.get('source_file') or '未关联论文'
        filtered.append(row)
    totals={'requests':len(filtered),'input_tokens':0,'output_tokens':0,'total_tokens':0,'characters':0,'successful_characters':0,'unknown_token_requests':0}
    groups={};days={}
    for row in filtered:
        for key in ('input_tokens','output_tokens','total_tokens','characters'):totals[key]+=row[key]
        if row['kind']=='translation' and row['status']=='completed':totals['successful_characters']+=row['characters']
        if row['kind']=='llm' and not row['usage_reported']:totals['unknown_token_requests']+=1
        for group,key in ((groups,row['provider']),(days,row['date'])):
            item=group.setdefault(key,{'name':key,'requests':0,'tokens':0,'characters':0})
            item['requests']+=1;item['tokens']+=row['total_tokens'];item['characters']+=row['characters']
    filtered.sort(key=lambda row:row['created_at'],reverse=True)
    offset=max(0,int(query.get('offset',['0'])[0]));limit=50
    return {'totals':totals,'providers':sorted(set(row['provider'] for row in entries)),'by_provider':list(groups.values()),
            'daily':sorted(days.values(),key=lambda row:row['name']),'records':filtered[offset:offset+limit],
            'offset':offset,'has_more':offset+limit<len(filtered),'period':period,'timezone':'Asia/Shanghai'}
