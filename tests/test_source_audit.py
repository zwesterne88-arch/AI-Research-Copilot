"""Offline regressions for observed 4.3 citation and provenance failures; no API."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from research_copilot.errors import CopilotError
from research_copilot.rag import apply_support_check
from research_copilot.reading import StructuredReading
from research_copilot.reading_schema import validate_card
from research_copilot.source_audit import card_fingerprint, validate_source_audit
from scripts.reading_mock import ReadingDouble, SYNTHETIC_TEXT


def claim_and_review(level="supported", supports=True):
    claim={"claim_id":"C1","text":"Synthetic independent assertion.","kind":"author_statement",
           "citations":[{"source_id":"E1","quote":"Synthetic exact source passage.","semantic_support":"automated_check_pending","review_status":"unreviewed"}]}
    review={"claim_reviews":[{"claim_id":"C1","verdict":"supported","reason":"Synthetic review, not actual source assessment.",
              "citation_support":[{"source_id":"E1","supports":supports,"support_level":level}]}]}
    return [claim],review


@pytest.mark.parametrize("level",["partial","background_only","conflicting_or_unsupported","unverifiable"])
def test_adverse_level_cannot_be_true_even_if_overall_verdict_says_supported(level):
    # P07 E14 had true despite a reason saying only background. Now a structured
    # background grade cannot coexist with true. Free-text meaning is NOT inferred.
    claims,review=claim_and_review(level,True)
    with pytest.raises(CopilotError,match="布尔判断冲突"):
        apply_support_check(review,claims,require_levels=True)
    assert claims[0]["citations"][0]["semantic_support"]=="automated_check_pending"


@pytest.mark.parametrize("level",["partial","background_only","conflicting_or_unsupported","unverifiable"])
def test_non_direct_citation_withholds_claim_without_deleting_raw_candidate(level):
    claims,review=claim_and_review(level,False)
    before=deepcopy(claims)
    kept,reasons=apply_support_check(review,claims,require_levels=True)
    assert not kept and reasons and claims==before


def test_new_reading_requires_levels_legacy_qa_contract_remains_readable():
    claims,review=claim_and_review()
    del review["claim_reviews"][0]["citation_support"][0]["support_level"]
    with pytest.raises(CopilotError): apply_support_check(review,deepcopy(claims),require_levels=True)
    kept,_=apply_support_check(review,deepcopy(claims))
    assert kept[0]["citations"][0]["review_status"]=="unreviewed"
    assert kept[0]["citations"][0]["semantic_support"]=="model_supported_not_human_verified"


def test_compound_claim_cannot_borrow_coverage_from_other_citation():
    claims,review=claim_and_review()
    claims[0]["text"]="Synthetic assertion contains two independent facts."
    claims[0]["citations"].append({"source_id":"E2","quote":"Only background for the second fact."})
    review["claim_reviews"][0]["citation_support"].append({"source_id":"E2","supports":False,"support_level":"background_only"})
    kept,reasons=apply_support_check(review,claims,require_levels=True)
    assert not kept and reasons


def test_supported_grade_never_becomes_human_approval():
    claims,review=claim_and_review()
    kept,_=apply_support_check(review,claims,require_levels=True)
    ref=kept[0]["citations"][0]
    assert ref["model_support_level"]=="supported" and ref["semantic_support"]=="model_supported_not_human_verified"


@pytest.fixture
def small_audit():
    # Contract-only synthetic objects, not a paper or a saved application card.
    text="Synthetic source passage with an explicit method."
    ref={"paper_id":"synthetic-paper","document_version_id":"synthetic-v1","file_sha256":"a"*64,
         "pdf_page":1,"quote":text,"quote_char_start":0,"quote_char_end":len(text),"chunk_id":"synthetic-chunk"}
    card={"card_id":"synthetic-card","card_version":8,"paper_id":ref["paper_id"],"document_version_id":ref["document_version_id"],
          "file_sha256":ref["file_sha256"],"structured_content":{"facts":{"approach:f1":{"field":"method","text":"Synthetic method.","evidence_ids":["approach:f1:E1"]}},"relations":[]},
          "evidence_references":{"approach:f1:E1":ref},"raw_outputs":{"synthetic":"unchanged"}}
    audit={"schema_version":"source-audit-v1",**{k:card[k] for k in ("card_id","card_version","paper_id","document_version_id","file_sha256")},
       "card_fingerprint":card_fingerprint(card),"reviewer_type":"development_assistant","human_review":"not_performed",
       "items":[{"item_id":"approach:f1","research_module":"approach","current_field":"method","original_statement":"Synthetic method.",
                 "evidence_ids":["approach:f1:E1"],"support_status":"supported","user_review_status":"pending","comparison_candidate":{"layer1":"candidate","layer2":"exclude"}}],
       "citations":[{**ref,"evidence_id":"approach:f1:E1","item_id":"approach:f1","locatable":True,"support_status":"supported","user_review_status":"pending"}]}
    return audit,card,{1:text}


def test_audit_is_read_only_and_keeps_assistant_and_human_separate(small_audit):
    audit,card,pages=small_audit;before=deepcopy(card)
    assert validate_source_audit(audit,card,pages) and card==before
    assert audit["human_review"]=="not_performed"


@pytest.mark.parametrize("problem",["card","version","document","file","duplicate_item","missing_citation","quote","page","offset","owner","field","fake_human","promote_partial","raw_output"])
def test_stale_or_falsely_promoted_audit_is_rejected(small_audit,problem):
    a,c,p=small_audit
    if problem=="card": a['card_id']='another-card'
    elif problem=="version": a['card_version']=9
    elif problem=="document": a['document_version_id']='another-version'
    elif problem=="file": a['file_sha256']='b'*64
    elif problem=="duplicate_item": a['items'].append(deepcopy(a['items'][0]))
    elif problem=="missing_citation": a['citations']=[]
    elif problem=="quote": a['citations'][0]['quote']='A different passage.'
    elif problem=="page": a['citations'][0]['pdf_page']=2
    elif problem=="offset": a['citations'][0]['quote_char_start']=1
    elif problem=="owner": a['citations'][0]['item_id']='other:f1'
    elif problem=="field": a['items'][0]['current_field']='motivation'
    elif problem=="fake_human": a['human_review']='approved'
    elif problem=="promote_partial": a['items'][0]['support_status']='partial'
    elif problem=="raw_output": c['raw_outputs']['synthetic']='silently changed'
    with pytest.raises(CopilotError): validate_source_audit(a,c,p)


def test_unknown_semantic_status_is_valid_but_stays_pending(small_audit):
    a,c,p=small_audit
    a['items'][0]['support_status']='unverifiable';a['items'][0]['comparison_candidate']['layer1']='pending'
    a['citations'][0]['support_status']='unverifiable'
    assert validate_source_audit(a,c,p)


@pytest.mark.parametrize("malformation",["reversed","wrong_type"])
def test_saved_graph_direction_is_checked_on_read(service,pdf_bytes,malformation):
    d=service.import_pdf(pdf_bytes([SYNTHETIC_TEXT]),'synthetic.pdf')['data']
    card=StructuredReading(service,{d['file_sha256']}).generate(d['paper_id'],document_version_id=d['document_version_id'],allow_remote=True,client=ReadingDouble())
    ids=card['structured_content']['facts']
    contribution=next(i for i,f in ids.items() if f['field']=='claimed_contributions')
    method=next(i for i,f in ids.items() if f['field']=='method')
    edge={'relation_id':'synthetic-edge','from_fact_id':contribution,'to_fact_id':method,'type':'implemented_by',
          'evidence_ids':ids[method]['evidence_ids'],'review_status':'unreviewed'}
    card['structured_content']['relations']=[edge];assert validate_card(card)
    if malformation=='reversed': edge['from_fact_id'],edge['to_fact_id']=edge['to_fact_id'],edge['from_fact_id']
    else: edge['type']='motivates'
    with pytest.raises(CopilotError): validate_card(card)


def test_actual_p07_audit_binds_all_original_items_and_quotes_without_mutation():
    root=Path(__file__).resolve().parents[1];path=root/'verification/stage4_3/source_audit.json'
    if not path.exists() or not (root/'data/library.sqlite3').exists(): pytest.skip('Private P07 library and audit not available')
    a=json.loads(path.read_text(encoding='utf-8'))
    con=sqlite3.connect((root/'data/library.sqlite3').as_uri()+'?mode=ro',uri=True)
    row=con.execute('SELECT record_json FROM reading_cards WHERE card_id=?',(a['card_id'],)).fetchone()
    if not row: con.close();pytest.skip('Original private card not available')
    c=json.loads(row[0]);p=dict(con.execute('SELECT pdf_page,raw_text FROM pages WHERE document_version_id=?',(c['document_version_id'],)))
    before=deepcopy(c);assert validate_source_audit(a,c,p) and validate_card(c)
    assert len(a['items'])==25 and len(a['citations'])==33 and c==before
    background=next(e for e in a['citations'] if e['evidence_id']=='framing:f2:E14')
    assert background['locatable'] and background['support_status']=='background_only'
    assert all(e['user_review_status']=='pending' for e in a['citations'])
    assert con.execute('SELECT record_json FROM reading_cards WHERE card_id=?',(a['card_id'],)).fetchone()[0]==row[0]
    con.close()
