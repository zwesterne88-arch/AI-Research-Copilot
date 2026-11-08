"""Free-form single-paper QA. No field/answer rules tied to a research discipline."""
import json
from pathlib import Path
import re
import unicodedata
import uuid
from decimal import Decimal

from .errors import CopilotError, NumericEvidenceError
from .llm import LLMConfig, create_client
from .qa_history import QAHistory
from .qa_retrieval import RETRIEVAL_VERSION, retrieve_context
from .evidence import locate_quote, validate_source_locations, support_payload, SUPPORT_LEVELS
from .model_access import prepare_client
from .date_evidence import numeric_check_text
from . import qa_prompts as prompts
from .repository import now
from .files import writer_lock

ROOT = Path(__file__).resolve().parents[1]


def authorized_hashes():
    from .validation_session import approved_hashes
    path = ROOT/"config/authorized_sources.json"
    if not path.is_file():
        return approved_hashes()
    return {s["file_sha256"] for s in json.loads(path.read_text(encoding="utf-8"))["sources"]} | approved_hashes()


def normalized(text):
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",text)).strip()


def require(condition, message="模型返回内容未通过结构／证据校验。", stage="evidence_validation"):
    if not condition:
        raise CopilotError("invalid_evidence_output",message,stage,True)


def model_sources(sources):
    # Keep immutable provenance server-side; only IDs/pages/passages go to the model.
    return [{k:s[k] for k in ("source_id","pdf_page","chunk_text")} for s in sources]


