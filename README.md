# AI Research Copilot

**English** | [简体中文](README.zh-CN.md)

A local research workspace for reading PDF papers, asking questions with source citations, and translating selected passages into Chinese. Bring your own LLM and translation API keys.

The application combines a Python backend, a browser workspace, SQLite storage, and a bundled PDF.js reader. Running the main application requires no Node.js or frontend build step. The current application interface is primarily Chinese; documentation is available in both languages.

## Features

| Feature | What it does |
| --- | --- |
| Paper library | Import text-based PDFs, extract page text, and search locally. |
| PDF reader | Read original pages and follow citations back to their source. |
| Paper Q&A | Send a question and retrieved passages to your selected LLM; save answers and citations. |
| Chinese close reading | Locate original passages, translate them, and save a source-linked report. Resume unfinished work and reuse cached translations. |
| Translation | Translate selected text or page content through a configured provider. |
| Provider settings | Select LLM and translation providers and save your own credentials. |
| Usage history | Review requests, LLM token usage, and submitted translation characters. |
| Local accounts | Register with a username and password. Additional accounts have separate workspaces and settings. |

Clicking a Q&A, translation, or close-reading action starts that operation directly, without a repeated provider-consent checkbox. Opening a paper alone does not translate it.

## Quick start

### 1. Get the source

Download and extract the repository ZIP, or clone using the actual repository URL:

```bash
git clone <repository-clone-url> AI-Research-Copilot
cd AI-Research-Copilot
```

Use **Python 3.12** for the documented setup. Installing dependencies and calling remote providers requires internet access. API keys are not required to open the workspace or read PDFs.

### 2. Install and start

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m research_copilot.web
```

macOS / Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m research_copilot.web
```

Open **http://127.0.0.1:8520**. Keep the terminal running; press `Ctrl+C` to stop. The login page uses a local application account, not your provider account password.

### 3. Configure and read

1. Register a local username and password.
2. Open **接口设置** (API settings).
3. Select an LLM provider, enter an accessible model ID and your API key, and save.
4. Select a translation provider, enter its credentials, and save.
5. Import a PDF and use **论文问答** (paper Q&A) or **AI 智能精读** (AI close reading).

The UI configuration is sufficient; you do not need a `.env` file. Saved keys persist across logouts and restarts on the same installation and OS account. API responses do not expose saved keys. Leaving a saved secret field blank preserves its existing value.

## Supported providers

### LLMs

| Setting | Transport | Required configuration |
| --- | --- | --- |
| DeepSeek | Chat Completions | API key and model ID |
| OpenAI / Codex | Responses | OpenAI API key and an API-accessible model ID |
| Claude / Anthropic | Messages | Anthropic API key and model ID |
| Qwen | Compatible Chat Completions | DashScope API key and model ID |
| Grok / xAI | Compatible Chat Completions | xAI API key and model ID |
| Custom endpoint | Selectable supported protocol | HTTPS base URL, API key, and model ID |

These are integrations, not bundled subscriptions. Model IDs and access depend on your provider account. The `OpenAI / Codex` label identifies an API integration, not a Codex desktop login. Use a model available through the selected API. See [provider configuration](docs/PROVIDERS.md).

### Translation

| Provider | Credentials / settings |
| --- | --- |
| Aliyun Machine Translation | AccessKey ID and AccessKey Secret |
| Baidu Translate | APP ID and secret key |
| Google Cloud Translation | API key |
| DeepL | API key and the appropriate Free or Pro API base URL |
| Microsoft Azure Translator | API key and resource region |

Adapters have offline request/response tests. Those tests do not establish live access, pricing, or output quality for every provider and model. The optional local translation runtime is installed separately and is not bundled.

## Reading workflow

1. **Import a paper.** The importer accepts text-based PDFs up to 25 MiB and 40 pages. Encrypted PDFs and scan-only documents are unsupported; there is no built-in OCR.
2. **Inspect the source.** Read original pages and use local retrieval alongside generated answers.
3. **Ask a question.** For example: “What problem does this paper address, and how does its method solve it?” The backend retrieves passages from the current document version and requests a structured answer with citations.
4. **Start Chinese close reading.** Relevant original passages are translated with the selected translation API. Completed work and translations are saved for reuse.
5. **Check evidence.** Follow a source link to its original page. Generated answers can be partial; missing evidence does not prove the paper lacks a result.
6. **Review usage.** Open **使用记录** (usage history) for request details and token / character totals.

