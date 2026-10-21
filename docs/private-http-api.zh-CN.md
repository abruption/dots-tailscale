# 从 Dots 访问私有 HTTP API

[English](private-http-api.md) · [한국어](private-http-api.ko.md) · 简体中文 · [日本語](private-http-api.ja.md) · [使用指南](usage.zh-CN.md#development-helpers)

本指南介绍如何在 Dots 终端进程中，通过可选的本地 Tailscale 代理调用 tailnet 上另一台设备的 HTTPS API。所需辅助工具从 v0.3.0 起提供；请安装 v0.3.0 或更新版本中的完整辅助工具集。v0.2.0 及更早版本不包含这些工具。

## 连接路径与前置条件

```text
Dots curl process -> 127.0.0.1:8080 HTTP CONNECT proxy
                 -> Tailscale -> private API device:8443 -> HTTPS API
```

另一种可选方式是使用 SOCKS5：`socks5h://127.0.0.1:1080`；其中的 `h` 表示通过代理解析目标名称。HTTP CONNECT 同样会把目标主机名传给代理。不要改用 `18089` 端口上的守护进程 shim：它为 Tailscale 本身提供 Dots 平台代理通道，不处理应用请求。

请分别准备以下各项：

- 已通过身份验证并加入目标 tailnet 的 Dots Tailscale 节点，且已安装 v0.3.0 或更新版本中的完整辅助工具集。
- tailnet 中可访问的私有 API 设备，并且 HTTPS 监听器绑定到预期的私有/Tailscale 接口和端口。若监听器只绑定到 API 设备的 `127.0.0.1`，Dots 将无法访问。不要为了省事而将它暴露到公网接口。
- 一个能通过所选代理解析、且与 API 证书匹配的目标名称。请使用受信任的公共 CA，或明确向客户端提供私有 CA 文件；如果使用 IP 地址，证书身份也必须与该 IP 匹配。
- 仅允许所需源设备、目标设备和 TCP 端口的 tailnet 权限，并配置服务器端 bearer 身份验证和 API 授权。Tailscale 连通性不能代替 API 身份验证。
- 一个经过身份验证的只读 `/health` 端点，并确保它只返回少量非私密内容，例如 `{"status":"ok"}`。请将示例端点替换成服务器上功能相同的端点，不要使用导出数据或修改数据的端点。

## 限制网络访问

以下内容是有效的 JSON，也适用于 Tailscale 策略使用的 JSON5 格式。它演示了源标签、目标标签和一个允许的 TCP 端口；标签名称和管理员归属只是策略示例中的占位值。请将相关条目合并到现有策略中，不要替换整个策略文件。

```json
{
  "tagOwners": {
    "tag:dots-client": ["autogroup:admin"],
    "tag:private-api": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["tag:dots-client"],
      "dst": ["tag:private-api"],
      "ip": ["tcp:8443"]
    }
  ]
}
```

有权限的 tailnet 管理员必须将源标签分配给目标 Dots 设备，并将目标标签分配给 API 设备。这些脚本不会分配标签或修改策略。授权规则会累加：这条范围较窄的规则不会撤销已有的更宽泛授权或 ACL 规则。在将最终访问视为受限之前，请检查这些规则、标签归属和 API 主机的防火墙规则。请使用 tailnet 策略编辑器，针对实际节点身份运行验证和策略测试。参阅官方 [grants 文档](https://tailscale.com/kb/1324/grants)。

## 谨慎启用本地监听器

请先阅读[配置与应用步骤](usage.zh-CN.md#development-helpers)。代理监听器默认关闭。仅修改配置只会保存期望状态，不会重启正在运行的守护进程：

```bash
/workspace/.dots-tailscale/dots-ts proxy show
/workspace/.dots-tailscale/dots-ts proxy configure --socks-port 1080 --http-port 8080
```

请为两种代理使用不同端口。目前此实现会拒绝 SOCKS5 和 HTTP 共用同一端口。期望配置保存在 `/workspace/.dots-tailscale/outbound-proxy.json`；环境替换后必须重新创建运行时监听器。应用配置需要单独执行受保护的守护进程重启，这可能会中断 SSH。在按 `dots-ts proxy apply` 的说明操作前，请确保可以使用独立的 Dots GUI 终端，并准备好回滚方案。应用后，请重新检查外部 SSH 的严格主机密钥验证。

这些是供本地进程使用的回环监听器，不是面向其他机器的身份验证网关。任何能够访问这些监听器的进程都可能尝试建立连接。即使只有自己的脚本使用代理，也要保留 API 身份验证和范围严格的 tailnet 权限。

## 发起一次受限的认证请求

**需要 curl 8.4.0 或更高版本。**从此版本起，`--max-filesize` 也会限制大小未知的响应，包括分块响应。示例会先检查版本，再提示输入令牌或发起 API 请求；如果无法获取版本或版本过低，就会停止。

在应用 HTTP 监听器后，于 Dots 终端中运行以下命令。请将 URL 替换为 HTTPS 服务的主机名和端口。示例使用 Python 标准库静默读取 bearer 令牌，并通过标准输入将请求头传给 curl。令牌不会出现在命令行参数、URL、shell 历史记录、环境变量或文件中；请求完成前，它会保留在进程内存里。不要启用 HTTP 跟踪，也不要打印令牌。

```bash
python3 - <<'PY'
import getpass
import json
import os
import re
import subprocess

url = "https://api.example.internal:8443/health"
proxy = "http://127.0.0.1:8080"
ca_file = ""  # Optional trusted private CA file path; blank uses system trust.
child_env = os.environ.copy()
for name in ("NO_PROXY", "no_proxy", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
    child_env.pop(name, None)
try:
    version_result = subprocess.run(
        ["curl", "--disable", "--version"],
        text=True, capture_output=True, timeout=5, env=child_env,
    )
except (OSError, subprocess.TimeoutExpired, UnicodeError):
    raise SystemExit("Could not verify curl version; curl 8.4.0 or newer is required")
version_match = re.match(r"curl ([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})(?:\s|$)", version_result.stdout)
if (version_result.returncode != 0 or version_match is None
        or tuple(map(int, version_match.groups())) < (8, 4, 0)):
    raise SystemExit("curl 8.4.0 or newer is required for the response size limit")
token = getpass.getpass("API bearer token: ")
if not token or any(c in token for c in "\r\n"):
    raise SystemExit("Invalid token input")
command = [
    "curl", "--disable", "--silent", "--show-error", "--proto", "=https",
    "--proxy", proxy, "--noproxy", "",
    "--connect-timeout", "5", "--max-time", "20", "--max-filesize", "4096",
    "--header", "@-", "--write-out", "\n%{http_code}", url,
]
if ca_file:
    command.extend(["--cacert", ca_file])
try:
    result = subprocess.run(
        command, input="Authorization: Bearer " + token + "\n",
        text=True, capture_output=True, timeout=25, env=child_env,
    )
except subprocess.TimeoutExpired:
    raise SystemExit("Request timed out")
except (OSError, UnicodeError):
    raise SystemExit("Could not execute curl or decode its response")
finally:
    token = None
if result.returncode:
    raise SystemExit("curl failed (exit %d); check network, DNS and TLS separately" % result.returncode)
body, separator, status = result.stdout.rpartition("\n")
if not separator:
    raise SystemExit("Missing HTTP status")
print("HTTP", status)
if status != "200":
    raise SystemExit("Unexpected HTTP status; response body omitted")
try:
    healthy = json.loads(body) == {"status": "ok"}
except ValueError:
    healthy = False
if not healthy:
    raise SystemExit("Unexpected health response; body omitted")
print("Expected read-only health response received")
PY
```

此请求不会跟随重定向；请确认使用的是规范端点，不要把凭据转发到其他位置。HTTPS 证书和主机名验证始终保持启用。响应上限为 4 KiB；输出仅包含状态码和是否符合预期结构的结果。示例服务器的响应格式特意设计为不含私密信息。不要将此检查用于包含私密记录的响应。

若要改用 SOCKS5，请先启用对应监听器，然后只将 `proxy` 改为 `socks5h://127.0.0.1:1080`。`--noproxy ''` 可防止 curl 的绕过列表跳过所选代理。清除 `NO_PROXY`/`no_proxy` 和继承的 CA 覆盖设置（`CURL_CA_BUNDLE`、`SSL_CERT_FILE`、`SSL_CERT_DIR`）只影响 curl 子进程，不会影响 shell 或其他应用。使用私有 CA 时，请通过 `--cacert` 明确指定经过审核的 `ca_file`。此操作不会更改系统代理、浏览器设置、MCP 配置或 ChatGPT 插件/连接器设置。其他客户端必须明确支持并配置此代理；任意集成都不会自动获得访问此 API 的能力。

## 逐层验证并检查拒绝场景

`dots-ts doctor` 会报告本地各层状态。可选的 `doctor --target api.example.internal:8443` 只执行有界 TCP 探测，并不验证经过身份验证的 HTTPS。TCP 连接成功不能证明证书有效，也不能证明令牌已获授权。

| 检查项 | 预期结果与说明 |
| --- | --- |
| 本地安装、套接字、守护进程和代理配置 | `doctor` 与 `proxy show` 会区分已保存的配置和运行状态；请检查各层的结果 |
| 允许的源设备访问指定主机和端口 | TCP 连接成功；经过身份验证的只读健康检查返回 HTTP 200 和约定的非私密响应 |
| 缺少或故意使用无效 API 令牌 | 服务器拒绝请求，通常返回 HTTP 401；部分服务会返回 403。不应返回包含敏感信息的响应正文 |
| 设备或目标端口不在允许策略范围内 | 连接会被拒绝或在 API 响应前超时；请检查策略测试，并确认不存在范围更广的许可规则 |
| CA 不受信任或主机名不匹配 | TLS 验证失败；请修正证书或信任配置，绝不要关闭验证 |
| 无法通过代理解析目标名称 | 出现 DNS/代理错误；请将名称解析问题与 tailnet 权限、API 身份验证分开排查 |
| 已禁用并应用配置 | 本地监听器不可用；单独执行 `disable` 只会记录期望状态，不能证明活动监听器已停止 |

HTTP 401 表示 HTTP 服务器已作出响应，不代表网络策略拒绝了连接。仅凭超时无法判断原因是 ACL、防火墙、路由还是 DNS。请使用能够解决问题的最小检查，并避免记录令牌或私密响应正文。

<a id="current-validation"></a>

## 验证状态

2026-10-18（KST）的实时检查确认了原生 SOCKS5 和 HTTP 代理流量、关闭两个监听器，以及通过 SSH 严格主机密钥验证后重新连接。两个代理共完成十项 HTTPS 检查，全部通过：有效身份验证（`200`）、缺少令牌和错误令牌（`401`）、禁止访问的端点（`403`）以及不受信任的 CA（TLS 失败）。另一次“修改前/阻止后/恢复后”检查确认 tailnet 策略拒绝生效；随后恢复了原策略。临时 API 服务器和凭据均已删除。查看[代理结果](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243157252)、[策略结果](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243247818)和 [HTTPS 结果](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243275452)。

这些检查不能证明系统能在 VM 自然替换后恢复，也不能证明会自动启动。在所比较的环境中，Dots 直接 Bash 看到的 `/workspace` 为只读，也看不到运行时套接字；GUI 终端和 SSH 则共享可写文件及正在运行的套接字。应用代理更改时，请使用独立的 GUI 终端；不要假设直接通过 agent 执行命令就能重启服务。生命周期研究仍在进行中，见 [#20](https://github.com/abruption/dots-tailscale/issues/20#issuecomment-6243404537)。
