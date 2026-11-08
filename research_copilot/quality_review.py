"""Presentation of fixed 5.1 tasks. No suggested labels and no model calls."""
from .errors import CopilotError

BATCH_ID = 'quality-first10-20261030-v1'
BATCH = [
    ('H009', 'P07 v8 · 研究动机', '区分背景、已有不足和研究动机，检查已保存内容及原文范围。'),
    ('H010', 'P07 v8 · 核心研究问题', '核查缺失字段；没有问句不代表没有研究目标。'),
    ('H015', 'P05 v5 · 研究动机', '检查空字段与原文目标章节，保留漏检案例。'),
    ('H016', 'P05 v5 · 核心研究问题', '检查空字段；若判缺失，请添加原文依据。'),
    ('H019', 'P05 v5 · 贡献主张与方法关系', '核查贡献主张、方法机制和二者关系；只给既定关系单元打标签。'),
    ('H011', 'P07 v8 · 方法机制完整性', '检查方法组成、机制与作用范围，不能只核查术语是否出现。'),
    ('H017', 'P05 v5 · 方法陈述引用支持', '核查RSA、IPFS、智能合约、存储与解密各子陈述的证据。'),
    ('H012', 'P07 v8 · 实验结果归属', '逐项核查数据集、攻击类别、基线和指标差值。'),
    ('H013', 'P07 v8 · 验证结果归属', '核查96.0%与91.2%的数据集及平均指标归属。'),
    ('H018', 'P05 v5 · 原始验证候选归属', '核查原始提取候选及验证单元；原始候选不是已发布事实。'),
]


def task_material(task, record):
    """Return only historical statements and sources; never assistant judgments."""
    content = record.get('structured_content', {})
    facts = content.get('facts', {})
    unit = task['unit']
    result = {'statements': [], 'context_statements': [], 'sources': [], 'field_state': None}
    def add(item, context=False):
        result['context_statements' if context else 'statements'].append({
            'text': item['text'], 'kind': item.get('kind'), 'validation_unit_id': item.get('validation_unit_id')})
        for eid in item.get('evidence_ids', []):
            ref = record['evidence_references'][eid]
            if ref not in result['sources']:result['sources'].append(ref)
    if unit in facts:
        add(facts[unit])
    elif unit in content.get('fields', {}):
        value = content['fields'][unit]
        result['field_state'] = {k: value.get(k) for k in ('status', 'reason_code')}
        for fid in value['fact_ids']:add(facts[fid])
        # A missing field has no published citation. Show the ORIGINAL checked
        # passages as review context, never as pre-certified relevant targets.
        checked = set(value.get('checked_source_ids', []))
        for module, data in record.get('modules', {}).items():
            for source in data.get('sources', []):
                if module+':'+source['source_id'] in checked:
                    result['sources'].append({**source, 'quote': source['chunk_text'], 'review_context_only': True})
    elif unit == 'raw_validation_candidate':
        raw = record['raw_outputs']['validation:extract']
        for item in raw['facts']:
            result['statements'].append({'text':item['text'], 'kind':'raw_unpublished_candidate', 'validation_unit_id':item.get('validation_unit_id')})
        wanted = {e['source_id'] for f in raw['facts'] for e in f.get('evidence', [])}
        for source in record['modules']['validation']['sources']:
            if source['source_id'] in wanted:
                result['sources'].append({**source, 'quote':source['chunk_text'], 'review_context_only':True})
        result['raw_candidate'] = True
    else:
        edge = next((e for e in content.get('relations', []) if e['relation_id']==unit), None)
        if edge:
            add(edge)
            for key in ('from_fact_id','to_fact_id'):
                if edge[key] in facts:add(facts[edge[key]], True)
    return result


def validate_targets(service, record, targets):
    if not isinstance(targets,list) or len(targets)>20:
        raise CopilotError('review_targets','核查依据需为最多20段原文。','annotation')
    if not targets:return []
    version = record.get('document_version_id')
    if not version:raise CopilotError('review_scope','该任务不接受单论文原文目标。','annotation')
    pages = service.get_pages(version)
    result=[]
    for t in targets:
        p,a,b = (t.get(k) for k in ('pdf_page','char_start','char_end'))
        if (t.get('document_version_id',version)!=version or
            not all(isinstance(x,int) and not isinstance(x,bool) for x in (p,a,b)) or
            not 1<=p<=len(pages) or not 0<=a<b<=len(pages[p-1]['raw_text']) or
            pages[p-1]['raw_text'][a:b]!=t.get('quote')):
            raise CopilotError('review_scope','核查依据的文件版本、页码或逐字原文不一致。','annotation')
        item={'document_version_id':version,'pdf_page':p,'char_start':a,'char_end':b,'quote':t['quote']}
        if item in result:raise CopilotError('review_targets','请勿重复加入同一原文目标。','annotation')
        result.append(item)
    return result
