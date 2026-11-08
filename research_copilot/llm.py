"""Small real Chat Completions adapter using Python's HTTPS client.

No model/provider/key defaults, no SDK dependency, no automatic paid retry,
no credential-bearing redirects. A different protocol can implement complete_json.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import socket
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .errors import CopilotError

PREFIX = "RESEARCH_COPILOT_LLM_"


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout: int = 45
    max_output_tokens: int = 2000
    json_mode: bool = False
    token_limit_field: str = "max_tokens"
    provider: str = "openai_compatible"
    protocol: str = "chat_completions"

    @classmethod
    def from_environment(cls, root=None):
        values = {}
        path = Path(root or Path(__file__).resolve().parents[1]) / ".env"
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if sep:
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    values[key] = value  # Plain text; never eval/expand shell commands.
        values.update({k:v for k,v in os.environ.items() if k.startswith(PREFIX)})
        alias = values.get(PREFIX+"API_KEY_ENV", "")
        key_value = values.get(PREFIX+"API_KEY", "")
        if alias:
            import re
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*",alias):
                raise CopilotError("invalid_llm_config","API_KEY_ENV需为环境变量名称。","configuration")
            key_value = os.environ.get(alias) or values.get(alias, "")
        required = ["BASE_URL", "MODEL"]
        missing = [k for k in required if not values.get(PREFIX+k, "").strip()]
        if not key_value.strip():
            missing.append(alias or "API_KEY")
        if missing:
            raise CopilotError("llm_not_configured", "尚未配置模型调用所需的 " + "、".join(missing) + "；请在本机.env中配置，勿在聊天中发送密钥。", "configuration")
        try:
            config = cls(values[PREFIX+"BASE_URL"].strip().rstrip("/"), values[PREFIX+"MODEL"].strip(),
                         key_value.strip(), int(values.get(PREFIX+"TIMEOUT",45)),
                         int(values.get(PREFIX+"MAX_OUTPUT_TOKENS",2000)),
                         values.get(PREFIX+"JSON_MODE","false").lower() == "true",
                         values.get(PREFIX+"TOKEN_LIMIT_FIELD","max_tokens"),
                         values.get(PREFIX+"PROVIDER","openai_compatible"))
        except (ValueError, TypeError) as exc:
            raise CopilotError("invalid_llm_config", "模型超时／token参数格式无效。", "configuration") from exc
        config.validate()
        return config

    def validate(self):
        parsed = urlsplit(self.base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in ("localhost","127.0.0.1","::1")
        if (not parsed.hostname or not (parsed.scheme == "https" or local_http) or parsed.username or parsed.password
                or parsed.query or parsed.fragment or self.base_url.endswith("/chat/completions")):
            raise CopilotError("invalid_llm_endpoint", "BASE_URL需为HTTPS API根地址（不含/chat/completions、账号、查询参数）；本地测试可用回环HTTP。", "configuration")
        if not self.model or not self.api_key or not 5 <= self.timeout <= 60 or not 200 <= self.max_output_tokens <= 4000:
            raise CopilotError("invalid_llm_config", "请配置模型与密钥；timeout范围5—60秒，输出token上限200—4000。", "configuration")
        if self.token_limit_field not in ("max_tokens","max_completion_tokens"):
            raise CopilotError("invalid_llm_config", "token字段仅支持max_tokens或max_completion_tokens。", "configuration")

    def public_info(self):
        return {"provider":self.provider,"model":self.model,"endpoint_host":urlsplit(self.base_url).hostname,
                "timeout_seconds":self.timeout,"max_output_tokens":self.max_output_tokens,
                "json_mode":self.json_mode,"protocol":self.protocol}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsClient:
    def __init__(self, config: LLMConfig, budget=None, *, response_observer=None):
        config.validate()
        self.config = config
        self.calls = []
        self.budget = budget
        self.response_observer = response_observer

    def extra_payload(self):
        return {}

    def wire_request(self, system, user):
        payload = {"model":self.config.model,"messages":[{"role":"system","content":system},{"role":"user","content":json.dumps(user,ensure_ascii=False)}],
                   "stream":False,self.config.token_limit_field:self.config.max_output_tokens}
        if self.config.json_mode:
            payload["response_format"] = {"type":"json_object"}
        payload.update(self.extra_payload())
        return self.config.base_url+"/chat/completions", payload, {"Authorization":"Bearer "+self.config.api_key}

    def normalize_response(self, obj):
        return obj

    def complete_json(self, stage, system, user):
        started = perf_counter()
        trace = {"stage":stage,"model":self.config.model,"status":"not_sent"}
        self.calls.append(trace)  # Count attempted HTTP requests, including failures.
        url, payload, headers = self.wire_request(system, user)
        body = json.dumps(payload,ensure_ascii=False).encode("utf-8")
        if self.budget is None:
            trace["status"] = "not_sent"
            raise CopilotError("budget_required","未设置累计调用预算，未发送请求。", "budget")
        trace["budget_reservation"] = self.budget.reserve(len(body),self.config.max_output_tokens)
        trace["status"] = "started"
        request = Request(url,data=body,
                          headers={**headers,"Content-Type":"application/json","Accept":"application/json"},method="POST")
        try:
            with build_opener(NoRedirect).open(request,timeout=self.config.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise CopilotError("model_response_too_large","模型返回内容超过安全读取上限。",stage)
                trace["provider_request_id"] = (response.headers.get("x-request-id") or "")[:150]
            if self.response_observer is not None:
                # Body only, never request Authorization headers. Validation artifacts stay private.
                self.response_observer(stage, raw)
            obj = self.normalize_response(json.loads(raw))
            trace["response_model"] = str(obj.get("model",""))[:150]
            usage = obj.get("usage") or {}
            if not isinstance(usage,dict):
                raise ValueError("Invalid usage")
            trace["usage"] = {k:v for k,v in usage.items() if isinstance(v,(int,float))}
            trace["estimated_peak_cost_cny"] = self.budget.estimate(usage)
            choice = obj["choices"][0]
            if choice.get("finish_reason") not in ("stop",None):
                raise CopilotError("model_output_incomplete","模型输出未正常结束，可能达到输出上限或被供应商拒绝；未发布答案。",stage,True)
            content = choice["message"]["content"]
            if not isinstance(content,str):
                raise ValueError("Expected text content")
            # Accommodate fenced JSON when JSON mode is not supported; never execute it.
            content = content.strip()
            if content.startswith("```json\n") and content.endswith("```"):
                content = content[8:-3].strip()
            result = json.loads(content)
            if not isinstance(result,dict):
                raise ValueError("Expected JSON object")
            trace["status"] = "completed"
            return result
        except HTTPError as exc:
            status = exc.code
            if self.response_observer is not None:
                self.response_observer(stage, exc.read(2_000_001))
            exc.close()
            trace["http_status"] = status
            if status in (401,403):
                code,message,retry = "model_auth_failed","模型鉴权或访问权限失败，请在本机核对密钥和模型权限。",False
            elif status == 429:
                code,message,retry = "model_rate_limited","模型限流或额度不足，请检查账户后手动重试。",True
            elif status >= 500 or status in (408,409):
                code,message,retry = "model_service_unavailable","模型服务暂不可用，请稍后手动重试。",True
            else:
                code,message,retry = "model_request_rejected",f"供应商拒绝请求（HTTP {status}），请核对模型、JSON模式和token字段配置。",False
            # Do not expose response bodies, request headers, keys or credential URLs.
            raise CopilotError(code,message,stage,retry) from None
        except (TimeoutError,socket.timeout):
            raise CopilotError("model_timeout","模型请求超时，请手动重试。",stage,True) from None
        except URLError as exc:
            code = "model_timeout" if isinstance(exc.reason,(TimeoutError,socket.timeout)) else "model_network_error"
            raise CopilotError(code,"无法完成模型连接，请检查网络或稍后重试。",stage,True) from None
        except (json.JSONDecodeError,KeyError,IndexError,TypeError,ValueError,UnicodeDecodeError):
            raise CopilotError("invalid_model_output","模型返回格式不符合JSON合同，未发布答案。",stage,True) from None
        finally:
            trace["elapsed_seconds"] = round(perf_counter()-started,4)
            if trace["status"] == "started":
                trace["status"] = "failed"


class DeepSeekClient(ChatCompletionsClient):
    def extra_payload(self):
        # Provider-specific details stay out of research prompts and RAG logic.
        return {"thinking":{"type":"disabled"}}


def create_client(config, repository, *, validation_profile=None, response_observer=None):
    from .llm_budget import DeepSeekValidationBudget
    if config.provider != "deepseek":
        raise CopilotError("provider_not_configured","本轮只配置了DeepSeek适配；其他供应商可新增适配器与预算配置，无需修改RAG。","configuration")
    if config.model != "deepseek-flash" or config.base_url != "https://api.deepseek.com":
        raise CopilotError("budget_profile_mismatch","本轮价格与授权仅覆盖api.deepseek.com的deepseek-flash，未发送请求。","budget")
    if validation_profile is None:
        budget = DeepSeekValidationBudget(repository)  # Original 30-call protection stays the default.
    elif validation_profile == "stage4_3_p07_11calls_4cny":
        from .reading_validation_budget import Stage43ReadingBudget
        budget = Stage43ReadingBudget(repository)
    elif validation_profile == "stage4_5_p05_e2e_17calls_5cny":
        from .validation_session import Stage45Budget
        budget = Stage45Budget()
    elif validation_profile == "stage4_5_2_p05_retest_11calls_4cny":
        from .retest_session import RetestBudget
        budget = RetestBudget()
    else:
        raise CopilotError("validation_policy_mismatch","未知授权额度，未发送。","budget")
    return DeepSeekClient(config,budget,response_observer=response_observer)
