"""Section/list navigation over immutable parser offsets; never certifies semantics."""
import re
from .evidence import nfkc_offsets,validate_source_locations

VERSION='section-aware-research-logic-v1'
HEADING=re.compile(r'(?im)^\s*(?:(?:\d+(?:\.\d+)*|[IVX]+)[.)]?\s+)?(?P<title>Introduction|Background|Related\s+work[s]?|Problem\s+(?:formulation|statement)|Research\s+objectives?|Contributions?|Method(?:ology|s)?|Conclusion[s]?|References|引言|绪论|研究背景|相关工作|问题定义|研究目标|研究贡献|主要贡献|结论|参考文献|方法)\s*$')
ABSTRACT=re.compile(r'(?i)\bA\s*B\s*S\s*T\s*R\s*A\s*C\s*T\b|摘要')
LEAD=re.compile(r'(?i)(?:(?:main\s+)?contributions?[^:.]{0,120}(?:as follows|threefold|summari[sz]ed|are\s*:)|(?:our|the)\s+contributions?\s*(?:are|:)|主要贡献[^\n]{0,40}[：:])')
BULLET=re.compile(r'(?m)(?:^[ \t]*|(?<=\s))(?P<mark>[•∙●▪]|\([1-9]\d?\)|[1-9]\d?[.)]|(?:First|Second|Third|Finally)[,:]|(?:首先|其次|再次|最后)[，、：:]|[（(][一二三四五六七八九][）)])\s*',re.I)
ROADMAP=re.compile(r'(?im)^\s*(?:The rest|The remainder|This paper is organi[sz]ed|本文其余|文章其余)\b')


def structure(service,doc):
    pages=service.get_pages(doc['document_version_id']);joined='';locations=[]
    for page in pages:
        start=len(joined);joined+=page['raw_text']+'\n';locations.append((page['pdf_page'],start,start+len(page['raw_text'])))
    canonical,mapping=nfkc_offsets(joined)
    aliases={'引言':'Introduction','绪论':'Introduction','研究背景':'Background','相关工作':'Related work','问题定义':'Problem statement','研究目标':'Research objectives','研究贡献':'Contributions','主要贡献':'Contributions','结论':'Conclusion','参考文献':'References','方法':'Methods'}
    headings=[(mapping[m.start()][0],mapping[m.end()-1][1],aliases.get(m.group('title'),m.group('title'))) for m in HEADING.finditer(canonical)]
    def spans(a,b):return [{'pdf_page':p,'char_start':max(a,left)-left,'char_end':min(b,right)-left} for p,left,right in locations if max(a,left)<min(b,right)]
    sections=[]
    for i,(a,b,title) in enumerate(headings):
        end=headings[i+1][0] if i+1<len(headings) else len(joined)
        sections.append({'name':title,'start':a,'content_start':b,'end':end,'spans':spans(b,end)})
    abstract=ABSTRACT.search(canonical)
    if abstract:
        a=mapping[abstract.end()-1][1];end=next((x[0] for x in headings if x[0]>a),min(len(joined),a+6000))
        # Some two-column publishers place keywords between the abstract header
        # and its paragraph. Keep the exact first prose line, not metadata.
        prose=re.search(r'(?m)^.{80,}$',joined[a:end])
        if prose:a+=prose.start()
        sections.insert(0,{'name':'Abstract','start':a,'end':end,'spans':spans(a,end)})
    intro=next((s for s in sections if s['name'].lower()=='introduction'),None)
    lists=[]
    # Look first in Introduction; allow an explicitly signposted list elsewhere.
    scopes=([intro] if intro else [])+[s for s in sections if s is not intro and s['name']!='Abstract']
    for section in scopes:
        region=joined[section['start']:section['end']];canon,mp=nfkc_offsets(region)
        leads=[m.end() for m in LEAD.finditer(canon)]
        if section['name'].lower().startswith('contribution'):leads.insert(0,-1)
        for lead_end in leads:
            a=mp[lead_end-1][1] if lead_end>=0 else section['content_start']-section['start'];tail=region[a:];stop=ROADMAP.search(tail)
            if stop:tail=tail[:stop.start()]
            matches=list(BULLET.finditer(tail))
            if not matches:continue
            group=[]
            for i,m in enumerate(matches[:12]):
                left=m.end();right=matches[i+1].start() if i+1<len(matches) else len(tail)
                text=tail[left:right].strip()
                if not text:continue
                # Final item must not swallow unrelated unnumbered paragraphs.
                boundary=re.search(r'\n\s*\n',text)
                if boundary:text=text[:boundary.start()].rstrip()
                left=tail.index(text,left);absolute=section['start']+a+left
                group.append({'ordinal':i+1,'author_marker':m.group('mark'),'author_number':re.sub(r'\D','',m.group('mark')) or None,
                    'original_text':text,'section':section['name'],'spans':spans(absolute,absolute+len(text)),
                    'source_type':'A_author_enumerated','extraction_status':'verbatim_list_candidate','human_review':'unreviewed','chinese_summary':None})
            if group:lists=group;break
        if lists:break
    return {'strategy':VERSION,'sections':sections,'contributions':lists,'parser_order':'original_parser_order_not_geometric_reflow',
            'notice':'章节和列表为定位候选；原文定位、语义支持和人工确认独立。'}