Chinese close reading and historical LLM analysis cards are separate workflows: the former translates selected original passages; the latter asks an LLM to construct structured analysis. Comparison views depend on available cards and are not independently verified rankings of papers.

Usage history covers requests recorded by this installation. It is not a provider-wide bill, remaining balance, or guaranteed spending cap. Retries can add usage, while cache hits can avoid new translation requests. Consult the provider dashboard for billing.

## Accounts, keys, and data

A fresh source download contains **no author's keys, accounts, papers, caches, or usage records**. Each person running their own copy registers an account and configures their own API access. Requests do not use the author's account.

The first registered account inherits any existing local workspace and legacy environment configuration. Subsequent accounts have separate directories and do not inherit those environment credentials. Logging in does not erase data. By default:

```text
data/
├── accounts.sqlite3       # Local accounts and sessions
├── library.sqlite3        # First account's paper library
├── workspace.sqlite3      # Reading, Q&A, translation and usage records
├── objects/               # Imported PDFs
├── api_settings.enc       # Encrypted API configuration
├── .api_settings.key      # Local encryption key material
└── users/<account-id>/    # Subsequent accounts' workspaces
```

Back up your own `data/` privately, preferably with the server stopped; also back up `.env` if used. Windows protects the configuration encryption key with the running OS user's DPAPI. Moving settings to another machine or OS user may require re-entering API keys; paper data can still be preserved. Other systems use a local key file with restricted permissions. Application accounts do not hide secrets from an administrator with host OS access. See [security and migration notes](SECURITY.md).

## Runtime options and deployment

```bash
python -m research_copilot.web --port 8530
python -m research_copilot.web --data-dir ./my-data
```

The main server binds to **127.0.0.1** and requires login. This release targets local use; it does not yet provide a complete public hosting setup with HTTPS, public origin configuration, account recovery, abuse controls, or operations tooling. The legacy `app.py` Streamlit interface lacks the same account isolation and is not the recommended multi-user entry point.

Uploading to GitHub distributes source; it does not start the Python service. [GitHub Pages serves static websites](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) and cannot run this Python backend or login system. See [installation and troubleshooting](docs/INSTALLATION.md).

## Development and tests

Inside your virtual environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/*.test.mjs
```

Node.js **22 or later** is needed only for JavaScript tests. There is no `npm install` or frontend build step. Python tests block external socket connections and use synthetic fixtures / provider substitutes. Historical tests skip when private local research fixtures are absent; these fixtures are intentionally excluded. GitHub Actions is configured to run the public suite on Windows and Ubuntu without API secrets.

```text
research_copilot/   Backend: accounts, ingestion, retrieval, providers, records, HTTP
web/               Browser workspace and bundled PDF.js assets
tests/             Public tests and synthetic fixtures
evaluation/        Evaluation utilities and schemas; no private datasets
scripts/           Release tooling and optional asset/runtime preparation
config/            Empty credential templates for legacy configuration
docs/              Installation, providers, architecture and publishing
```

Read [architecture](docs/ARCHITECTURE.md) and [contributing](CONTRIBUTING.md) before changing provider or account behavior. Create a clean source package using the allowlist builder in [publishing](docs/PUBLISHING.md), rather than uploading a working directory with runtime data.

## FAQ

**Does registration cost anything?** No. Local username/password registration uses no SMS service. Remote API usage is billed, if applicable, by your own provider account.

**Do I enter keys every time?** No. Save once per local account and installation. A host or OS-user migration may require reconfiguration.

**Why did a request fail?** Check model ID, credentials, access permissions, quotas, and the backend process's network access. A browser reaching a provider website does not prove Python can connect. Windows socket error `10013` indicates denied process network access.

**Why are pages missing from search?** Pages without enough extractable text are not indexed. Scan-only PDFs need external OCR. Equations and complex layouts can also affect extraction.

**Can I attach my database to an issue?** Use a synthetic reproduction instead. Databases and logs may contain paper text, questions, answers, sessions, and usage details.

## License

Original code uses the [MIT License](LICENSE). Bundled components retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). The source release includes neither research-paper datasets nor paid API access.
