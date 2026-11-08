# Installation and troubleshooting / 安装与故障排查

Start with [English README](../README.md) or [中文 README](../README.zh-CN.md). Use Python 3.12 and a separate virtual environment. The main entry point is `python -m research_copilot.web`; `app.py` is a legacy Streamlit interface.

## Fresh installation / 全新安装

1. Extract the public source package into its own directory. Do not copy another user's `data/` or `.env` into it.
2. Install `requirements.txt` in a virtual environment using the README commands.
3. Start the backend and open `http://127.0.0.1:8520` in a current browser.
4. Register a username and password, then configure providers in **接口设置**.
5. Import a text-based PDF. Provider requests begin when you submit the corresponding action.

全新安装没有预置账号、作者论文或作者密钥。页面保存接口配置即可，不需要把密钥写进代码。密码注册不使用短信验证码。

## Ports and storage / 端口与目录

```bash
python -m research_copilot.web --port 8530
python -m research_copilot.web --data-dir ./my-data
```

Use the chosen port in the browser URL. Relative `--data-dir` paths resolve from the shell's working directory. The default is the project's `data/`.

启动后不要关闭终端。端口占用时可使用 `--port` 换端口，不必停止其他未知进程。使用自定义数据目录后，备份的是实际目录，而不只是默认 `data/`。

Legacy environment configuration is optional. For the first account, the LLM fallback reads `.env` from the **parent of the chosen data directory**, as well as supported environment variables. Additional isolated accounts do not inherit these credentials. Prefer browser settings; see [providers](PROVIDERS.md).

## Updating and migrating / 更新与迁移

Stop the server, privately back up the complete data directory and any environment configuration, then update the source and reinstall requirements. Start with the same data directory. Do not overwrite it with an empty directory from a fresh installation.

Windows 上的加密配置由保存时的系统用户保护。换机器或换系统用户后不能保证直接解密，可能需要重新填写 API Key。保留论文与历史数据库，不要通过删除 `.api_settings.key` 来尝试恢复配置。详见 [SECURITY.md](../SECURITY.md)。

## Common failures / 常见问题

| Symptom / 现象 | Check / 排查 |
| --- | --- |
| `ModuleNotFoundError` | Install with the same virtual environment Python used to start the server. / 确认安装与启动使用同一个虚拟环境。 |
| Browser cannot connect | Keep the server running and confirm URL and port. / 检查终端、地址和端口。 |
| Windows socket `10013` | Check firewall and process execution context; browser access alone is insufficient. / 检查后端进程是否被限制联网。 |
| Provider authentication failure | Check provider, key type, permissions, and model ID. / 检查供应商、密钥类型与权限。 |
| Empty or partial answer | Inspect extracted text and evidence; try a more specific question. / 检查文字与证据，尝试更具体的问题。 |
| PDF cannot be searched | Scan-only and low-text pages need external OCR; encrypted PDFs are rejected. / 扫描件需先 OCR，加密 PDF 不支持。 |
| Credentials cannot decrypt | Use the original OS account or restore the complete matching backup. / 使用原系统用户或恢复完整配套备份。 |

Keep API keys and session cookies out of bug reports. Use a synthetic paper and redacted errors for reproduction.

## Optional local translation / 可选本地翻译

`scripts/install_local_translation.py` prepares a separate Argos runtime and model downloads. Its launcher setup currently assumes a Windows virtual environment. It is optional, uses additional download/disk space, and is not run by the normal installation commands. When installed, the local service uses loopback port 8521. Model packages have their own licenses.

## Deployment boundary / 部署范围

The application binds to loopback and checks hosts/origins. Public hosting needs explicit work on HTTPS, trusted origins, sessions, account recovery, rate limits and operations. Forwarding the port or changing the bind address is not a complete deployment solution. GitHub source hosting does not run the backend.
