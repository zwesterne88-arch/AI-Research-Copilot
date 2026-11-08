# AI Research Copilot

[English](README.md) | **简体中文**

一个在本机运行的论文阅读工作台：管理 PDF、结合原文进行问答、将论文选段翻译成中文，并保存来源和使用记录。大模型和翻译服务均使用你自己的 API Key。

项目由 Python 后端、浏览器工作台、SQLite 本地存储和内置 PDF.js 阅读器组成。启动主应用不需要 Node.js，也不需要前端打包。当前应用界面主要为中文。

## 功能

| 功能 | 说明 |
| --- | --- |
| 论文库 | 导入文本型 PDF，提取页面文字，在本机检索论文。 |
| PDF 阅读 | 阅读原始页面，按引用回到对应来源。 |
| 论文问答 | 将问题及相关片段发送给所选大模型，保存回答和引用。 |
| 中文智能精读 | 定位原文选段、调用翻译接口、保存带来源的中文报告；支持继续任务和复用缓存。 |
| 学术翻译 | 使用所选翻译接口翻译选段或页面内容。 |
| 接口设置 | 选择大模型、翻译供应商，填写自己的密钥并保存。 |
| 使用记录 | 查看请求明细、大模型 token 和提交翻译的字符统计。 |
| 本地账号 | 账号密码注册登录；后续账号拥有独立工作区和配置。 |

点击问答、翻译或一键精读后直接执行，不需要反复勾选供应商授权。仅打开论文不会自动开始翻译。

## 快速开始

### 1. 下载源码

从仓库下载 ZIP 并解压，或使用实际 GitHub 仓库地址克隆：

```bash
git clone <实际仓库克隆地址> AI-Research-Copilot
cd AI-Research-Copilot
```

建议使用 **Python 3.12** 按本文安装。安装依赖和调用远程服务需要联网；打开工作台和阅读 PDF 不要求先填写 API Key。

### 2. 安装并启动

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m research_copilot.web
```

macOS / Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m research_copilot.web
```

浏览器打开 **http://127.0.0.1:8520**。使用时保持终端运行，按 `Ctrl+C` 停止服务。登录页填写本应用的本地账号密码，不是供应商账号密码。

### 3. 配置接口并阅读

1. 在登录页注册自己的账号密码。
2. 打开「接口设置」。
3. 选择大模型供应商，填写可用的模型 ID 和自己的 API Key，保存。
4. 选择翻译供应商，填写相应凭证，保存。
5. 导入 PDF，打开论文，使用「论文问答」或「AI 智能精读」。

直接在页面配置即可，**不必创建 `.env`**。同一安装环境、同一系统运行用户下，退出登录、重新登录或重启不会要求重新填写密钥。后端接口不会回显密钥；编辑设置时将已保存的密钥输入框留空，表示保留原值。

## 支持的接口

### 大模型

| 页面选项 | 请求协议 | 需要填写 |
| --- | --- | --- |
| DeepSeek | Chat Completions | API Key、模型 ID |
| OpenAI / Codex | Responses | OpenAI API Key、该接口可用的模型 ID |
| Claude / Anthropic | Messages | Anthropic API Key、模型 ID |
| Qwen | 兼容 Chat Completions | 百炼 API Key、模型 ID |
| Grok / xAI | 兼容 Chat Completions | xAI API Key、模型 ID |
| 自定义兼容接口 | 可选已支持的协议 | HTTPS 接口地址、API Key、模型 ID |

这里提供后端适配，不附赠供应商订阅或额度。模型名称和权限取决于供应商账号。「OpenAI / Codex」是 API 接入选项，不是登录 Codex 桌面应用；请填写自己账号实际可调用的 API 模型 ID。详细说明见[接口配置](docs/PROVIDERS.md)。

### 翻译

| 供应商 | 凭证 / 设置 |
| --- | --- |
| 阿里云机器翻译 | AccessKey ID、AccessKey Secret |
| 百度翻译 | APP ID、密钥 |
| Google Cloud Translation | API Key |
| DeepL | API Key、与 Free / Pro 账号对应的接口地址 |
| Microsoft Azure Translator | API Key、资源区域 |

适配器通过离线请求与响应测试；这不等于所有供应商和模型均已逐一完成真实联网与质量验证。可选本地翻译运行时单独安装，不包含在源码包中。

## 推荐使用流程

1. **导入论文。** 当前支持不超过 25 MiB、40 页的文本型 PDF。不支持加密 PDF，没有内置 OCR；纯扫描件需先在外部完成文字识别。
2. **查看原文。** 使用阅读器和本地检索了解论文内容。
3. **提交问题。** 例如「这篇论文希望解决什么问题？核心方法如何回应这一问题？」后端从当前论文版本检索片段，再请求所选大模型生成带引用的结构化回答。
4. **开始中文精读。** 系统定位原文选段，使用所选翻译接口生成中文，保存完成状态和缓存。未完成的任务可继续。
5. **核对来源。** 点击来源跳回原文页。生成结果可能只有部分内容；未找到证据不表示论文中一定没有该结论。
6. **查看使用记录。** 查看请求、token、翻译字符和请求状态。

