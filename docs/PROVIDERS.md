# Provider configuration / 接口配置

Use **接口设置** to select an LLM provider and a translation provider, enter your own credentials, and save. Different local accounts have separate settings.

## LLM configuration / 大模型

| Provider | Default base URL | Protocol |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `chat_completions` |
| OpenAI / Codex | `https://api.openai.com/v1` | `responses` |
| Anthropic | `https://api.anthropic.com/v1` | `anthropic` |
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `chat_completions` |
| xAI | `https://api.x.ai/v1` | `chat_completions` |
| Custom | Your HTTPS base URL | Select a supported protocol |

Enter the provider's **base URL**, not a full `/chat/completions`, `/responses`, or `/messages` URL. The backend adds the path. Model IDs must be supported by your endpoint and account. Editable presets do not guarantee availability across regions or future model changes.

不要填网页聊天地址。模型名称使用供应商提供的 API 模型 ID；界面名称或客户端订阅名称不能代替模型 ID。自定义接口只适用于已支持的协议，并非任意 HTTP API 都兼容。

Chat Completions and Responses use bearer credentials. Anthropic uses native Messages headers and response structure. Transport lives in `research_copilot/provider_client.py` and `research_copilot/llm.py`; the account registry is `research_copilot/api_settings.py`.

## Translation / 翻译

| Provider | Configuration |
| --- | --- |
| Aliyun | AccessKey ID + AccessKey Secret for the enabled translation service; official TranslateGeneral integration. |
| Baidu | Translation Open Platform APP ID + secret key; other Baidu product credentials are not interchangeable. |
| Google | Google Cloud Translation API key with service access. |
| DeepL | API key and matching base URL: `https://api-free.deepl.com/v2` or `https://api.deepl.com/v2`. |
| Azure | Translator API key and the resource's region. |

The main reading workflow translates English to Chinese. Provider changes can produce different output; it is not a human-reviewed translation. The selected provider receives the text needed for the requested operation.

## Saved settings / 保存规则

- Keys are sent to the local backend for encrypted storage and used by that backend to call the selected provider.
- The settings API does not return saved keys. An empty secret input preserves its previous value.
- Logging out does not delete credentials or papers. Host or OS-user migration may require re-entering keys.
- Fresh public source has no keys. The first local account may inherit its operator's environment settings; additional isolated accounts do not.

源码和前端静态文件不应包含真实密钥。不要把个人 Key 放到 README、截图、Issue、CI 配置或公共变量示例中。

## Optional legacy environment configuration / 旧配置兼容

`.env.example` documents the LLM fallback and Aliyun variables. Copy it locally to `.env` only if needed. Empty templates do not enable a provider. Files under `config/` describe legacy adapters and older quota settings; browser-configured providers use the current account settings path.

| Variable | Purpose |
| --- | --- |
| `RESEARCH_COPILOT_LLM_PROVIDER` | Legacy provider ID |
| `RESEARCH_COPILOT_LLM_BASE_URL` | API base URL |
| `RESEARCH_COPILOT_LLM_MODEL` | Model ID |
| `RESEARCH_COPILOT_LLM_API_KEY_ENV` | Name of the variable containing the API key |
| `DEEPSEEK_API_KEY` | Empty key placeholder in the template |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | Aliyun access key ID |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | Aliyun access key secret |

The main UI does not require repeated consent checkboxes or a manual budget session per action. Legacy modules retain historical settings; they do not describe the current UI workflow.

## Usage and testing / 用量与验证

LLM records use returned usage when available. Translation records include submitted characters and request status. Interpret cached and failed requests alongside their status. Local history is not a live provider balance or consolidated bill.

Offline tests validate protocol handling without paid calls. Live access depends on credentials, permissions, network, model and service configuration. The repository does not include credits or promise a free tier.
