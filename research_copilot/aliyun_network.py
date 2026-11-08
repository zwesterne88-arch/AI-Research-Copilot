"""Transport diagnostics: no keys, source text or translation Action are sent."""
import re,socket,ssl
from .errors import CopilotError
from .repository import now

def transport_error(exc):
    """Unwrap Tea without exposing its signed last_request or raw message."""
    if isinstance(exc,CopilotError):return exc
    nodes=[];pending=[exc];seen=set()
    while pending and len(nodes)<12:
        node=pending.pop()
        if id(node) in seen:continue
        seen.add(id(node));nodes.append(node)
        for attr in ('inner_exception','__cause__','__context__','reason'):
            value=getattr(node,attr,None)
            if isinstance(value,BaseException):pending.append(value)
    # Inspect text only for known signatures; never return or log this text.
    text=' '.join(str(n) for n in nodes).lower();names={type(n).__name__ for n in nodes}
    if re.search(r'winerror\s*10013',text) or any(isinstance(n,PermissionError) for n in nodes):
        code='aliyun_network_permission';message='后台进程被系统限制联网（Windows 10013），尚未连接阿里云。请从正常终端启动工作台，检查连接后继续；原文和已有译文保留。'
    elif any(isinstance(n,ssl.SSLCertVerificationError) for n in nodes) or 'certificate_verify_failed' in text or 'certificate verify failed' in text:
        code='aliyun_tls';message='连接阿里云时TLS证书校验失败，请检查本机证书或代理；没有关闭证书验证。'
    elif 'ProxyError' in names or 'proxyerror' in text:
        code='aliyun_proxy';message='无法通过当前代理连接阿里云，请检查代理设置后继续。'
    elif any(isinstance(n,socket.gaierror) for n in nodes) or 'getaddrinfo failed' in text or 'name resolution' in text:
        code='aliyun_dns';message='无法解析阿里云域名，请检查DNS或网络后继续。'
    elif any(isinstance(n,(TimeoutError,socket.timeout)) for n in nodes) or names.intersection({'ReadTimeout','ConnectTimeout','Timeout'}) or 'timed out' in text:
        code='aliyun_temporary';message='连接阿里云超时，原文与已完成译文保留；本次已发送请求的字符仍保守预留。'
    elif names.intersection({'ConnectionError','ConnectionResetError','ConnectionRefusedError','ConnectError'}) or 'connection refused' in text or 'connection reset' in text:
        code='aliyun_connection';message='无法建立到阿里云的连接，请检查网络后继续；原文和已有译文保留。'
    elif names.intersection({'UnretryableException','RetryError'}):
        code='aliyun_transport';message='阿里云SDK未能完成网络传输，未收到服务响应。请先检查连接，再继续任务。'
    else:
        from .aliyun_translation import mapped_error
        return mapped_error(getattr(exc,'code',None) or type(exc).__name__,getattr(exc,'request_id',None))
    error=CopilotError(code,message,'translation',code=='aliyun_temporary')
    error.failure_layer='transport';error.cause_type=type(nodes[-1]).__name__;return error

def network_status():
    """Fixed-host, unsigned GET via the official SDK's real HTTP transport.

    A 4xx at the root proves connectivity, not account access or translation
    quality. No TranslateGeneral invocation occurs here.
    """
    result={'checked_at':now(),'host':'mt.cn-hangzhou.aliyuncs.com','translation_tested':False,'credentials_sent':False,'source_characters':0}
    try:
        from darabonba.core import DaraCore
        from darabonba.request import DaraRequest
        request=DaraRequest();request.protocol='https';request.method='GET';request.pathname='/';request.headers={'host':result['host']}
        response=DaraCore.do_action(request,{'connectTimeout':3000,'readTimeout':3000,'ignoreSSL':False})
        result.update(reachable=True,http_status=response.status_code,message='已能连接阿里云官方域名；本检查未发送密钥、论文或翻译请求，尚未验证账号权限和论文译文。')
    except Exception as exc:
        error=transport_error(exc)
        if error.code=='aliyun_temporary':
            error.message='连接检查超时，尚未发送翻译请求，也未预留翻译字符。'
        result.update(reachable=False,error=error.as_dict(),message='连接检查未通过：'+error.message)
    return result