「中文智能精读」与历史格式的大模型分析卡是两个流程：前者翻译定位到的原文，后者让大模型生成结构化分析。论文比较依赖已有分析卡，不能直接视为经过独立验证的论文优劣排名。

使用记录反映本安装环境记录到的请求，不是供应商账号的完整账单、实时余额或费用上限。失败重试可能增加用量；命中缓存可减少新翻译请求。最终账单以供应商控制台为准。

## 账号、密钥和历史数据

全新下载的源码中，**没有作者的 API Key、账号、论文、缓存或使用记录**。其他人自行部署后，注册自己的账号、填写自己的 API Key，费用进入他们自己的供应商账号，不会使用作者的额度。

第一个注册账号接续该安装目录原有的工作区和旧环境配置；后续账号使用独立目录，不继承首个账号的环境密钥。登录不会清空原数据。默认启动方式的数据目录如下：

```text
data/
├── accounts.sqlite3       # 本地账号与会话
├── library.sqlite3        # 首个账号的论文库
├── workspace.sqlite3      # 精读、问答、翻译和用量记录
├── objects/               # 导入的 PDF
├── api_settings.enc       # 加密的接口配置
├── .api_settings.key      # 本地加密密钥材料
└── users/<account-id>/    # 后续账号的独立工作区
```

备份时建议停止服务，将自己的 `data/` 完整复制到私人位置；使用 `.env` 时也应单独备份。不要上传公开仓库。

Windows 使用当前系统用户的 DPAPI 保护配置加密密钥。换机器或换系统用户后，可能需要重新填写 API Key，但论文数据可以保留。其他系统以受文件权限限制的本地密钥文件保存加密材料。应用账号隔离不能阻止具有主机系统权限的管理员访问凭证。详见[凭证与迁移说明](SECURITY.md)。

## 启动参数与部署范围

```bash
python -m research_copilot.web --port 8530
python -m research_copilot.web --data-dir ./my-data
```

主服务绑定 **127.0.0.1**，要求账号登录。当前版本面向本机工作台，尚未提供完整的公网部署配置，包括 HTTPS、公网来源配置、账号找回、防滥用和运维体系。旧版 `app.py` / Streamlit 入口不具备相同的账号隔离，不建议作为多用户入口。

上传 GitHub 是发布源码，不会自动启动 Python 服务。[GitHub Pages 提供静态网站托管](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)，单靠 Pages 无法运行本项目的后端、登录和接口调用。安装、迁移和故障排查见[安装指南](docs/INSTALLATION.md)。

## 开发与测试

在虚拟环境中安装开发依赖：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/*.test.mjs
```

只有前端测试需要 **Node.js 22 或更高版本**；不需要 `npm install` 或前端构建。Python 测试阻止外部 socket 连接，使用合成 PDF 和供应商替身。少量依赖历史私有论文的测试在缺少夹具时会跳过，源码包不附带这些论文。GitHub Actions 已配置 Windows / Ubuntu 公共测试，不需要提供真实 API Key。

```text
research_copilot/   Python 后端：账号、导入、检索、供应商、记录、HTTP 服务
web/               浏览器工作台与 PDF.js 资源
tests/             公共自动化测试与合成夹具
evaluation/        评测工具和结构定义，不含私有数据集
scripts/           发布打包、测试辅助、可选资源和运行时准备
config/            兼容旧配置的空密钥模板
docs/              安装、接口、架构和发布说明
```

修改前可先阅读[架构](docs/ARCHITECTURE.md)和[贡献说明](CONTRIBUTING.md)。需要重新生成公开源码包时，使用[发布指南](docs/PUBLISHING.md)中的白名单打包工具；不要直接上传带有运行数据的整个开发目录。

## 常见问题

**注册需要短信验证码或付费吗？** 不需要。本地使用账号密码注册，没有短信服务。远程大模型和翻译是否收费，由你使用的供应商账号决定。

**每次登录都要重新填写 API Key 吗？** 不需要。同一安装环境内保存一次即可；迁移主机或更换系统运行用户后，可能需要重新填写。

**为什么请求失败？** 检查模型 ID、密钥、供应商权限、额度和后端进程联网情况。浏览器能打开供应商官网，不代表 Python 进程也能联网。Windows socket `10013` 表示该进程的网络访问被拒绝。

**为什么部分页面无法检索？** 可提取文字不足的页面不会被索引。纯扫描件需要外部 OCR；公式、多栏和复杂排版也可能影响提取质量。

**能直接把数据库发到 Issue 吗？** 请改用合成复现材料。数据库和日志可能包含论文片段、问答、会话和使用记录。

## 许可证

项目原创代码采用 [MIT License](LICENSE)。第三方组件保留各自的许可证，详见[第三方声明](THIRD_PARTY_NOTICES.md)。源码包不附带研究论文数据集或付费 API 使用权。
