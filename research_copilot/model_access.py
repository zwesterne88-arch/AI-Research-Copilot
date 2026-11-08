"""Shared source authorization and existing LLM adapter factory, for QA and reading."""
from .errors import CopilotError
from .llm import LLMConfig, create_client


def prepare_client(service, doc, *, allow_remote, allowed_hashes, allowed_provider, client=None):
    if not allow_remote:
        raise CopilotError("source_consent_required", "请先确认可将当前论文必要片段发送给已配置供应商。", "authorization")
    if doc["file_sha256"] not in allowed_hashes:
        raise CopilotError("source_not_authorized", "当前文件不在获准发送清单中，未发送原文。", "authorization")
    service.search(doc["paper_id"], "research", document_version_id=doc["document_version_id"], top_k=1)
    if client is None:
        config = LLMConfig.from_environment()
        if config.provider != allowed_provider:
            raise CopilotError("source_provider_not_authorized", "当前供应商不在资料处理授权范围。", "authorization")
        from .validation_session import applies, client_for_document
        from . import retest_session
        if retest_session.applies(doc):
            client = retest_session.client_for_document(config, service.db, doc)
        else:
            client = client_for_document(config, service.db, doc) if applies(doc) else create_client(config, service.db)
    elif not getattr(client, "is_test_double", False) and getattr(getattr(client, "config", None), "provider", None) != allowed_provider:
        raise CopilotError("source_provider_not_authorized", "当前模型客户端不符合资料处理授权。", "authorization")
    return client
