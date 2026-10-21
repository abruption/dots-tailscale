<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="README.md">English</a> · <a href="README.ko.md">한국어</a> · 简体中文 · <a href="README.ja.md">日本語</a></p>

在 Dots 云电脑上以普通用户权限运行 Tailscale 和 OpenSSH。安装文件、节点状态和 SSH 身份保存在 `/workspace`；运行时套接字、PID 和日志会在 `/tmp` 中重新创建。

本方案适用于会保留 `/workspace` 的环境。每次进入新环境后，仍需运行启动命令。本项目独立开发，并非 OpenAI 或 Tailscale 的官方工具。

## 演示

![Dots 终端中的安装检查以及 Tailscale 版本 1.102.4 截图](docs/assets/dots-tailscale-ssh-verification.png)

这是维护者提供的 Dots 桌面终端截图，显示了已有安装的检查结果和状态，以及 Tailscale 版本 1.102.4。截图使用了原始图片，并未展示新安装或 VM 替换。

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

## 快速开始

需要 Linux x86_64、Git、Python 3、OpenSSH，以及绑定本地 22 端口的权限。完整依赖和 SSH 设置请参阅[使用指南](docs/usage.zh-CN.md)。

克隆命令明确使用 Dots 代理，因为在经过测试的 Dots SSH 环境中，直接通过 HTTPS 连接 GitHub 失败了。有关诊断方法和这些设置的适用范围，请参阅[下载连接与代理设置](docs/usage.zh-CN.md#download-proxy)。

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.3.1 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

首次登录时会显示一个授权链接。将客户端公钥添加到已安装 SSH 的 `authorized_keys` 文件中，并在连接前通过云电脑的 GUI 终端核对主机密钥指纹。环境替换后，使用 `bash /workspace/.dots-tailscale/start.sh` 启动现有安装。脚本不会注册自动启动项。

## v0.3.1：诊断与私有 HTTP 访问

要使用这些辅助工具，请从 [v0.3.1](https://github.com/abruption/dots-tailscale/releases/tag/v0.3.1) 一并安装全部四个源文件。

```bash
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts proxy show
```

`doctor` 读取本地诊断信息，不会更改状态。`cli` 会选择已安装的 Tailscale 二进制文件和套接字。可选的回环 SOCKS5/HTTP 代理默认关闭；启用前需要单独审查并重启守护进程。配置、访问策略和验证范围请参阅[使用指南](docs/usage.zh-CN.md#development-helpers)和[私有 HTTP API 配置示例](docs/private-http-api.zh-CN.md)。这些代理不会自动配置 ChatGPT 连接器、MCP 客户端或 Dots 浏览器。

## 开发中：查看已安装的软件包版本

开发版新增了 `dots-ts version` 和 `dots-ts version --json`，可以分别查看 dots-tailscale 软件包版本和 Tailscale 程序版本。v0.3.1 尚不包含这些命令。详情见[软件包版本与文件校验](docs/usage.zh-CN.md#package-version)。

开发版还新增了正式版本检查、可选的缓存通知，以及支持代码备份和回滚的手动更新。运行条件、独立恢复终端确认和单独的 SSH 验证，请参阅[更新说明](docs/usage.zh-CN.md#update-management)。

## 项目内容

| 路径 | 用途 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | 检查、安装和启动 Tailscale；配置本地 OpenSSH；Tailscale 身份验证需明确执行 |
| `dots-ts`, `dots_proxy.py` | CLI、诊断，以及可选的按进程配置出站代理 |
| `home-persistence-probe.py` | 记录 HOME 和 `/workspace` 的连续性标记 |
| `tests/` | 离线回归测试 |

另请参阅[英文使用指南](docs/usage.md)、[韩文使用指南](docs/usage.ko.md)、[简体中文使用指南](docs/usage.zh-CN.md)、[日语使用指南](docs/usage.ja.md)和 [MIT 许可证](LICENSE)。
