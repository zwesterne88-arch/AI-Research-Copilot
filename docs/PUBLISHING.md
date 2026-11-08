# Publishing to GitHub / 发布到 GitHub

Suggested repository name: **AI-Research-Copilot**. License: **MIT**. `README.md` is the English entry and `README.zh-CN.md` is Chinese. The GitHub owner determines the final URL; a placeholder is not an existing repository.

## What to upload / 上传什么

| Include / 上传 | Exclude / 不上传 |
| --- | --- |
| `research_copilot/`, `web/`, `app.py` | `.env`, `.env.baidu`, `.env.google`, real secrets |
| Public tests, scripts and schemas | `data/`, accounts, sessions, keys and databases |
| Requirements and empty templates | Virtual environments, local models and installed dependencies |
| Both README files and public docs | Paper PDFs, private excerpts and model outputs |
| MIT and third-party notices | Verification logs, screenshots and research history |
| `.gitignore`, `.github/workflows/` | Old Git history containing sensitive files |

**Do not upload the original working directory as a whole.** Generate a separate public folder with the allowlist builder. `.gitignore` does not remove files from ZIPs or erase committed history.

## Build a clean package / 生成源码包

From the project root, with dependencies installed:

```bash
python scripts/build_public_release.py --output release/AI-Research-Copilot
```

The output directory must be new. The tool refuses to overwrite a folder or ZIP. For another build, choose a new directory under `release/`, such as `release/AI-Research-Copilot-v2`.

The builder selects files from an allowlist, excludes private paths and runtime artifacts, and compares file bytes with local credentials held only in memory. It also checks common key patterns and development-machine paths. Outputs:

- A clean source directory.
- `RELEASE_MANIFEST.json` with source file hashes; it does not hash itself.
- A ZIP containing the directory and manifest.
- A summary with ZIP SHA-256 and file count, without secret values.

This audit is not a guarantee that arbitrary future content contains no private data. Review new allowlist entries before publication. Windows 上检查本机加密凭证需要原系统用户；无法解密时应排查运行身份，不要删除凭证或跳过比对后宣称检查通过。

## Validate / 验证

```bash
python scripts/build_public_release.py --audit release/AI-Research-Copilot
```

Extract the ZIP to a separate temporary directory. There, install `requirements-dev.txt` and run:

```bash
python -m pytest -q
node --test tests/*.test.mjs
python -m research_copilot.web --port 8530
```

Confirm registration works and a new account has no papers, saved keys or prior usage. Private historical tests may skip. Check documentation links and retain bundled component licenses.

不要把测试或注册后生成数据的副本整包上传。上传原始干净发布目录，或从已审核 ZIP 重新解压。测试、截图和本机审核记录放在发布目录外。

## Create the repository / 创建仓库

Open the [prefilled GitHub creation page](https://github.com/new?name=AI-Research-Copilot&description=A+local+research+workspace+for+PDF+reading,+source-grounded+Q%26A,+and+Chinese+translation+with+your+own+API+keys.&visibility=public). It fills in an English name and description; you must sign in and create the repository under your account. This is a creation link, not an already published repository. GitHub documents these fields in [creating a repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository).

Create `AI-Research-Copilot` on GitHub with the intended visibility. Leave optional README / license / `.gitignore` initialization unchecked: the package already includes them. Suggested English description:

> A local research workspace for PDF reading, source-grounded Q&A, and Chinese translation with your own API keys.

Upload the **contents** of the clean directory, not only its ZIP. Git is convenient for including all assets and hidden files:

```bash
cd release/AI-Research-Copilot
git init -b main
git add .
git status --short
git diff --cached --stat
git commit -m "Initial open-source release"
git remote add origin https://github.com/YOUR_USERNAME/AI-Research-Copilot.git
git push -u origin main
```

Replace `YOUR_USERNAME` with the real owner. Authenticate through your GitHub login or credential manager, not a token in the URL. These commands target a new empty repository; inspect existing repositories first and do not force-push over unrelated work.

Before committing, confirm staged files exclude runtime data, `.env`, PDFs, databases, credentials and local logs. Empty `.env.example` is intentional. Include all PDF.js assets and their font / WebAssembly license notices.

## After publishing / 发布后

Check both README links and the Actions result. Configuring CI is not the same as passing a hosted run. Replace README clone placeholders with the actual repository URL after creation.

GitHub distributes source. [GitHub Pages is static hosting](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) and does not run this backend. A public hosted application requires separate deployment work.

If a key was committed publicly, revoke or rotate it at the provider before cleaning Git history. Deleting the latest file or adding `.gitignore` does not revoke an exposed key.