def numeric_values(text):
    # Exclude digits inside identifiers such as a method name; compare decimal values without rounding.
    return {Decimal(x) for x in re.findall(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?",normalized(text))}


def validate_answer(obj, sources):
    require(obj.get("status") in ("answered","partial","insufficient_evidence"))
    claims, unanswered = obj.get("claims"), obj.get("unanswered",[])
    require(isinstance(claims,list) and len(claims)<=8 and isinstance(unanswered,list) and len(unanswered)<=10)
    require(all(isinstance(x,str) and len(x)<=1000 for x in unanswered))
    by_id = {s["source_id"]:s for s in sources}
    verified = []
    for i,c in enumerate(claims,1):
        require(isinstance(c,dict) and isinstance(c.get("text"),str) and 1<=len(c["text"].strip())<=400)
        require(c.get("kind") in ("author_statement","evidence_summary","analysis"))
        refs = c.get("evidence")
        require(isinstance(refs,list) and 1<=len(refs)<=3)
        citations = []
        for ref in refs:
            require(isinstance(ref,dict) and ref.get("source_id") in by_id,"引用了不属于本次检索的来源，未发布答案。")
            source = by_id[ref["source_id"]]
            quote = ref.get("quote",source["chunk_text"])
            require(isinstance(quote,str) and 20<=len(quote)<=450,"原文引用长度或类型无效，未发布答案。")
            span = locate_quote(source["chunk_text"],quote)
            require(span is not None,"引用原句不能在指定片段中定位，未发布答案。")
            # Bind the exact raw substring even if a legacy candidate used normalized whitespace/ligatures.
            a,b = span
            citations.append({**{k:v for k,v in source.items() if k!="chunk_text"},"quote":source["chunk_text"][a:b],
                              "quote_char_start":source["char_start"]+a,"quote_char_end":source["char_start"]+b,
                              "quote_found":True,"locator_valid":True,"semantic_support":"automated_check_pending","review_status":"unreviewed"})
        numbers = numeric_values(numeric_check_text(c["text"],[x["quote"] for x in citations]))
        quoted_numbers = numeric_values(" ".join(x["quote"] for x in citations))
        if not numbers <= quoted_numbers:
            raise NumericEvidenceError()
        require(len({e["source_id"] for e in citations})==len(citations),"同一陈述重复引用了相同来源。")
        verified.append({"claim_id":f"C{i}","text":c["text"].strip(),"kind":c["kind"],"citations":citations})
    require(not (obj["status"] == "insufficient_evidence" and claims))
    require(obj["status"] == "insufficient_evidence" or bool(verified))
    # Callers append processing diagnostics to their result, never to the raw
    # model candidate. The previous shared list changed historical candidate_output.
    return verified,list(unanswered)


def apply_support_check(obj, claims, *, require_levels=False):
    reviews = obj.get("claim_reviews")
    require(isinstance(reviews,list),stage="support_verification")
    require(all(isinstance(r,dict) and isinstance(r.get("claim_id"),str) for r in reviews),stage="support_verification")
    ids = [r["claim_id"] for r in reviews]
    require(len(ids)==len(set(ids)) and set(ids)=={c["claim_id"] for c in claims},"模型支持检查未覆盖所有陈述，未发布答案。","support_verification")
    by_id = {r["claim_id"]:r for r in reviews}
    kept, reasons = [], []
    for claim in claims:
        review = by_id[claim["claim_id"]]
        require(review.get("verdict") in ("supported","unsupported","non_atomic")
                and isinstance(review.get("reason"),str) and 1<=len(review["reason"])<=1000,stage="support_verification")
        refs = review.get("citation_support")
        require(isinstance(refs,list) and all(isinstance(r,dict) and isinstance(r.get("source_id"),str)
                and isinstance(r.get("supports"),bool) for r in refs),stage="support_verification")
        ref_ids = [r["source_id"] for r in refs]
        require(len(ref_ids)==len(set(ref_ids)) and set(ref_ids)=={e["source_id"] for e in claim["citations"]},
                "支持检查没有逐一覆盖本陈述的引用，未发布答案。","support_verification")
        for ref in refs:
            level = ref.get("support_level")
            if require_levels or "support_level" in ref:
                require(isinstance(level,str) and level in SUPPORT_LEVELS,
                        "支持检查缺少合法的逐引用支持等级，未发布陈述。","support_verification")
                require(ref["supports"] == (level == "supported"),
                        "引用支持等级与布尔判断冲突，未发布陈述。","support_verification")
        # A non-atomic or partially supported statement is withheld as a whole. Never auto-fill facts.
        if review["verdict"] == "supported" and all(r["supports"] for r in refs):
            for ref in claim["citations"]:
                ref["semantic_support"] = "model_supported_not_human_verified"
                graded = next(r for r in refs if r["source_id"] == ref["source_id"])
                if "support_level" in graded:
                    ref["model_support_level"] = graded["support_level"]
            claim["coverage_review"] = "model_checked_atomic_not_human_verified"
            kept.append(claim)
        else:
            reasons.append(f"{claim['claim_id']} 未发布：{review['reason']}")
    return kept,reasons


class PaperQA:
    def __init__(self, service, allowed_hashes=None):
        self.service = service
        self.history = QAHistory(service.db)
        self.allowed_hashes = authorized_hashes() if allowed_hashes is None else set(allowed_hashes)
        consent_path = ROOT/"config/authorized_sources.json"
        self.allowed_provider = json.loads(consent_path.read_text(encoding="utf-8")).get("provider") if consent_path.is_file() else None

    def answer_question(self,paper_id,question,*,document_version_id,allow_remote=False,client=None,progress=None,resume_candidate_run_id=None,audit_unanswered=False):
        try:
            with writer_lock(self.service.root):
                return self._answer_question(paper_id,question,document_version_id=document_version_id,allow_remote=allow_remote,
                    client=client,progress=progress,resume_candidate_run_id=resume_candidate_run_id,audit_unanswered=audit_unanswered)
        except CopilotError as exc:
            if exc.code=="busy":
                raise CopilotError("qa_busy","本地有问答、精读或文件处理进行中；本次未重复提交模型请求，请稍后再试。","qa_inflight",True) from exc
            raise

    def _answer_question(self,paper_id,question,*,document_version_id,allow_remote=False,client=None,progress=None,resume_candidate_run_id=None,audit_unanswered=False):
        if not isinstance(question,str) or not 1<=len(question.strip())<=2000:
            raise CopilotError("invalid_question","请输入1—2000字符的问题。","question")
        doc = self.service.get_document(document_version_id)
        if doc["paper_id"] != paper_id:
            raise CopilotError("scope_not_found","文件版本不属于指定论文。","selection")
        result = {"run_id":"qa_"+uuid.uuid4().hex,"paper_id":paper_id,"document_version_id":document_version_id,
                  "file_sha256":doc["file_sha256"],"source_file":doc["source_file"],"question":question.strip(),"created_at":now(),
                  "status":"processing","stage":"configuration","prompt_version":prompts.PROMPT_VERSION,
                  "planner_version":prompts.PLANNER_VERSION,
                  "retrieval_version":RETRIEVAL_VERSION,"claims":[],"unanswered":[],"sources":[],"model_calls":0,
                  "error":None,"human_review":"not_performed"}
        start_calls = len(client.calls) if client is not None else 0

        def stage(name):
            result["stage"] = name
            self.history.save(result)
            if progress:
                progress(name)

        try:
            self.history.save(result)
            client = prepare_client(self.service,doc,allow_remote=allow_remote,allowed_hashes=self.allowed_hashes,
                                    allowed_provider=self.allowed_provider,client=client)
            if audit_unanswered and not (getattr(client,"is_test_double",False) or getattr(client,"allow_scope_audit",False)):
                raise CopilotError("scope_audit_not_authorized", "独立范围复核需要明确的新调用授权；原额度不自动增加请求。", "authorization")
            if getattr(client,"config",None) is not None:
                result["model_info"] = client.config.public_info()
            result["execution_mode"] = "test_double" if getattr(client,"is_test_double",False) else "real_api"
            previous = self.history.get(resume_candidate_run_id) if resume_candidate_run_id else None
            if previous:
                require(previous["paper_id"]==paper_id and previous["document_version_id"]==document_version_id
                        and previous["question"]==question.strip() and previous["status"]=="failed"
                        and previous.get("candidate_output") is not None and previous["prompt_version"]==prompts.PROMPT_VERSION
                        and previous.get("execution_mode")==result["execution_mode"]
                        and previous.get("model_info")==result.get("model_info"),
                        "待恢复候选的论文、问题、模型或版本不匹配，未发送请求。","candidate_resume")
            stage("query_planning")
            preview = self.service.db.one("SELECT chunk_text FROM chunks WHERE paper_id=? AND document_version_id=? ORDER BY pdf_page,char_start LIMIT 1",(paper_id,document_version_id))
            cached = None if getattr(client,"is_test_double",False) else self.history.cached_plan(doc,question,client.config.model,prompts.PLANNER_VERSION)
            result["query_plan_cache"] = {"hit":bool(cached),"source_run_id":cached["source_run_id"] if cached else None}
            plan = cached or client.complete_json("query_planning",prompts.PLANNER,{"question":question,"paper_preview":preview["chunk_text"][:1200] if preview else ""})
            queries = plan.get("queries_en")
            require(isinstance(queries,list) and 1<=len(queries)<=3 and all(isinstance(q,str) and 1<=len(q)<=160 and re.search(r"[a-zA-Z]",q) for q in queries),"检索改写未返回有效的论文语言检索表达。","query_planning")
            result["retrieval_queries"] = queries
            stage("retrieving")
            result["retrieval_diagnostics"] = {}
            sources = retrieve_context(self.service,doc,queries,question=question,diagnostics=result["retrieval_diagnostics"])
            validate_source_locations(self.service,doc,sources)
            result["sources"] = sources
            result["checked_scope"] = {"source_ids":[s["source_id"] for s in sources],"pdf_pages":sorted({s["pdf_page"] for s in sources}),
                                       "parse_status":doc["parse_status"],"exhaustive_full_text_review":False}
            if not sources:
                result.update(status="insufficient_evidence",unanswered=["当前可用索引中没有足够来源片段；不能据此认定全文未提及。"])
                result["evidence_assessment"] = "retrieval_empty"
            else:
                stage("answering")
                if previous:
                    require(previous["sources"]==sources,"来源片段已变化，不能恢复旧候选。","candidate_resume")
                    generated=previous["candidate_output"]
                    result["resumed_candidate_run_id"]=resume_candidate_run_id
                else:
                    generated = client.complete_json("answering",prompts.ANSWER,{"question":question,"sources":model_sources(sources)})
                result["candidate_output"] = generated  # Private diagnostic data; never a published answer.
                stage("evidence_validation")
                claims,unanswered = validate_answer(generated,sources)
                if claims:
                    stage("support_verification")
                    from .scope_review import add_scope_context, interpret_scope_reviews
                    from .support_policy import adjudicate_support, POLICY_VERSION
                    checked = client.complete_json("support_verification",prompts.VERIFY,support_payload(question,claims))
                    result["support_output"] = checked
                    claims,reasons,decisions = adjudicate_support(checked,claims)
                    result.update(support_policy_version=POLICY_VERSION, support_decisions=decisions)
                    if audit_unanswered and unanswered:
                        scope_output = client.complete_json("scope_verification",prompts.SCOPE_VERIFY,add_scope_context({"question":question},unanswered,sources))
                        result["scope_review_output"] = scope_output
                    else:
                        scope_output = {}
                    result["scope_reviews"] = interpret_scope_reviews(scope_output, unanswered, sources)
                    unanswered.extend(reasons)
                    result["evidence_assessment"] = "some_candidates_withheld" if reasons else "cited_claims_model_checked"
                else:
                    result["evidence_assessment"] = "retrieved_evidence_insufficient"
                result["claims"],result["unanswered"] = claims,unanswered
                result["status"] = ("partial" if unanswered or generated["status"]=="partial" else "answered") if claims else "insufficient_evidence"
                if not claims and not unanswered:
                    result["unanswered"] = ["当前检索到的原文不足以确认该问题。"]
            # Do not publish a result if the selected source disappeared/changed in-flight.
            self.service.search(paper_id,"research",document_version_id=document_version_id,top_k=1)
            result["stage"] = "completed"
        except CopilotError as exc:
            result.update(status="failed",stage=exc.stage,error=exc.as_dict(),claims=[])
        except Exception as exc:
            result.update(status="failed",error={"code":"qa_internal_error","stage":result["stage"],"message":f"问答未完成（{type(exc).__name__}），未发布答案。","retryable":True},claims=[])
        finally:
            calls = client.calls[start_calls:] if client is not None else []
            result["request_traces"] = calls
            result["model_calls"] = 0 if getattr(client,"is_test_double",False) else sum(c.get("status")!="not_sent" for c in calls)
            result["test_double_calls"] = len(calls) if getattr(client,"is_test_double",False) else 0
            result["updated_at"] = now()
            self.history.save(result)
        return result
