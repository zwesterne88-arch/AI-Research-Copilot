"""Source ranges first, translations second. Never translate a historical summary.

Independent workspace snapshots leave research-reading-v1 cards immutable.
Paragraph navigation uses PDF geometry where available; every displayed character
is still an exact slice of the original parser text, not a rewritten PDF/index.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib,json,re,uuid,threading
from pathlib import Path
from .errors import CopilotError
from .evidence import locate_quote
from .research_structure import structure
from .reading_schema import LABELS
from .repository import now

SCHEMA='source-reading-v1'
STRATEGY='paragraph-ranges-v1'
FIELDS=('research_questions','background','prior_work_gaps','motivation','claimed_contributions','method','validation','findings','conclusions','author_limitations','future_work')
GAP=r'however|although|\bbut\b|cannot|can not|not (?:able|effective)|no .{0,60}(?:able|detect)|lack|limitation|disadvantage|research gap|difficult|impractical|struggle|limited|不足|难以|缺乏'
NEED=r'important|critical|necessary|need|to (?:address|overcome|solve)|therefore|hence|consequently|objective|aim|motivat|why|必要|为了解决'
PROBLEM=GAP+r'|addresses|we investigate|objective|aim|challenge|research question|problem|待解决|研究目标'
SENTENCE=re.compile(r'(?<=[.!?。！？])\s+(?=[A-Z“"(\d\u4e00-\u9fff])')
LOCK=threading.Lock()

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def terminal(text):return bool(re.search(r'[.!?。！？][\]"”)]*\s*$',text))

@lru_cache(maxsize=8)
def geometry(path,version):
    import unicodedata
    import pdfplumber
    from pdfminer.layout import LTTextBoxHorizontal,LTTextLineHorizontal
    from pypdf import PdfReader
    raws=[p.extract_text() or '' for p in PdfReader(path).pages]
    result={}
    with pdfplumber.open(path,laparams={'line_margin':0.45,'all_texts':True}) as pdf:
        for number,page in enumerate(pdf.pages,1):
            raw=raws[number-1];chars=[];mapping=[]
            for i,ch in enumerate(raw):
                for c in unicodedata.normalize('NFKC',ch):
                    if not c.isspace():chars.append(c);mapping.append(i)
            hay=''.join(chars);cuts=set();excluded=[]
            def boxes(node):
                if isinstance(node,LTTextBoxHorizontal):yield node
                elif hasattr(node,'__iter__'):
                    for child in node:yield from boxes(child)
            for box in boxes(page.layout):
                previous=None
                for line in box:
                    if not isinstance(line,LTTextLineHorizontal):continue
                    text=line.get_text().strip();needle=''.join(unicodedata.normalize('NFKC',text).split());at=hay.find(needle)
                    if not needle or at<0 or hay.find(needle,at+1)>=0:continue
                    a,b=mapping[at],mapping[at+len(needle)-1]+1
                    furniture=(line.y0>page.height-55 or line.y1<45) and (len(text)<100)
                    furniture=furniture or bool(re.match(r'(?:E-mail address(?:es)?:|[∗*] Corresponding author|https?://doi\.org/|Received \d|Available online|©)',text))
                    if furniture:excluded.append((a,b));continue
                    if previous is None:cuts.add(a)
                    else:
                        dx=line.x0-previous.x0
                        if 4<dx<32 and terminal(previous.get_text()):cuts.add(a)
                    previous=line
            result[number]={'raw_hash':fingerprint(raw),'cuts':sorted(cuts),'excluded':excluded}
    return result


def catalog(service,doc):
    info=structure(service,doc);pages=service.get_pages(doc['document_version_id']);joined='';locations=[]
    for page in pages:
        start=len(joined);joined+=page['raw_text']+'\n';locations.append((page['pdf_page'],start,start+len(page['raw_text'])))
    sections=deepcopy(info['sections'])
    for m in re.finditer(r'(?im)^\s*(CRediT authorship contribution statement|Declaration of competing interest|Acknowledg(?:e)?ments?|Data availability|Funding|Author contributions)\s*$',joined):sections.append({'name':'Publication metadata','start':m.start(),'content_start':m.end()})
    # Reading-only section expansion: retain the stable RAG retriever contract.
    # Unnumbered scientific headings and IEEE inline INTRODUCTION occur in
    # otherwise well-parsed PDFs; neither should disappear into Methods.
    for m in re.finditer(r'(?im)^[ \t]*(?:(?:\d+(?:\.\d+)*|[IVX]+)[.)]?\s+)?(Experimental (?:setup|settings?|results?|evaluation|design|protocol|analysis)|Experiments?|Evaluation|Results?(?: and Discussion)?|Discussion|Limitations?|Materials and Methods|Case Stud(?:y|ies)|Proof|Methods?|Methodology)[ \t]*$',joined):
        if not any(abs(s['start']-m.start())<4 for s in sections):sections.append({'name':m.group(1).strip(),'start':m.start(),'content_start':m.end()})
    for m in re.finditer(r'(?im)^[ \t]*(?:(?:\d+(?:\.\d+)*|[IVX]+)[.)]?\s+)?(INTRODUCTION)(?=\s+[A-Z])',joined):
        if not any(abs(s['start']-m.start())<4 for s in sections):sections.append({'name':'Introduction','start':m.start(),'content_start':m.end()})
    # Recognize numbered headings without hardcoding a paper's method names.
    reference_start=min((s['start'] for s in sections if s['name'].lower() in ('references','参考文献')),default=len(joined))
    for m in re.finditer(r'(?m)^[ \t]*(?:\d+(?:\.\d+)*\.?|[IVX]+\.)[ \t]+([A-Z][^\n]{2,90})[ \t]*$',joined):
        name=m.group(1).strip()
        if m.start()>=reference_start:continue
        if len(name.split())>12 or re.search(r'[;!?]$|\.\s*$',name):continue
        if not any(abs(s['start']-m.start())<4 for s in sections):sections.append({'name':name,'start':m.start(),'content_start':m.end()})
    sections.sort(key=lambda s:s['start'])
    for i,s in enumerate(sections):s['end']=sections[i+1]['start'] if i+1<len(sections) else len(joined)
    geom={}
    try:geom=geometry(str(service.store.check(doc['file_sha256'])),doc['parser_version'])
    except (AttributeError,OSError,ValueError,ImportError):pass
    paragraphs=[]
    for section in sections:
        if section['name'].lower() in ('references','参考文献','publication metadata'):continue
        start=section.get('content_start',section['start']);end=section['end'];parts=[]
        for number,left,right in locations:
            a=max(start,left)-left;b=min(end,right)-left
            if a>=b:continue
            raw=pages[number-1]['raw_text'];g=geom.get(number,{})
            if g.get('raw_hash')!=fingerprint(raw):g={}
            cuts={a,b}|{x for x in g.get('cuts',[]) if a<x<b}
            cuts|={a+m.end() for m in re.finditer(r'\n\s*\n',raw[a:b])}
            excluded=g.get('excluded',[])
            # Page numbers may be ambiguous strings in the text body. Only
            # remove the physical number in the already identified page header.
            if any(x<200 for x,y in excluded):
                excluded=list(excluded)+[(m.start(),m.end()) for m in re.finditer(r'(?m)^'+str(number)+r'\s*$',raw[:200])]
            for x,y in excluded:
                if a<x<b:cuts.add(x)
                if a<y<b:cuts.add(y)
            ordered=sorted(cuts)
            for x,y in zip(ordered,ordered[1:]):
                if any(lo<=x and y<=hi for lo,hi in excluded):continue
                value=raw[x:y];lo=x+len(value)-len(value.lstrip());hi=x+len(value.rstrip())
                if hi>lo:parts.append({'pdf_page':number,'char_start':lo,'char_end':hi,'text':raw[lo:hi]})
        # Coalesce exact contiguous slices within each physical paragraph; PDF
        # navigation must not present one button per rendered line/font change.
        for part in parts:
            # Cross-column/page continuation stays one reading unit with several
            # anchors. Page furniture is excluded, but never rewrite the text.
            previous=len(paragraphs)-1
            # A top-of-page figure caption can precede the continuation of a
            # paragraph from the prior page. Keep the caption separately without
            # letting it sever that paragraph's meaning or source association.
            while previous>=0 and paragraphs[previous]['section']==section['name'] and re.match(r'^(?:Fig(?:ure)?\.?|Table|Algorithm)\s*\d',paragraphs[previous]['text']):previous-=1
            continuation=previous>=0 and paragraphs[previous]['section']==section['name'] and not terminal(paragraphs[previous]['text']) and bool(re.match(r'[a-z(δ𝛿]',part['text']))
            if continuation:
                paragraphs[previous]['spans'].append(part);paragraphs[previous]['text']+='\n'+part['text']
            else:paragraphs.append({'section':section['name'],'spans':[part],'text':part['text'],'boundary':'geometry' if geom else 'blank_lines_or_section'})
    return info,paragraphs

def sentences(paragraph):
    result=[];offset=0
    for piece in SENTENCE.split(paragraph['text']):
        a=paragraph['text'].find(piece,offset);offset=a+len(piece)
        result.append((a,offset,piece))
    return result

def crop(paragraph,a,b):
    out=[];position=0
    for span in paragraph['spans']:
        size=len(span['text']);lo=max(0,a-position);hi=min(size,b-position)
        if lo<hi:out.append({**span,'char_start':span['char_start']+lo,'char_end':span['char_start']+hi,'text':span['text'][lo:hi]})
        position+=size+1
    return {**paragraph,'spans':out,'text':'\n'.join(s['text'] for s in out),'boundary':'rhetorical_sentence_range'}

def sentence_focus(paragraph,pattern,prefix=False):
    ss=sentences(paragraph);hits=[i for i,(_,_,text) in enumerate(ss) if re.search(pattern,text,re.I)]
    if prefix:
        end=hits[0] if hits else len(ss)
        return [crop(paragraph,ss[0][0],ss[end-1][1])] if end else []
    # Keep adjacent matching sentences together; do not omit any matched range.
    groups=[]
    for i in hits:
        if groups and i==groups[-1][-1]+1:groups[-1].append(i)
        else:groups.append([i])
    return [crop(paragraph,ss[g[0]][0],ss[g[-1]][1]) for g in groups]

def overlap(a,b):return a['pdf_page']==b['pdf_page'] and a['char_start']<b['char_end'] and b['char_start']<a['char_end']

def make_unit(doc,paragraph,origin,**extra):
    spans=paragraph['spans'];identity=[{k:s[k] for k in ('pdf_page','char_start','char_end','text')} for s in spans]
    uid=fingerprint([doc['document_version_id'],identity])
    refs=[{**{k:doc[k] for k in ('paper_id','document_version_id','file_sha256','parser_version','chunker_version','index_version')},
           'pdf_page':s['pdf_page'],'quote_char_start':s['char_start'],'quote_char_end':s['char_end'],'char_start':s['char_start'],'char_end':s['char_end'],
           'quote':s['text'],'text':s['text'],'section_hint':paragraph['section'],'semantic_support':'unverifiable'} for s in spans]
    return {'unit_id':uid,'original_text':'\n'.join(s['text'] for s in spans),'section':paragraph['section'],'sources':refs,
            'origin':origin,'boundary':paragraph.get('boundary','author_list'),'human_review':'unreviewed','translation':None,**extra}

def build(service,doc,card=None):
    info,paragraphs=catalog(service,doc);fields={};facts=(card or {}).get('structured_content',{}).get('facts',{})
    for field in FIELDS:
        accepted=[f for f in facts.values() if f['field']==field and f.get('kind')!='analysis']
        refs=[card['evidence_references'][eid] for f in accepted for eid in f['evidence_ids']]
        chosen=[];method='historical_citation_expanded_to_paragraph'
        front=[p for p in paragraphs if re.search('abstract|introduction|problem|objective|引言',p['section'],re.I)]
        if field=='claimed_contributions' and info['contributions']:
            fields[field]={'units':[make_unit(doc,{'section':x['section'],'spans':[{**s,'text':service.get_pages(doc['document_version_id'])[s['pdf_page']-1]['raw_text'][s['char_start']:s['char_end']]} for s in x['spans']],
                'text':x['original_text']},'author_enumerated',ordinal=x['ordinal'],author_marker=x['author_marker'],author_number=x['author_number']) for x in info['contributions']], 'location':'located','coverage':'author_list_complete_in_detected_scope'}
            continue
        if field=='research_questions':
            abstract=[p for p in front if p['section']=='Abstract']
            chosen=[x for p in abstract for x in sentence_focus(p,PROBLEM)]
            if not chosen:chosen=[p for p in front if re.search(PROBLEM,p['text'],re.I)]
            if not chosen:chosen=abstract
            method='section_and_rhetorical_range'
        elif field=='background':
            abstracts=[p for p in front if p['section']=='Abstract']
            chosen=[x for p in abstracts[:1] for x in sentence_focus(p,GAP+'|we propose|this paper|objective',prefix=True)]
            if not chosen:chosen=[p for p in front if p['section'].lower()=='introduction'][:1]
            method='background_range_before_gap_transition'
        elif field=='prior_work_gaps':
            chosen=[x for p in paragraphs if p['section']=='Abstract' for x in sentence_focus(p,GAP)]
            chosen += [p for p in paragraphs if re.search('introduction|related|引言|相关',p['section'],re.I) and re.search(GAP,p['text'],re.I)]
            method='author_shortcoming_paragraphs'
        elif field=='motivation':
            chosen=[p for p in front if p['section'].lower()=='introduction' and re.search(NEED,p['text'],re.I)]
            method='introduction_need_and_intent_paragraphs'
        # For later fields keep own quotes/context together instead of joining
        # facts/numbers from unrelated datasets into a synthesized result.
        if not chosen:
            chosen=[p for p in paragraphs if any(overlap(s,{'pdf_page':r['pdf_page'],'char_start':r.get('quote_char_start',-1),'char_end':r.get('quote_char_end',-1)}) for s in p['spans'] for r in refs)]
        if field in ('method','validation','findings','conclusions','future_work'):
            pattern={'method':r'method|approach|mechanism|algorithm|architecture|framework|方法|机制',
                     'validation':r'experiment|evaluation|dataset|environment|validation|验证|实验',
                     'findings':r'result|discussion|结果','conclusions':r'conclusion|结论','future_work':r'future|未来'}[field]
            extra=[p for p in paragraphs if re.search(pattern,p['section'],re.I)]
            if field=='future_work':extra=[p for p in paragraphs if re.search(r'future work|in future|we (?:will|plan)|未来',p['text'],re.I)]
            chosen+=extra
        if field=='author_limitations' and not chosen:
            chosen=[p for p in paragraphs if re.search('limitation|discussion|conclusion|局限',p['section'],re.I) and
                    re.search(r'our|\bwe\b|this (?:study|work)|本研究|我们',p['text'],re.I) and re.search(r'limitation|however|risk|cannot|局限',p['text'],re.I)]
        units=[];seen=[]
        for p in chosen:
            if any(p['text']==q['text'] or all(any(s['pdf_page']==t['pdf_page'] and t['char_start']<=s['char_start'] and t['char_end']>=s['char_end'] for t in q['spans']) for s in p['spans']) for q in seen):continue
            seen.append(p);units.append(make_unit(doc,p,method,key_passage=any(overlap(s,{'pdf_page':r['pdf_page'],'char_start':r.get('quote_char_start',-1),'char_end':r.get('quote_char_end',-1)}) for s in p['spans'] for r in refs)))
        # Read in the author's physical order, not in historical citation or
        # retrieval-channel order. In particular the full method introduction
        # must precede later quoted implementation substeps.
        units.sort(key=lambda u:(u['sources'][0]['pdf_page'],u['sources'][0]['quote_char_start']))
        fields[field]={'units':units,'location':'located' if units else 'not_found_in_checked_scope','coverage':'partial_related_scope' if field=='research_questions' and units and not any(re.search(PROBLEM,u['original_text'],re.I) for u in units) else 'not_exhaustive'}
    # Same source may serve two roles, but it retains one identity and exact range.
    uses={}
    for field,value in fields.items():
        for unit in value['units']:uses.setdefault(unit['unit_id'],[]).append(field)
    for value in fields.values():
        for unit in value['units']:unit['shared_fields']=uses[unit['unit_id']]
    return {'schema_version':SCHEMA,'strategy':STRATEGY,**{k:doc[k] for k in ('paper_id','document_version_id','file_sha256')},
            'base_card_id':card['card_id'] if card else None,'base_card_version':card['card_version'] if card else None,
            'fields':fields,'source_reading_version':None,'historical_card_unchanged':True}

def validate(service,doc,book):
    if any(book.get(k)!=doc[k] for k in ('paper_id','document_version_id','file_sha256')):raise CopilotError('source_reading_scope','原文阅读记录不属于当前论文版本。','evidence')
    pages={p['pdf_page']:p['raw_text'] for p in service.get_pages(doc['document_version_id'])}
    for value in book['fields'].values():
        for unit in value['units']:
            for r in unit['sources']:
                raw=pages.get(r['pdf_page'],'');a,b=r['quote_char_start'],r['quote_char_end']
                if any(r.get(k)!=doc[k] for k in ('paper_id','document_version_id','file_sha256','parser_version','chunker_version','index_version')) or not 0<=a<b<=len(raw) or raw[a:b]!=r['quote']:
                    raise CopilotError('source_reading_locator','原文区间或版本不一致。','evidence')
            if '\n'.join(r['quote'] for r in unit['sources'])!=unit['original_text']:raise CopilotError('source_reading_text','原文与来源范围不一致。','evidence')
            tr=unit.get('translation')
            if tr and tr['source_fingerprint']!=fingerprint(unit['original_text']):raise CopilotError('translation_source_mismatch','译文与当前英文范围不一致。','translation')

def historical_diagnostics(card,field):
    if not card:return {'extraction':'not_run','saved_facts':0,'human_review':'unreviewed'}
    from .reading_schema import MODULE_FIELDS
    module=next(m for m,fs in MODULE_FIELDS.items() if field in fs)
    raw=card.get('raw_outputs',{}).get(module+':extract',{});facts=[x for x in raw.get('facts',[]) if x['field']==field]
    state=card['structured_content']['fields'][field]
    ids={'C'+str(i+1) for i,f in enumerate(raw.get('facts',[])) if f['field']==field}
    review=card.get('raw_outputs',{}).get(module+':review',{})
    conflict=any(r.get('verdict')=='contradicted' or any(s.get('support_level')=='conflicting_or_unsupported' for s in r.get('citation_support',[])) for r in review.get('claim_reviews',[]) if r.get('claim_id') in ids)
    return {'card_id':card['card_id'],'card_version':card['card_version'],'extraction':'no_model_output' if not facts else 'withheld_by_validation' if len(state['fact_ids'])<len(facts) else 'saved',
            'raw_facts':facts,'saved_facts':len(state['fact_ids']),'field_state':state,'review':card.get('raw_outputs',{}).get(module+':review'),
            'citation_conflict':conflict,'human_review':card.get('human_review','unreviewed'),'note':'旧卡为短事实结构，不是完整原文译文；未人工核查不禁止阅读已定位原文。'}

def split_request(ref,limit=2500,byte_limit=None):
    text=ref['quote'];out=[];a=0
    while a<len(text):
        b=min(len(text),a+limit)
        if byte_limit:
            while len(text[a:b].encode())>byte_limit:b-=1
        if b<len(text):
            ends=[a+m.start() for m in SENTENCE.finditer(text[a:b])]
            if ends:b=ends[-1]
        if b<=a:raise CopilotError('translation_range','无法安全分段，请手工缩小原文范围。','translation')
        # Retain all chars; transport fragments reassemble to the full paragraph.
        part={**ref,'quote':text[a:b],'text':text[a:b],'quote_char_start':ref['quote_char_start']+a,'quote_char_end':ref['quote_char_start']+b}
        out.append(part);a=b
    return out

def plan(book,unit_id,provider,glossary=None):
    if provider not in ('mock','aliyun','baidu','google','deepl','azure','argos','libretranslate','mymemory','deepseek'):raise CopilotError('provider_unknown','请选择已有翻译引擎。','translation')
    unit=next((u for f in book['fields'].values() for u in f['units'] if u['unit_id']==unit_id),None)
    if not unit:raise CopilotError('source_unit_missing','这段原文不属于所选阅读记录。','evidence')
    chunks=[p for r in unit['sources'] for p in split_request(r,byte_limit=500 if provider=='mymemory' else None)]
    material={'unit_id':unit_id,'provider':provider,'glossary':glossary or {},'chunks':chunks}
    requests=1 if provider in ('argos','google') else len(chunks)
    if provider=='baidu':
        from .translation_providers import settings
        from .prose import reading_prose,request_segments
        limit=settings('baidu')['request_character_limit'];material['transport_limit']=limit;requests=len(request_segments(reading_prose(unit['original_text']),limit,6000))
    if provider=='aliyun':
        from .aliyun_translation import prepare
        prepared=prepare(unit['original_text'],glossary);requests=2*len(prepared['chunks']);material['aliyun_protection']='aliyun-general-v1-protected'
    size=sum(len(c['quote']) for c in chunks)
    if provider=='aliyun':size=prepared['characters']
    return {'plan_id':fingerprint(material),'unit_id':unit_id,'provider':provider,'requests':requests,'characters':size,
            'input_token_upper_estimate':sum(len(c['quote'].encode())+8192+len(json.dumps(glossary or {},ensure_ascii=False).encode()) for c in chunks) if provider=='deepseek' else None,
            'reservation_cny_upper_estimate':round(sum(2*((len(c['quote'].encode())+8192+len(json.dumps(glossary or {},ensure_ascii=False).encode()))*2+2000*8)/1000000 for c in chunks),6) if provider=='deepseek' else 0,
            'cost_kind':'paid_session_reservation' if provider=='deepseek' else 'mock' if provider=='mock' else 'characters',
            'chunks':chunks,'glossary_fingerprint':fingerprint(glossary or {})}

class SourceReading:
    def __init__(self,service,store):self.service,self.store=service,store;self.cache={}
    def load(self,doc,card=None,snapshot_id=None,fresh=False):
        records=sorted(self.store.records('source_reading',doc['document_version_id']),key=lambda r:r['source_reading_version'],reverse=True)
        saved=next((r for r in records if (r['id']==snapshot_id if snapshot_id else r.get('base_card_id')==(card or {}).get('card_id'))),None)
        if snapshot_id and not saved:raise CopilotError('source_reading_scope','阅读版本不属于当前论文。','evidence')
        if saved and saved.get('base_card_id')!=(card or {}).get('card_id'):raise CopilotError('source_reading_card_scope','原文阅读版本与所选历史卡不对应。','evidence')
        key=(doc['document_version_id'],doc['parser_version'],doc['index_version'],(card or {}).get('card_id'))
        if fresh or key not in self.cache:
            self.service.store.check(doc['file_sha256'])
            cache_file=self.service.root/'source_reading_layout'/(fingerprint([key,fingerprint(Path(__file__).read_text(encoding='utf-8'))])+'.json')
            cached=None
            if cache_file.is_file():
                try:cached=json.loads(cache_file.read_text(encoding='utf-8'));validate(self.service,doc,cached)
                except (ValueError,KeyError,CopilotError):cached=None
            self.cache[key]=cached or build(self.service,doc,card)
            if not cached:
                cache_file.parent.mkdir(parents=True,exist_ok=True);cache_file.write_text(json.dumps(self.cache[key],ensure_ascii=False),encoding='utf-8')
        book=deepcopy(saved if saved and not fresh else self.cache[key]);validate(self.service,doc,book)
        records=self.store.records('translation',doc['document_version_id'])
        glossaries=self.store.records('glossary',doc['document_version_id']);glossary=glossaries[0]['terms'] if glossaries else {}
        hydrated={}
        for value in book['fields'].values():
            for unit in value['units']:
                if unit.get('translation'):continue
                if unit['unit_id'] in hydrated:unit['translation']=deepcopy(hydrated[unit['unit_id']]);continue
                from .translation import source_identity
                exact=next((r for r in records if r.get('provider') in ('aliyun','argos','google','baidu','deepl','azure') and (source_identity(r.get('source_ranges',[]))==source_identity(unit['sources']) if r.get('provider')=='aliyun' else r.get('source_ranges')==unit['sources']) and r.get('source_text')==unit['original_text'] and fingerprint(r.get('glossary',{}))==fingerprint(glossary)),None)
                if exact:
                    unit['translation']=passage_payload(unit,exact);unit['translation']['origin']='reused_exact_saved_translation';hydrated[unit['unit_id']]=deepcopy(unit['translation']);continue
                for provider in dict.fromkeys(r.get('provider','mock' if r.get('mode')=='mock' else 'deepseek') for r in records):
                    p=plan(book,unit['unit_id'],provider,glossary);matches=[]
                    for chunk in p['chunks']:
                        chunk_text=chunk['quote'];a=chunk['quote_char_start']+len(chunk_text)-len(chunk_text.lstrip());b=a+len(chunk_text.strip())
                        match=next((r for r in records if r.get('provider','mock' if r.get('mode')=='mock' else 'deepseek')==provider and
                            r['anchor']['document_version_id']==doc['document_version_id'] and r['anchor']['file_sha256']==doc['file_sha256'] and
                            r['anchor']['pdf_page']==chunk['pdf_page'] and r['anchor'].get('char_start')==a and r['anchor'].get('char_end')==b and
                            r['anchor']['text'].strip()==chunk_text.strip() and fingerprint(r.get('glossary',{}))==fingerprint(glossary)),None)
                        if not match:break
                        matches.append(match)
                    if len(matches)==len(p['chunks']):
                        unit['translation']=translation_payload(unit,p,matches,None);unit['translation']['origin']='reused_exact_saved_translation'
                        hydrated[unit['unit_id']]=deepcopy(unit['translation']);break
        for field,value in book['fields'].items():
            value['diagnostics']=historical_diagnostics(card,field);value['conflict']=value['diagnostics'].get('citation_conflict',False)
        return book
    def save(self,doc,book,operation):
        validate(self.service,doc,book)
        # Appending a separate workspace record is the only write. No migration.
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');rows=c.execute("SELECT payload FROM records WHERE kind='source_reading' AND document_version_id=?",(doc['document_version_id'],)).fetchall()
            revision=max((json.loads(r[0])['source_reading_version'] for r in rows),default=0)+1
            item={**deepcopy(book),'id':uuid.uuid4().hex,'kind':'source_reading','created_at':now(),'source_reading_version':revision,'operation':operation,'parent_snapshot_id':book.get('id') or book.get('parent_snapshot_id')}
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(item['id'],'source_reading',doc['document_version_id'],None,item['created_at'],json.dumps(item,ensure_ascii=False)))
        return item
    def translate(self,doc,book,unit_id,provider,translation_service,client,glossary=None,approved_plan=None):
        p=plan(book,unit_id,provider,glossary)
        if approved_plan!=p['plan_id']:raise CopilotError('translation_plan_changed','请先查看原文翻译计划并明确执行；计划已变化时需重新查看。','authorization')
        if provider=='argos':return self.translate_local(doc,book,translation_service,client,glossary,unit_id=unit_id)
        if provider in ('aliyun','google','baidu','deepl','azure'):
            unit=next(u for value in book['fields'].values() for u in value['units'] if u['unit_id']==unit_id)
            result=translation_service.translate_passage(doc,unit,client,glossary);updated=deepcopy(book)
            for value in updated['fields'].values():
                for target in value['units']:
                    if target['unit_id']==unit_id:target['translation']=passage_payload(unit,result)
            return {'book':self.save(doc,updated,provider+'_chinese_reading'),'error':None,'new_requests':result['new_requests'],'requests_planned':p['requests']}
        results=[];error=None
        for chunk in p['chunks']:
            try:
                from .translation import selection
                anchor=selection(self.service,doc['document_version_id'],chunk['pdf_page'],chunk['quote'])
                # Explicit source offsets disambiguate repeated words/sentences.
                anchor.update(char_start=chunk['quote_char_start'],char_end=chunk['quote_char_end'],mapping_status='parser_text_matched')
                results.append(translation_service.translate(anchor,glossary,client))
            except CopilotError as exc:error=exc.as_dict();break
        updated=deepcopy(book)
        for value in updated['fields'].values():
            for u in value['units']:
                if u['unit_id']!=unit_id:continue
                complete=not error and len(results)==len(p['chunks'])
                u['translation']=translation_payload(u,p,results,error)
        saved=self.save(doc,updated,'translate_original_range')
        return {'book':saved,'error':error,'new_requests':sum(r.get('new_requests',0) for r in results),'requests_planned':p['requests']}

    def translate_local(self,doc,book,translation_service,client,glossary=None,field=None,unit_id=None):
        """One explicit/locally enabled operation; one appended snapshot, no cloud."""
        if getattr(client,'provider',None)!='argos' or not client.config_data.get('local'):
            raise CopilotError('local_translation_only','自动中文阅读只允许本机免费引擎。','authorization')
        validate(self.service,doc,book)
        with LOCK:return self._translate_local(doc,book,translation_service,client,glossary,field,unit_id)

    def _translate_local(self,doc,book,translation_service,client,glossary=None,field=None,unit_id=None):
        if field and field not in FIELDS:raise CopilotError('invalid_field','请选择阅读字段。','evidence')
        updated=deepcopy(book);seen=set();new_requests=0;error=None;changed=False
        for name,value in updated['fields'].items():
            if field and name!=field:continue
            for unit in value['units']:
                uid=unit['unit_id'];tr=unit.get('translation')
                if unit_id and uid!=unit_id:continue
                if tr and tr.get('status')=='complete' and not tr.get('is_placeholder') and tr.get('provider') not in ('mock','argos'):
                    seen.add(uid);continue
                if uid in seen or (tr and tr['status']=='complete' and tr.get('provider')=='argos' and tr.get('glossary_fingerprint')==fingerprint(glossary or {}) and tr.get('engine_revision')==client.config_data['config_revision']):continue
                seen.add(uid)
                try:
                    result=translation_service.translate_passage(doc,unit,client,glossary)
                    new_requests+=result.get('new_requests',0);payload=passage_payload(unit,result)
                except CopilotError as exc:
                    error=exc.as_dict();payload=translation_payload(unit,plan(updated,uid,'argos',glossary),[],error)
                payload['engine_revision']=client.config_data['config_revision'];changed=True
                for other in updated['fields'].values():
                    for target in other['units']:
                        if target['unit_id']==uid:target['translation']=deepcopy(payload)
                if error:break
            if error:break
        saved=self.save(doc,updated,'local_chinese_reading') if changed else book
        return {'book':saved,'error':error,'new_requests':new_requests,'local_only':True}

def passage_payload(unit,result):
    return {'text':result['translation'],'provider':result['provider'],'mode':'machine','status':'complete','is_placeholder':False,
        'translated_at':result.get('created_at'),'cache_hit':result.get('cache_hit',True),'translation_id':result['id'],'api':result.get('model_info',{}).get('api'),
        'parts':[{'source_ranges':unit['sources'],'translation_id':result['id'],'text':result['translation'],'cache_hit':result.get('cache_hit',True)}],
        'source_fingerprint':fingerprint(unit['original_text']),'glossary_fingerprint':fingerprint(result.get('glossary',{})),
        'engine_revision':result['model_info']['config_revision'],'human_review':'unreviewed','error':None,
        'numeric_preservation_warnings':result.get('numeric_preservation_warnings',[]),'math_layout_risk':result['anchor'].get('math_layout_risk',False)}

def translation_payload(unit,p,results,error):
    complete=not error and len(results)==len(p['chunks'])
    return {'text':'\n'.join(r['translation'] for r in results) if complete else None,'provider':p['provider'],'mode':'mock' if p['provider']=='mock' else 'machine',
        'is_placeholder':any(r.get('is_placeholder') or r['translation'].startswith('【Mock流程演示') for r in results),
        'status':'complete' if complete else 'partial' if results else 'unavailable',
        'parts':[{'source':chunk,'translation_id':r['id'],'text':r['translation'],'cache_hit':r.get('cache_hit',True)} for chunk,r in zip(p['chunks'],results)],
        'source_fingerprint':fingerprint(unit['original_text']),'glossary_fingerprint':p['glossary_fingerprint'],'human_review':'unreviewed','error':error,
        'numeric_preservation_warnings':list(dict.fromkeys(n for r in results for n in r.get('numeric_preservation_warnings',[]))),
        'math_layout_risk':any(r['anchor'].get('math_layout_risk') for r in results)}