def span_sources(service,doc,spans,section,limit=1000):
    """Cover spans with existing chunk boundaries; no forged cross-chunk refs."""
    chunks=service.db.rows('SELECT * FROM chunks WHERE document_version_id=? ORDER BY pdf_page,char_start',(doc['document_version_id'],))
    pages={p['pdf_page']:p['raw_text'] for p in service.get_pages(doc['document_version_id'])};result=[]
    keys=('chunk_id','paper_id','document_version_id','file_sha256','pdf_page','parser_version','chunker_version','index_version')
    for span in spans:
        pos=span['char_start']
        while pos<span['char_end']:
            covering=[c for c in chunks if c['pdf_page']==span['pdf_page'] and c['char_start']<=pos<c['char_end']]
            if not covering:break
            c=max(covering,key=lambda c:c['char_end']);end=min(c['char_end'],span['char_end'],pos+limit)
            raw=pages[span['pdf_page']][pos:end];a=pos+len(raw)-len(raw.lstrip());b=pos+len(raw.rstrip())
            if b>a:result.append({**{k:c[k] for k in keys},'char_start':a,'char_end':b,'chunk_text':pages[span['pdf_page']][a:b],'quote':pages[span['pdf_page']][a:b],'quote_char_start':a,'quote_char_end':b,
                'section_hint':section,'retrieval_reason':'section_priority','boundary_may_cut_sentence':end<span['char_end']})
            pos=end
    return result


def focus_sources(service,doc,field,section_filter=None):
    info=structure(service,doc)
    if field=='claimed_contributions' and info['contributions']:
        sources=[source for item in info['contributions'] if not section_filter or item['section'].lower().startswith(section_filter.lower()) for source in span_sources(service,doc,item['spans'],item['section'])]
    else:
        preferences={'research_questions':['Abstract','Introduction','Problem','Research'],
                     'background':['Introduction','Abstract'],'prior_work_gaps':['Introduction','Related','Abstract'],
                     'motivation':['Introduction','Abstract','Related']}.get(field,['Introduction','Abstract'])
        sections=[]
        for name in preferences:
            sections.extend(s for s in info['sections'] if s['name'].lower().startswith(name.lower()))
        if section_filter:sections=[s for s in sections if s['name'].lower().startswith(section_filter.lower())]
        sources=[]
        for s in sections:
            pieces=span_sources(service,doc,s['spans'],s['name'])
            # Keep whole abstract, opening and later intent transitions. Headings
            # reserve coverage; cue scores only order passages, not certify intent.
            if s['name']=='Abstract':chosen=pieces[:4]
            else:
                score=lambda x:len(re.findall(r'however|although|challenge|limited|problem|therefore|address|objective|aim|need|propos|研究|不足|目的',x['chunk_text'],re.I))
                chosen=sorted(pieces,key=lambda x:-score(x))[:5]
            sources.extend(chosen)
    out=[];seen=set()
    for s in sources:
        key=(s['pdf_page'],s['char_start'],s['char_end'])
        if key in seen:continue
        seen.add(key);out.append({**s,'source_id':'E'+str(len(out)+1),'candidate_status':'unreviewed_relevance_not_fact_support'})
        if len(out)>=12:break
    validate_source_locations(service,doc,out)
    return out


def reading_sources(service,doc,module,queries,question,diagnostics,retriever=None):
    from .qa_retrieval import retrieve_context
    existing=(retriever or retrieve_context)(service,doc,queries,question=question,diagnostics=diagnostics)
    validate_source_locations(service,doc,existing)
    if module not in ('framing','approach'):return existing
    fields=['research_questions','motivation'] if module=='framing' else ['claimed_contributions']
    priority=[s for field in fields for s in focus_sources(service,doc,field)]
    # Retain lexical method/mechanism hits before generic closing windows.
    # The unchanged QA retriever keeps its historical ordering contract.
    existing=sorted(existing,key=lambda s:s.get('retrieval_reason')=='section_context')
    sources=[];seen=set();total=0
    for s in priority+existing:
        key=(s['pdf_page'],s['char_start'],s['char_end'])
        if key in seen or total+len(s['chunk_text'])>12000:continue
        if len(sources)>=40:break
        seen.add(key);total+=len(s['chunk_text']);sources.append({**s,'source_id':'E'+str(len(sources)+1)})
    diagnostics.update(structure_strategy=VERSION,section_reserved_sources=len([s for s in sources if s.get('retrieval_reason')=='section_priority']),context_characters=total)
    validate_source_locations(service,doc,sources)
    return sources


def research_preview(service,doc):
    info=structure(service,doc)
    for item in info['contributions']:
        item['sources']=span_sources(service,doc,item['spans'],item['section'])
    return {**info,'document_version_id':doc['document_version_id'],'file_sha256':doc['file_sha256'],
        'fields':{field:focus_sources(service,doc,field) for field in ('research_questions','background','prior_work_gaps','motivation')},
        'model_extraction':'not_run','semantic_support':'unreviewed','human_review':'unreviewed'}
