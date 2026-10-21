# 使用指南

[English](usage.md) · [한국어](usage.ko.md) · 简体中文 · [日本語](usage.ja.md) · [项目介绍](../README.zh-CN.md)

本指南适用于用户级安装，前提是环境替换后 `/workspace` 仍会保留。恢复时会复用已保存的安装文件、身份验证状态和 SSH 主机密钥；只要已保存的凭据仍有效，就无需重新登录。安装过程不会注册启动钩子；每次进入新环境时都要运行启动命令。

## 已发布版本

首次公开仓库的克隆与原始文件下载已与准备好的源文件核对确认。[版本 v0.3.1](https://github.com/abruption/dots-tailscale/releases/tag/v0.3.1)包含 ZIP 和 tar.gz 压缩包，也提供 `tailscale-setup.sh`、`shim.py`、`dots-ts` 和 `dots_proxy.py` 四个单独的资源文件。安装示例将源版本固定为 `v0.3.1`。捆绑的 Tailscale 版本仍为 `1.102.4`。

<a id="download-proxy"></a>

## 下载连通性与代理设置

在 2026-10-15 测试的 Dots SSH 环境中，直接向 GitHub 仓库、原始文件和发布资源 URL 发出的 HTTPS 请求均失败，而明确使用 `http://browser-proxy:8889` 的请求成功。通过代理克隆后，`v0.2.0` 解析到提交 `643484`，下载的原始文件也与该修订版的校验和一致。这些观察结果只适用于该环境，不能据此认定 GitHub 在全局范围内遭到屏蔽。浏览器下载需要单独检查；这些终端检查无法诊断浏览器错误。

如果克隆或下载失败，可在受影响的终端运行以下有时限的检查，查看代理路径是否可用。命令会丢弃响应正文，并仅显示最终 HTTP 状态，不会泄露重定向后的签名下载 URL：

```bash
for url in \
  'https://github.com/abruption/dots-tailscale' \
  'https://raw.githubusercontent.com/abruption/dots-tailscale/v0.2.0/tailscale-setup.sh' \
  'https://github.com/abruption/dots-tailscale/releases/download/v0.2.0/tailscale-setup.sh'
do
  printf '%s\n' "$url"
  curl --fail --silent --show-error --location \
    --proxy http://browser-proxy:8889 --connect-timeout 5 --max-time 20 \
    --output /dev/null --write-out 'HTTP %{http_code}\n' "$url"
done
```

如需与直连路径对比，可在相同命令中将 `--proxy http://browser-proxy:8889` 替换为 `--noproxy '*'`。curl 退出码 `7` 表示连接失败，`28` 表示超时；这两种结果都不能单独证明某个主机名遭到屏蔽。请保持证书验证开启；如果环境提供的代理地址不同，请使用实际提供的地址。

这些设置的作用范围各不相同：

| 设置 | 作用范围 |
| --- | --- |
| `git clone` 或 `git fetch` 之前设置 `HTTPS_PROXY` / `HTTP_PROXY` | 仅对该 Git 命令生效；不会更改全局 Git 设置 |
| curl `--proxy` | 仅对该下载或诊断请求生效 |
| 安装程序的 `UPSTREAM_PROXY` | 安装期间下载 Tailscale 软件包和校验文件时生效，也作为启动期间 shim 的上游代理 |
| `127.0.0.1:18089` 上的本地 shim | Tailscale 守护进程通过其进程环境使用的 TLS/WebSocket 桥接服务 |

本地 shim 不会配置全局代理，也不会让无关应用通过 Tailscale 路由。如果环境使用其他上游地址，请在安装和之后每次启动时显式设置 `UPSTREAM_PROXY`。

## 安装并启动

将仓库从 `https://github.com/abruption/dots-tailscale` 克隆到 `/workspace`，以便安装脚本和 `shim.py` 位于一起。克隆需要 Git，以及云计算机通过代理访问 GitHub HTTPS 的能力。安装程序需要 Linux x86_64、Python 3、`timeout`、`flock`、OpenSSL、`ssh-keygen` 和 `/usr/sbin/sshd`。此外，安装还需要 curl、tar 和 sha256sum。当前用户必须获准绑定端口 22；无需 root 或 systemd。

对于 v0.3.1，请将以下四个源文件放在一起：`tailscale-setup.sh`、`shim.py`、`dots-ts` 和 `dots_proxy.py`。较旧的 v0.2.0 安装只使用原来的两个文件，不包含这些辅助工具。

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.3.1 --depth 1 'https://github.com/abruption/dots-tailscale' dots-tailscale
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

这些代理设置仅对克隆命令生效，不会更改全局 Git 配置。如果环境提供的代理地址不同，请使用该地址。请保持 HTTPS 证书验证开启。

`check` 会列出所选安装文件是否存在；如果守护进程套接字和 CLI 可用，还会输出 Tailscale 状态。它不会启动组件，也不会运行依赖项检查。仅仅缺少文件不会导致退出码非零：未安装的环境可能会输出 `missing:` 行并以退出码 `0` 退出。要判断是否已就绪，请确认成功启动并实际建立 SSH 连接。

`install` 只会放置文件，不会启动进程。它会保留现有二进制文件；如果发现配置或脚本冲突，就会停止安装并要求人工检查。下载的 Tailscale 压缩包固定为脚本中指定的版本，并会根据其官方 SHA-256 文件进行校验。

`login` 会启动尚未运行的组件，并以交互方式进行 Tailscale 身份验证，时限为 180 秒。打开显示的 URL，确认要使用的账户和 tailnet 并完成授权。这些脚本中没有嵌入身份验证密钥；下文会介绍可选的基于文件的身份验证方式。

身份验证完成后，使用以下命令启动尚未运行的组件：

```bash
bash /workspace/.dots-tailscale/start.sh
```

如需检查状态或显式进行身份验证，可向已安装的包装脚本传入 `check` 或 `login`。安装会在安装目录中生成 `start.sh`，并将 `tailscale-setup.sh` 复制为 `setup.sh`。生成的包装脚本会根据自身位置确定该目录，并调用同级的 `setup.sh`。更新现有安装时，请按照下文的[更新步骤](#restart-update)操作。

| 设置 | 默认值 |
| --- | --- |
| `DOTS_TS_DIR` | `/workspace/.dots-tailscale` |
| `UPSTREAM_PROXY` | `http://browser-proxy:8889` |
| `NODE_HOSTNAME` | `dots-sandbox` |

备用的 `DOTS_TS_DIR` 必须是绝对路径，且不能包含空白字符。`UPSTREAM_PROXY` 必须使用不含身份验证信息的 HTTP 代理 URL。安装和之后每次启动都应使用一致的设置。当前 shim 监听端口 18089，SSH 监听端口 22；此配置仅支持单个安装实例。

### SSH 密钥与就绪状态

SSH 监听 `127.0.0.1:22`，并使用公钥身份验证。Tailscale SSH 已禁用；shell 会话由本地 OpenSSH 服务器处理。如果 HOME 中已有 `.ssh/authorized_keys`，安装程序会将其复制到持久化安装目录中。否则，请将客户端公钥添加到：

```text
/workspace/.dots-tailscale/sshd/authorized_keys
```

将客户端公钥作为一整行附加到文件中，并将文件权限设为 `600`。在客户端上，从对应的 `.pub` 文件获取公钥；绝不要传输私钥。在客户端接受服务器主机密钥之前，请先在云计算机上核对主机密钥指纹：

```bash
ssh-keygen -lf /workspace/.dots-tailscale/sshd/ssh_host_ed25519_key.pub
```

在 Dots 终端中，通过已安装的包装脚本获取节点地址：

```bash
bash /workspace/.dots-tailscale/start.sh check
```

此操作使用已安装的 CLI 及其每用户套接字 `/tmp/dots-tailscale-$(id -u)/tailscaled.sock`；无需全局 `tailscale` 命令或默认套接字。要显示节点地址，守护进程必须已经在运行。自定义安装时，请使用该安装目录中的 `start.sh`。

首次连接时，在 tailnet 客户端上运行以下命令。在主机密钥提示处，将显示的指纹与之前在 Dots 终端中独立获取的指纹进行比较。如果两者不一致，请回答 `no` 并停止；不要登记该密钥，也不要删除现有条目。不要仅凭 `ssh-keyscan` 的输出信任密钥。只有指纹一致时才接受；接受后，客户端会将该密钥记录到 `known_hosts` 文件中。

```bash
ssh -o StrictHostKeyChecking=ask -i /path/to/client_private_key 'agent@<tailscale-ip>' 'id; pwd'
```

已记录并验证密钥后，后续连接请启用严格检查：

```bash
ssh -o StrictHostKeyChecking=yes -i /path/to/client_private_key 'agent@<tailscale-ip>' 'id; pwd'
```

如果云计算机上的用户名与示例不同，请替换示例用户名。守护进程运行时，请使用 `start.sh check` 报告的地址。`StrictHostKeyChecking=yes` 要求主机密钥已经受信任；它无法通过交互方式接受新密钥。如果密钥发生变化，请先独立核验新指纹，再修改保存的条目；不要绕过主机密钥验证。

`running: Tailscale and local SSH ready` 表示后端处于 Running 状态，且本地 SSH banner 有响应。这并不能证明客户端公钥身份验证成功；请另行测试实际 SSH 命令。

| 启动结果 | 退出码 |
| --- | --- |
| Tailscale 运行且本地 SSH 已就绪 | `0` |
| 需要身份验证 (`needs_auth`) | `10` |
| 后端就绪时限已到或处于其他不可用状态 | `11` |
| 截止时限仍未收到有效后端状态 | `12` |
| 需要管理员批准这台设备 (`needs_machine_auth`) | `13` |
| 后端处于 Running 状态时显式身份验证命令失败 | 原始命令的失败退出码 |
| 安装或组件故障 | 非零，通常为 `1` |

后端就绪检查在每个阶段使用一个 30 秒单调时钟截止时间，其中包括重试和等待；每次状态查询最多耗时三秒，且不能超过剩余时限。`NoState` 和 `Starting` 会重试。如果此前观察到有效的暂态，则即使后续查询失败，时限到期仍返回退出码 `11`；退出码 `12` 表示该阶段始终未获得有效状态。仅 CLI 通信成功并不代表已就绪。此截止时间仅覆盖后端阶段，不包括所有组件初始化或显式身份验证：`login` 和 `auth` 会先等待本地 API 返回有效响应，最长 30 秒。此预检查接受 NoState/Starting 状态，不要求后端处于 Running，因此刚启动且尚未身份验证的守护进程也可以继续执行显式 `up`。`login` 仍有单独的 180 秒命令时限；`auth` 则有单独的 60 秒身份验证时限；之后两者都会执行最终的后端就绪检查。如果机器已处于待批准状态，则跳过 `up`。如果 `up` 失败或超时，仍会检查后端状态：NeedsMachineAuth 映射为 `13`，NeedsLogin 映射为 `10`；后端处于 Running 状态也不会掩盖命令失败。新启动后的状态探测最多耗时三秒。

普通的 `start` 不会调用 login。先前已通过身份验证的状态若消失，程序会报错并要求人工检查，而不会静默替换身份。现有文件冲突、CA 安装不完整或已建立的主机密钥缺失，也都需要人工检查。更新脚本时不要删除安装目录：该目录包含节点状态和私钥。

自定义 TLS/WebSocket shim 实现了配置好的本地 HTTP CONNECT 桥接服务。使用隔离套接字进行的本地测试涵盖了初始载荷保留和传输边界。修订后的 shim 于 2026-10-14 部署，并通过代理路径完成了实时 DERP ping 和严格公钥 SSH 检查。使用旧版 shim 时，维护者观察到安装文件在环境替换后仍被保留，并成功手动重启。修订版尚未经过后续环境替换验证。平台代理发生变化时，该 shim 可能无法工作。

本仓库没有 VM cron、systemd 服务或自动启动注册。尚未测试通过 Dots 自动化启动。VM 内的进程循环无法在该环境销毁后继续运行。请在新环境中显式启动安装，或通过单独验证过的平台执行机制启动。

<a id="package-version"></a>

## 软件包版本与文件校验（开发中）

这是为 v0.4.0 开发的功能，上面的 v0.3.1 安装示例尚不包含它。发布新版本不会自动更新已有安装。

```bash
/workspace/.dots-tailscale/dots-ts version
/workspace/.dots-tailscale/dots-ts version --json
```

命令只检查本地软件包文件，不连接 GitHub 或 tailnet。它分别显示清单声明的软件包版本、源文件的一致性和已安装的 Tailscale 程序版本。无法确定的源码修订版本会保留为未知。文件与清单一致，只能说明文件一致性，不能证明发布者身份，也不能证明正在运行的服务正常。

请将同一已审核修订版本的四个源文件与 `source-manifest.json` 放在一起。新安装程序会校验这些文件，并将清单保存在安装目录中。不要用修改后的安装文件重新生成预期校验值来掩盖不一致。没有元数据的旧版 v0.2/v0.3 安装会显示软件包版本未知，不能仅凭 Tailscale 版本推断软件包版本。

准备更新或回滚时，请把源文件与对应的原始清单作为一组保存并一起替换。不要只把新清单覆盖到旧代码上。保留节点状态、私钥、SSH 身份和代理设置。下方手动更新的注意事项仍然适用；此命令本身不会更新或重启服务。

JSON 将 `package.declared_version`、`package.source_revision`、`package.integrity` 和逐文件结果，与 `tailscale.version`、`tailscale.status` 分开显示。一致性结果为 `match`、`mismatch`（文件被修改或版本混用）、`incomplete`（文件缺失或不可读）或 `unknown`（元数据缺失、无效或不可读）。目前 `source_revision` 为 `null`，不会猜测提交版本。mismatch/incomplete 的退出码为 `1`，其余为 `0`；因此，退出码 `0` 本身并不代表版本已知或 Tailscale 程序可用。`doctor` 将未知元数据标为 degraded，将文件不一致或不完整标为 unhealthy。

维护时，在确认源码修改后运行 `python3 scripts/source_manifest.py`，检查生成的差异，再运行 `python3 scripts/source_manifest.py --check`。CI 只检查，不重写文件。请只在已审核的源码检出目录中生成清单，不要在安装目录中生成。发布时需要将匹配的清单与四个源文件一起提供；现有 v0.3.1 发布包不包含此清单。

<a id="update-management"></a>

## 发布检查与本地通知（开发中）

这些命令正在为 v0.4.0 开发，需要新版辅助工具；发布新版本不会自动更新已有安装。发布检查只接受来自 `abruption/dots-tailscale` 的稳定版，且必须包含四个源文件和匹配的 `source-manifest.json`。不含此清单的旧版（包括 v0.3.1）会报告为不受支持，不会被当作已验证的更新包。

```bash
/workspace/.dots-tailscale/dots-ts update check --json
/workspace/.dots-tailscale/dots-ts update check --tag v0.4.0 --proxy http://browser-proxy:8889 --json
```

第二个示例会选择指定的稳定版本；只有该版本发布后才能使用。未指定 `--proxy` 时，检查会直接通过 HTTPS 连接，不会继承 shell 中的代理凭据。需要时请显式指定平台下载代理。这与 shim 代理以及应用 SOCKS/HTTP 代理相互独立。HTTPS 证书验证始终保持启用。无需 GitHub token。

`update check` 不会更改已安装代码、节点状态、配置或进程。JSON 中的 `status` 可为 `update_available`、`current`、`local_newer`、`not_comparable` 或 `check_failed`；`selection` 用于区分选择最新版本还是固定版本。有效比较的退出码为 `0`，本地安装无法比较时为 `1`，查询失败时为 `2`。请根据状态判断，不要假定退出码为 `0` 就表示存在更新。即使找到了远程发布版，未知、已修改或混杂的本地源文件也不会因此成为可信版本。

通知为可选的本地功能，不会安装更新、注册定时器或发送外部消息：

```bash
/workspace/.dots-tailscale/dots-ts update notifications configure --enable --interval-hours 24
/workspace/.dots-tailscale/dots-ts update notifications refresh --proxy http://browser-proxy:8889
/workspace/.dots-tailscale/dots-ts update notifications show
/workspace/.dots-tailscale/dots-ts update notifications notify
/workspace/.dots-tailscale/dots-ts update notifications configure --disable
```

`refresh` 会执行有时限的网络检查并保存本地结果。`notify` 只读取缓存，遵守过期时间，并抑制同一版本的重复通知。交互式启动成功时，可在短时限内将缓存通知输出到 `stderr`；通知检查失败不会改变启动结果。原样转发的 `cli` 输出和 JSON 输出不会增加通知文字。存储只读或刷新失败时，不会把旧数据当作一次新的成功检查。使用 `show` 查看记录时间以及结果是否过期或出错。定期刷新需要另行显式配置调用方；VM 不运行时，VM 内不会执行任何命令。默认情况下，`refresh` 遵守已配置的间隔；传入 `--force` 可提前刷新。

### 经验证的手动更新

请在能够访问安装目录、运行时套接字和 Linux 进程信息的环境中运行。`update apply` 要求使用支持 pidfd 的 Linux，并且 Python 版本为 3.9 或更高。开始前，请打开云电脑上的独立终端并确认可以操作该终端。`--confirm-recovery-terminal` 只记录你已确认；它无法替你检查该终端。支持通过 SSH 发起更新，因为事务工作进程会与调用会话脱离；但 SSH 连接本身不能作为恢复途径。

先检查并选定一个固定的稳定版本：

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.4.1 --proxy http://browser-proxy:8889 --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts update status --id TRANSACTION_ID --json
```

以上仅为示例，并不表示 v0.4.1 已经发布。请将 `TRANSACTION_ID` 替换为 `apply` 返回的 ID。如果发布下载受阻，或需要明确测试尚未发布的候选版本，也可以使用经过审查的本地源文件包：

```bash
/workspace/.dots-tailscale/dots-ts update apply --bundle /workspace/reviewed-bundle --confirm-recovery-terminal --json
```

源文件包必须包含四个源文件和与之匹配的原始清单。本地选择可以是开发版本，但不会被标记为已发布的 GitHub 版本。更新器会在替换文件前验证哈希和语法。校验和匹配只能证明文件与所选源文件包一致，不能独立证明发布者可信。

`started` 表示已脱离会话运行的工作进程接受了操作，不表示更新成功。重新连接后，请轮询 `update status`。最终状态包括 `applied`、`failed_rolled_back` 和 `rollback_failed_manual_required`。本地就绪状态和版本验证不能证明外部 SSH 可用：更新成功或成功回滚后，请使用严格主机密钥验证新建 SSH 连接，并单独测试所需连接。

工作进程会在与 setup 和 proxy apply 共用的操作锁保护下，仅备份和替换四个源文件及其清单。它只会重启已识别的 shim 进程；tailscaled 和 sshd 会继续运行。节点状态、登录凭据、私钥、SSH 配置和代理设置不会被复制或重置。若发布版本会更改捆绑的 Tailscale 二进制版本，此更新器会拒绝操作；该类更新不在其适用范围内。更新器不会终止无法识别的进程，也不会删除套接字。

必须有匹配的已安装清单。对于没有元数据的旧安装，先将全部四个已安装文件与独立获取并经过审查的发布版本逐一比对。不要根据已安装文件本身生成预期校验值。如果无法确认来源，或文件版本混杂，请停止并进行人工审查。若要用源码检出执行有意的迁移，该检出也必须先与自身清单匹配，更新器才能用于现有安装。

之前未完成的事务会阻止新更新。工作进程被终止或 VM 被替换后，请在独立终端检查状态和仅包含代码的备份；不要为了强行重试而删除事务记录，也不要假定下一步一定安全。如果回滚无法恢复就绪状态，请保留证据，并根据已验证的备份手动恢复。此更新器不是自动 VM 恢复服务。

事务记录保存在 `.update-transactions/TRANSACTION_ID/` 下：`journal.json` 记录阶段，`stage/` 保存选定的文件包，`backup/` 只保存之前的源码包，`supervisor/` 保存更新器代码快照。在确认外部连接正常前，请保留这些记录。`update status` 对运行中或已应用的事务返回退出码 0；对拒绝、已回滚、中断或需要手动恢复的结果返回 1。运行中的退出码 0 不代表已经完成。

`applied` 表示所选代码与清单一致，并通过了本地就绪检查，不代表已经验证 shim 的端到端 CONNECT/WebSocket 通信。重启的 shim 保留上游主机、端口和使用绝对路径的 SSL 证书设置，但不继承其他 shell 环境或调试设置。其 stdout/stderr 会被丢弃，因此应查看事务中的固定错误码并单独检查连接，而不要期待旧 `shim.log` 出现新的输出。

中断后，可以在保留证据的前提下完成手动恢复：

1. 在独立终端中明确确认 worker 已退出。PID 无法访问或 status 不确定，都不能作为退出的证据。
2. 必要时恢复经过审核的代码包并启动，验证清单、本地就绪状态以及一次新的外部严格主机密钥 SSH 连接。旧版工具可能需要单独审核的源码运行器来检查清单。
3. 确认没有活动 worker，并持有安装目录的 `.operation.lock` 后，将整个已审核的事务目录移动到 `.update-transactions/` 之外、属于当前用户且权限为 700 的 `.update-recovery-archive/`。日志和备份应一并保留。归档后，该事务不再阻止新更新；不要为了绕过限制而归档仍在运行或未经审核的事务。

<a id="development-helpers"></a>

## v0.3.1 辅助工具与可选应用代理

以下命令要求 v0.3.1 的文件已一起安装；v0.2.0 发布版不提供这些命令。`dots-ts` 使用 Python 3 标准库，并根据自身位置查找安装目录。无需全局 `tailscale` 命令、套接字符号链接或修改 shell 配置文件。

```bash
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts doctor --json
/workspace/.dots-tailscale/dots-ts doctor --target api.example.internal:8443
```

`cli` 使用安装目录的运行时套接字执行已安装的 Tailscale CLI，并转发其参数。它会保留原始输出和交互行为，没有辅助工具时限，而且可能暴露地址或其他敏感 CLI 输出。运行时间较长的命令请自行设置时限。它不会自行登录或启动守护进程。

`doctor` 仅执行只读检查：不会启动进程、进行身份验证、修复文件或更改代理设置。它按层级报告经过脱敏的本地检查结果，不会宣称每个应用都已就绪。请查看 `overall` 和各项结果：即使为 `degraded`，退出码也可能是 `0`。`--target HOST:PORT` 还会通过本地 Tailscale 守护进程执行有时限的 TCP 连通性检查，而不会连接应用 HTTP/SOCKS 监听器；该检查不会发送凭据，也无法证明 TLS、API 身份验证或授权状态。目标名称仍会触发明确的网络请求，因此请选择你确实要联系的服务。应用检查和验证矩阵请参阅[私有 HTTP API 配置示例](private-http-api.zh-CN.md)。

### 执行上下文诊断

`doctor` 只描述当前执行上下文及其中可见的进程。它会在 JSON 中新增 `context` 对象，用于报告安装目录和运行时的可见性、基于元数据判断的读写权限，以及文件系统的 `read_only` / `read_write` / `unknown` 状态。这些都是元数据和访问权限检查；不会尝试写入。`advice` 包含固定的指导 ID，`skipped_checks` 标出未执行的检查；人类可读输出会展开相应指导内容。

套接字缺失可能意味着它在当前上下文中不可见，但守护进程仍在其他位置运行。就当前上下文而言，该结果仍为 `unhealthy`，退出码为 `1`；权限被拒绝或套接字无响应同样属于失败。检查结果未知，或安装目录只读但其他服务正常时，结果为 `degraded`，退出码为 `0`；只有 `overall=healthy` 时，`healthy` 才为 true。请检查各个字段，不要仅凭退出码 `0` 判断已就绪。套接字元数据和连接结果会区分缺失、访问被拒和不可用状态。

如果安装目录或运行时为只读或无法访问，或者套接字缺失/无响应，请打开云计算机上独立的 GUI 终端，并在那里运行 `doctor`。不要据此推断必须重新安装、登录、更改权限或重置状态。用户 ID 相同并不能证明文件系统或套接字的可见性相同。此诊断不会进入其他命名空间，也不会更改挂载。

### 分别配置并应用代理

```bash
/workspace/.dots-tailscale/dots-ts proxy show
/workspace/.dots-tailscale/dots-ts proxy configure --socks-port 1080 --http-port 8080
```

配置会将所需的 loopback 监听器记录到 `/workspace/.dots-tailscale/outbound-proxy.json`；它不会重启守护进程，也不会立即启用监听器。SOCKS5 和 HTTP 代理默认禁用。两个监听器请使用不同端口：即使原生 Tailscale 支持共用一个端口，此实现最初仍会拒绝共用端口的设置。配置会保存在 `/workspace`，而环境替换后必须重新创建套接字和运行中的进程。

应用更改后的配置需要显式重启守护进程，且可能中断 Tailscale SSH。首先确保可以使用独立的 Dots GUI 终端，记录当前设置和连通性，并准备好回滚方案。在该终端运行 `/workspace/.dots-tailscale/dots-ts proxy apply`。如果设置了 `SSH_CONNECTION` 或 `SSH_CLIENT`，更改配置的操作会被拒绝；已经应用的配置则不会产生变化。如果替换失败但回滚成功，旧守护进程会恢复，而所需配置仍处于待应用状态。不要把现有 SSH 会话当作恢复途径，也不要认为本地重启成功就证明远程访问正常。请重新检查守护进程状态、本地 SSH、外部严格主机密钥 SSH 和目标应用；如果检查失败，请从 GUI 终端回滚。仅执行 `proxy configure` 不会部署生产代理。

```bash
/workspace/.dots-tailscale/dots-ts proxy disable
```

`disable` 会保存禁用配置；已运行的监听器仍会继续运行，直到显式应用更改。禁用代理或应用设置不会删除节点状态、密钥或安装文件。

应用代理会在 loopback 上监听，供显式配置的本地客户端使用。它们与守护进程的 TLS/WebSocket shim（位于 `127.0.0.1:18089`）不同。不要把该 shim 当作应用 HTTP 代理。请在单次 curl 请求或子进程上设置代理；代理设置不会传递到全局环境、浏览器、MCP 或 ChatGPT 连接器。能够访问 loopback 代理的本地进程都可以尝试使用它，因此仍需设置网络策略并进行 API 身份验证。

### 不使用 Git 安装 v0.3.1

将全部四个发布文件下载到单独的源文件目录中。如果任一请求失败，请停止；安装前请检查文件和哈希：

```bash
(
  set -euo pipefail
  mkdir -p /workspace/dots-tailscale-v0.3.1
  cd /workspace/dots-tailscale-v0.3.1
  DOTS_TS_REF=v0.3.1
  for file in tailscale-setup.sh shim.py dots-ts dots_proxy.py; do
    curl -fsSL --proxy http://browser-proxy:8889 --connect-timeout 5 --max-time 60 \
      "https://github.com/abruption/dots-tailscale/releases/download/${DOTS_TS_REF}/${file}" \
      -o "$file"
  done
  sha256sum tailscale-setup.sh shim.py dots-ts dots_proxy.py
)
```

所有文件下载成功且检查完毕后，对于全新安装，请运行以下命令：

```bash
cd /workspace/dots-tailscale-v0.3.1
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

运行安装程序前，请将全部四个文件保存在同一目录中。如果发布资源主机不可用，请从 `https://raw.githubusercontent.com/abruption/dots-tailscale/v0.3.1/` 下载相同标签和文件名的文件，或从另一台可信设备传输已检查的文件。若为现有安装，请参阅[下文的更新步骤](#restart-update)；`install` 不会覆盖内容不同的已安装源文件。

### 可选的非交互式身份验证

如果需要无浏览器身份验证，请将 Tailscale 身份验证密钥放在私密文件中，然后显式运行 `auth`：

```bash
chmod 600 /workspace/.dots-tailscale/auth.key
DOTS_TS_AUTH_KEY_FILE=/workspace/.dots-tailscale/auth.key \
  bash /workspace/.dots-tailscale/start.sh auth
```

请使用安全方式单独创建密钥文件。不要将密钥粘贴到聊天、源代码、命令行参数或 Git 中。`DOTS_TS_AUTH_KEY_FILE` 必须指定当前用户拥有的常规文件的绝对路径；该文件不能是符号链接，权限必须恰好为 `600` 或 `400`。密钥会以 `--auth-key=file:PATH` 的形式传给 Tailscale。

如果后端已处于 Running 状态，`auth` 会跳过身份验证，不读取也不要求提供密钥文件。已有身份验证标记之后若状态丢失，此模式仍会拒绝继续；不会静默替换现有节点身份。脚本不会更改 tailnet 标签或 ACL 策略、强制重新身份验证或重置偏好设置。auth 命令受 60 秒进程超时和 45 秒连接超时限制；不会回退到浏览器身份验证。尚未测试实际的身份验证密钥注册。普通 `start` 绝不会自动回退到身份验证密钥。

## 检查 HOME 连续性

在 Dots 的仓库检出目录中运行探测程序：

```bash
python3 /workspace/dots-tailscale/home-persistence-probe.py
```

首次运行会在 `$HOME/.dots-home-persistence-marker.json` 中创建一个随机标记，并在 `/workspace/.dots-persistence-probe` 下创建基线、workspace 标记、最新结果和历史记录。后续运行会与该初始基线进行比较。探测程序只读取自身的标记和进程上下文，不读取身份验证状态或用户文档。

| `assessment` | 含义 |
| --- | --- |
| `baseline_created` | 已记录首次观察结果 |
| `markers_intact` | 两个标记的身份都与基线一致 |
| `home_marker_changed_workspace_intact` | HOME 标记发生变化或已消失，但 workspace 标记仍然完好 |
| `marker_continuity_changed` | 其他标记连续性发生变化 |

标记丢失只能证明标记丢失，不能证明整个 HOME 目录已被重置。主机名、启动 ID 和 PID 1 的启动时刻可提供上下文；仅凭启动 ID 无法区分容器替换和主机重启。如果 workspace 基线也消失，下次运行会创建新基线，因此请在 VM 外部保留结果以便比较。

你可以通过 SSH 运行探测程序，并将结果保存在 VM 外的客户端以便比较：

```bash
ssh agent@dots-sandbox 'python3 /workspace/dots-tailscale/home-persistence-probe.py' > observation.json
```

请根据自己的设置调整 SSH 密钥文件和主机别名。结果包含主机名和标记 ID，公开分享前请先匿名化。可使用 `--home` 和 `--probe-root` 指定自定义路径。此探测程序不会注册定期运行。

<a id="restart-update"></a>

## 更新已安装脚本

本项目没有自动更新程序。请在 `/workspace/.dots-tailscale` 之外将 `tailscale-setup.sh` 和 `shim.py` 一起暂存并检查；请选择特定提交或发布版本，不要直接从会变化的分支就地更新已安装文件。记录该版本和这两个文件的 SHA-256 哈希。更改安装目录之前，请验证暂存 shell 脚本的语法，并解析暂存的 Python 源码。

更新到 v0.3.1 时，也要从该确切提交暂存 `dots-ts` 和 `dots_proxy.py`。下方的兼容性代码块只验证原有的 setup/shim 文件对，不会验证新增的辅助工具。另外，请在不执行的情况下编译这两个 Python 辅助工具，并记录全部四个源文件的哈希。部署和回滚时，请将 `dots-ts` 和 `dots_proxy.py` 与 `setup.sh`、`shim/shim.py` 一同放在安装根目录，权限设为 `700`。保留 `outbound-proxy.json` 和节点身份文件。不要在文档或更新过程中顺带应用代理配置或重启守护进程。

```bash
python3 -c 'from pathlib import Path; [compile(Path(p).read_text(), p, "exec") for p in ("dots-ts", "dots_proxy.py")]'
sha256sum tailscale-setup.sh shim.py dots-ts dots_proxy.py
```

使用独立的暂存检出目录 `/workspace/dots-tailscale`（如有需要可调整路径）。将 `<REVIEWED_COMMIT>` 替换为你检查过的完整 40 字符 SHA。无论当前位于分支还是分离的 HEAD，此代码块都可用于重复更新。它会拒绝处理已跟踪、已暂存或未跟踪的改动，且不会丢弃这些改动。fetch、修订版本、checkout 或验证失败都会停止子 shell；只有整个代码块成功后才能继续部署。它只会修改暂存检出目录，不会更改已安装文件或身份验证状态。代理设置仅对 fetch 生效；请保持证书验证开启。

<!-- update-staging:start -->
```bash
(
  set -euo pipefail
  cd /workspace/dots-tailscale
  REVIEWED_COMMIT='<REVIEWED_COMMIT>'
  [[ "$REVIEWED_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || {
    printf 'Stop: specify the full reviewed commit SHA.\n' >&2
    exit 1
  }
  status=$(git status --porcelain --untracked-files=all)
  [[ -z "$status" ]] || {
    printf 'Stop: preserve and review local changes before updating.\n%s\n' "$status" >&2
    exit 1
  }
  HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
    git fetch --tags origin
  target=$(git rev-parse --verify "${REVIEWED_COMMIT}^{commit}")
  git switch --detach "$target"
  [[ "$(git rev-parse HEAD)" = "$target" ]]
  bash -n tailscale-setup.sh
  python3 -c 'from pathlib import Path; compile(Path("shim.py").read_text(), "shim.py", "exec")'
  git rev-parse HEAD
  sha256sum tailscale-setup.sh shim.py
)
```
<!-- update-staging:end -->

shim 发生变化时，请按以下步骤部署：

1. 打开云计算机平台的 GUI 终端，该终端必须独立于 Tailscale SSH。保留此终端以便恢复。将已安装的 `setup.sh` 和 `shim/shim.py` 哈希、节点地址及 SSH 公钥主机密钥指纹记录在 VM 外部。确认基线已通过身份验证，并将基线记录与候选版本分开保存。
2. 仅保存旧版 `setup.sh` 和 `shim/shim.py` 源文件的私密回滚副本；对于 v0.3.1，还要包含现有的 `dots-ts` 和 `dots_proxy.py`，并记录它们在更新前是否不存在。保留身份验证状态、CA 密钥、SSH 主机密钥和授权密钥；不要将状态复制到另一个安装目录。在停止进程之前，准备好有时限的脱离终端重启方案和回滚方案。脱离终端执行的操作必须能在 SSH 会话断开后继续；本仓库没有为你实现此更新程序。
3. 根据确切命令行和已安装脚本路径识别正在运行的 shim。从独立终端只停止已识别的 shim PID。不要按宽泛的进程名称终止进程。确认该进程已退出，且没有重复的 shim。将暂存的 `shim.py` 安装到 `shim/shim.py`，并将暂存的 `tailscale-setup.sh` 安装为 `setup.sh`，权限设为 `700`。对于 v0.3.1，还要将暂存的 `dots-ts` 和 `dots_proxy.py` 放在安装根目录，权限设为 `700`。
4. 执行准备好的脱离终端启动操作，然后将已安装源文件的哈希与暂存文件的哈希进行比较。确认只有一个 shim，且守护进程符合预期。在独立终端检查 Tailscale CLI 状态和本地 SSH banner，然后通过外部连接检查 DERP 连通性，并使用严格主机密钥验证运行真实 SSH 命令。将结果记录在候选版本名下。
5. 如果验证失败，请使用独立终端只停止已识别的候选 shim，恢复旧源文件，然后重新启动。重新检查旧版哈希和基线连接。回滚期间请保留现有状态和密钥；如发现主机密钥意外变化，应先调查，不要直接接受。

不要将 `install` 当作快捷更新方式，也不要删除安装目录。安装程序会有意拒绝冲突文件，而安装目录中包含身份材料。仅更新 setup 脚本也需要记录版本和哈希，并验证启动；这并不能证明已加载单独变更的 shim。

受控重启成功只能证明该环境中的进程恢复能力，不能证明 VM 替换后的持久性。连续性探测仅用于观察：等待环境自然变化，在外部保留结果，并单独评估已通过身份验证的连续性。连续性探测无需强制重置或重启。

## 文件存储位置与 SSH 权限

| 路径 | 内容 |
|---|---|
| `/workspace/.dots-tailscale/state` | Tailscale 身份验证状态 |
| `/workspace/.dots-tailscale/shim` | shim、CA、私钥和证书 |
| `/workspace/.dots-tailscale/sshd` | SSH 配置、主机密钥和 `authorized_keys` |
| `/tmp/dots-tailscale-$(id -u)` | 每用户的套接字、PID 和运行日志 |

如果已认证安装的 state 文件丢失，脚本会停止，不会自动生成另一个设备身份。不要将同一个 Tailscale 节点的 state 复制到多个同时运行的 VM。`/workspace` 并非自动私密的位置；只能使用你能控制访问权限的目录。SSH 服务器使用 `StrictModes no`，因为 `/workspace` 上级目录的权限可能导致 OpenSSH 拒绝公钥身份验证。存储目录权限会限制为 `700`；SSH 密钥、`authorized_keys` 和 CA 私钥的权限会限制为 `600`。启动时只检查 Tailscale CLI 和本地 SSH banner；不会在每次启动时测试远程公钥登录。重启时，超过 1 MiB 的日志会轮换并只保留一代。进程持续运行期间，日志大小没有上限。

探测进程时即使无法访问 `/proc` 中的部分条目，脚本也会继续；如果 CLI 能够响应，则会复用现有的 tailscaled。对于无法识别的守护进程套接字，脚本不会自动删除。

## 保护生成的文件

不要将 Tailscale 状态、主机和客户端私钥、shim CA 私钥、身份验证密钥文件、生成的备份或运行时日志提交到 Git。不要将一个 Tailscale 节点的状态复制到多个同时运行的 VM。`/workspace` 并非自动私密的位置；只能使用你能控制访问权限的目录。

<a id="current-validation"></a>

## 当前验证情况

![Dots 桌面终端中显示安装文件、状态和 Tailscale 版本的截图](assets/dots-tailscale-ssh-verification.png)

这张由维护者提供的截图展示了 Dots 桌面终端中现有安装的文件和状态，以及 Tailscale 版本 `1.102.4`。截图并未展示新安装或 VM 替换。

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

- 维护者此前观察到安装连续运行超过 24 小时。环境替换后，`/workspace` 中的安装文件仍被保留，并通过手动运行 `start.sh` 成功重启。
- 2026-10-14，修订版 shim 完成了实时 DERP ping，以及在严格主机密钥验证下的 SSH 公钥登录和命令检查。该修订版之后是否经历过 VM 替换并成功恢复，尚未得到观察。
- `check` 会报告文件和本地状态。`running: Tailscale and local SSH ready` 表示 backend 为 Running，且本地 SSH banner 有响应；这并不能验证远程公钥登录。
- Auth Key 注册和通过 Dots 自动化启动尚未经过测试。

## 离线回归检查

在仓库检出目录中运行 `python3 -m unittest discover -s tests -v` 和 `bash -n tailscale-setup.sh`。入门测试需要 Git、Bash、Python 3，以及 OpenSSH 客户端和服务器工具。测试会使用一次性仓库、密钥、`known_hosts`、模拟的 Tailscale CLI 和隔离的 Unix 套接字。SSH 通过本地 stdio 运行，不会开启监听器；这些测试不会向 tailnet 进行身份验证、测试实时代理，也不会验证 VM 替换。

## 故障排查

- `cannot inspect process`（旧版本）或 `process inspection failed`（当前版本）：当前脚本会跳过无法访问的部分 `/proc` 条目。请按照上文的[更新步骤](#restart-update)，用经过审查的代码更新已安装的 `setup.sh`。
- `Permission denied (publickey)`：检查 `sshd/authorized_keys` 中公钥的指纹，并按需添加客户端公钥。`running` 消息并不能确认远程公钥身份验证成功。
- `existing file differs`：已安装文件与源文件不一致。检查配置和密钥，然后只更新需要更新的文件。不要删除整个安装目录，其中还保存着 state 和主机密钥。
- `socket exists but daemon cannot be identified`：检查现有守护进程和套接字。脚本不会自动删除无法识别的套接字。
- 粘贴后出现 `^[[200~` 等终端控制文本：取消命令，然后逐行输入。继续前请检查命令内容和主机密钥指纹。
