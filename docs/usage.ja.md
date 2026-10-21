# 使用ガイド

[English](usage.md) · [한국어](usage.ko.md) · [简体中文](usage.zh-CN.md) · 日本語 · [プロジェクト概要](../README.ja.md)

この手順は、環境の入れ替え後も `/workspace` が保持される環境で、ユーザー権限でインストールする場合を対象とします。保存済みの認証情報が有効な間は、保存済みのインストールファイル、認証状態、SSH ホスト鍵を再利用し、新たにログインせずに復旧できます。インストール時に自動起動フックは登録されないため、新しい環境ごとに start コマンドを実行してください。

## 公開リリース

公開リポジトリの初回 clone と raw ファイルのダウンロードは、準備済みのソースと照合して検証しました。[Release v0.4.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.4.0) には ZIP と tar.gz のアーカイブに加えて、`tailscale-setup.sh`、`shim.py`、`dots-ts`、`dots_proxy.py` の個別ファイルと `source-manifest.json` が含まれています。インストール例ではソースを `v0.4.0` に固定しています。`SHA256SUMS` にリリースファイルのチェックサムを収録しています。同梱される Tailscale のバージョンは引き続き `1.102.4` です。

<a id="download-proxy"></a>

## ダウンロード接続とプロキシ設定

2026-10-15 に検証した Dots SSH 環境では、GitHub リポジトリ、raw ファイル、リリースアセットの各 URL への HTTPS 直接リクエストは失敗しましたが、`http://browser-proxy:8889` を明示的に使うと成功しました。プロキシ経由で clone した際、`v0.2.0` は commit `643484` に解決され、ダウンロードした raw ファイルはそのリビジョンのチェックサムと一致しました。この結果はその環境に限ったものであり、GitHub 全体がブロックされていることを示すものではありません。ブラウザーからのダウンロードは別途確認してください。ここに示すターミナルでの確認では、ブラウザーのエラーは診断できません。

clone やダウンロードに失敗した場合は、影響を受けているターミナルで次の範囲を限定した確認を実行し、プロキシ経由の接続を調べます。レスポンス本文は破棄し、リダイレクト後の署名付きダウンロード URL を表示せずに、最終 HTTP ステータスだけを出力します。

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

直接接続との比較では、同じコマンドの `--proxy http://browser-proxy:8889` を `--noproxy '*'` に置き換えます。curl の終了コード `7` は接続失敗、`28` は制限時間への到達を意味します。どちらの結果も、それだけでホスト名がブロックされている証拠にはなりません。証明書の検証は有効のままにし、異なる場合は実際の環境で提供されているプロキシアドレスを使ってください。

設定ごとに適用範囲が異なります。

| 設定 | 適用範囲 |
| --- | --- |
| `HTTPS_PROXY` / `HTTP_PROXY` を `git clone` または `git fetch` の前に指定 | その Git コマンドだけに適用。Git のグローバル設定は変更しない |
| curl の `--proxy` | そのダウンロードまたは診断リクエストだけに適用 |
| インストーラーの `UPSTREAM_PROXY` | インストール中の Tailscale パッケージ／チェックサムのダウンロード、および起動中の shim の上流プロキシに適用 |
| `127.0.0.1:18089` のローカル shim | プロセス環境を通じて、起動した Tailscale デーモンが使う TLS/WebSocket ブリッジ |

ローカル shim はグローバルプロキシを設定せず、無関係なアプリケーションを Tailscale 経由にもしません。環境で異なる上流アドレスを使う場合は、インストール時とその後の起動時に `UPSTREAM_PROXY` を明示してください。

## インストールと起動

セットアップスクリプトと `shim.py` を一緒に使えるよう、リポジトリを `https://github.com/abruption/dots-tailscale` から `/workspace` に clone します。clone には Git と、クラウドコンピューターのプロキシ経由で GitHub HTTPS にアクセスできることが必要です。セットアップには Linux x86_64、Python 3、`timeout`、`flock`、OpenSSL、`ssh-keygen`、`/usr/sbin/sshd` が必要です。インストール時にはさらに curl、tar、sha256sum が必要です。現在のユーザーがポート 22 をバインドできる必要があります。root や systemd は必要ありません。

v0.4.0 では、ソースファイル 4 つと `source-manifest.json` を同じ場所に置いてください: `tailscale-setup.sh`、`shim.py`、`dots-ts`、`dots_proxy.py`。旧版 v0.2.0 のインストールでは元の 2 ファイルだけを使い、これらのヘルパーは含まれません。

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.4.0 --depth 1 'https://github.com/abruption/dots-tailscale' dots-tailscale
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

このプロキシ設定は clone コマンドにのみ適用され、Git のグローバル設定は変更しません。アドレスが異なる場合は、環境で指定されているプロキシを使ってください。HTTPS 証明書の検証は有効のままにしてください。

`check` は選択したインストールファイルの有無を一覧表示し、デーモンソケットと CLI を利用できる場合は Tailscale の状態も表示します。コンポーネントを起動したり、依存関係を確認したりはしません。ファイルがないだけでは終了コードは非ゼロになりません。未インストールの環境では `missing:` の行を表示して終了コード `0` で終わることがあります。起動の成功と実際の SSH 接続で準備状況を判断してください。

`install` はプロセスを起動せずにファイルを配置します。既存のバイナリは保持されます。設定ファイルまたはスクリプトが競合している場合は、手動確認のためインストールを停止します。ダウンロードする Tailscale アーカイブはスクリプトに記載されたバージョンに固定され、公式の SHA-256 ファイルと照合されます。

`login` は不足しているコンポーネントを起動し、180 秒の制限時間内で対話形式の Tailscale 認証を行います。表示された URL を開き、対象のアカウントと tailnet を承認してください。スクリプトに auth key は埋め込まれていません。ファイルを使う任意の認証方法は後述します。

認証後、不足しているコンポーネントを起動するには次のコマンドを使います。

```bash
bash /workspace/.dots-tailscale/start.sh
```

確認や明示的な認証を行うには、インストール済みラッパーに `check` または `login` を渡します。インストール時にインストールディレクトリへ `start.sh` が生成され、`tailscale-setup.sh` は `setup.sh` としてコピーされます。生成されたラッパーは自身の場所からディレクトリを特定し、同じ場所にある `setup.sh` を実行します。既存のインストールを更新する場合は、下記の[更新手順](#restart-update)を使ってください。

| 設定 | 既定値 |
| --- | --- |
| `DOTS_TS_DIR` | `/workspace/.dots-tailscale` |
| `UPSTREAM_PROXY` | `http://browser-proxy:8889` |
| `NODE_HOSTNAME` | `dots-sandbox` |

別の `DOTS_TS_DIR` を指定する場合は、空白を含まない絶対パスにしてください。`UPSTREAM_PROXY` には認証情報を含まない HTTP プロキシ URL を指定します。インストール時とその後の起動時で同じ設定を使います。shim は現在ポート 18089、SSH はポート 22 で待ち受けるため、この構成で同時に実行できるインストールは 1 つだけです。

### SSH 鍵と接続準備

SSH は `127.0.0.1:22` で待ち受け、公開鍵認証を使います。Tailscale SSH は無効で、シェル接続はローカルの OpenSSH サーバーが処理します。HOME に `.ssh/authorized_keys` がすでにある場合、インストール時に永続化されるインストール先へコピーされます。ない場合は、クライアントの公開鍵を次のファイルに追加してください。

```text
/workspace/.dots-tailscale/sshd/authorized_keys
```

クライアントの公開鍵を1行まるごと追記し、ファイルのモードを `600` にします。クライアントでは `.pub` ファイルから公開鍵を取得してください。秘密鍵を転送してはいけません。クライアントで受け入れる前に、クラウドコンピューター上でサーバーのホスト鍵フィンガープリントを確認してください。

```bash
ssh-keygen -lf /workspace/.dots-tailscale/sshd/ssh_host_ed25519_key.pub
```

Dots ターミナルで、インストール済みラッパーを使ってノードアドレスを取得します。

```bash
bash /workspace/.dots-tailscale/start.sh check
```

インストール済み CLI と、ユーザーごとの `/tmp/dots-tailscale-$(id -u)/tailscaled.sock` を使用します。グローバルな `tailscale` コマンドや既定のソケットは必要ありません。アドレスの表示にはデーモンが起動済みである必要があります。カスタムインストールの場合は、そのインストール先にある `start.sh` を使ってください。

初回接続では、tailnet のクライアントで次のコマンドを実行します。ホスト鍵の確認プロンプトで、表示されたフィンガープリントを Dots ターミナルで別途取得した値と比較してください。一致しない場合は `no` と答えて中止してください。鍵を登録したり、既存の登録を削除したりしないでください。`ssh-keyscan` の出力だけを信頼してはいけません。値が一致した場合に限って受け入れてください。これによりクライアントの `known_hosts` ファイルに鍵が記録されます。

```bash
ssh -o StrictHostKeyChecking=ask -i /path/to/client_private_key 'agent@<tailscale-ip>' 'id; pwd'
```

検証済みの鍵が記録されたら、以降の接続では厳格な確認を使います。

```bash
ssh -o StrictHostKeyChecking=yes -i /path/to/client_private_key 'agent@<tailscale-ip>' 'id; pwd'
```

クラウドコンピューター上のユーザー名が異なる場合は、例のユーザー名を置き換えてください。デーモンの起動中に `start.sh check` が表示したアドレスを使います。`StrictHostKeyChecking=yes` は信頼済みのホスト鍵を要求し、新しい鍵を対話形式で受け入れることはできません。鍵が変わった場合は、保存済みのエントリーを変更する前に新しいフィンガープリントを別途確認してください。ホスト鍵の検証を回避してはいけません。

`running: Tailscale and local SSH ready` は、backend が Running でローカル SSH バナーが応答したことを意味します。クライアント鍵での認証成功を示すものではありません。実際に SSH コマンドを実行して別途確認してください。

| 起動結果 | 終了コード |
| --- | --- |
| Running かつローカル SSH 準備完了 | `0` |
| 認証が必要 (`needs_auth`) | `10` |
| backend の準備期限切れ、またはその他の利用不可状態 | `11` |
| 期限までに有効な backend 状態を取得できなかった | `12` |
| 管理者によるデバイス承認が必要 (`needs_machine_auth`) | `13` |
| backend が Running のときに明示的な認証コマンドが失敗 | 元のコマンドの失敗コード |
| セットアップまたはコンポーネントの失敗 | 非ゼロ。通常は `1` |

backend の準備確認では、各フェーズにつき単調時計で計る30秒の期限を設け、再試行とスリープもその中に含めます。各ステータス照会は最大3秒で、残り時間を超えません。`NoState` と `Starting` は再試行します。有効な一時状態が一度でも確認されていれば、その後の照会に失敗しても期限切れは終了コード `11` です。フェーズ中に有効な状態を一度も取得できなかった場合は `12` です。CLI との通信に成功しただけでは準備完了ではありません。この期限が適用されるのは backend のフェーズであり、すべてのコンポーネント初期化や明示的な認証を合わせた時間ではありません。`login` と `auth` は最初に最大30秒間、有効なローカル API 応答を待ちます。この事前確認では Running を要求せず `NoState`/`Starting` も許容するため、未認証の新しいデーモンでも明示的な `up` を実行できます。`login` には別途180秒、`auth` の認証には別途60秒の制限があり、その後に最終的な backend 準備確認を行います。デバイス承認がすでに保留中の場合は `up` を省略します。`up` が失敗またはタイムアウトしても backend を確認します。NeedsMachineAuth は `13`、NeedsLogin は `10` に対応します。backend が Running でもコマンドの失敗を隠しません。新しい起動時のステータス確認は最大3秒です。

通常の `start` は login を実行しません。認証済みだった state が消失した場合は、ID を黙って置き換えず、手動確認のため失敗します。競合する既存ファイル、不完全な CA インストール、すでに設定済みのホスト鍵の欠落についても確認が必要です。スクリプトを更新するためにインストールディレクトリを削除しないでください。ノードの state と秘密鍵が含まれています。

カスタム TLS/WebSocket shim は、設定されたローカル HTTP CONNECT ブリッジを実装します。ローカルテストでは、分離したソケットを使って初期ペイロードの保持と通信境界を確認しています。修正版 shim は2026-10-14に展開され、プロキシ経由で実環境の DERP ping と、厳格な公開鍵 SSH 接続の確認に成功しました。以前の shim では、メンテナーが環境入れ替え後もインストールファイルが保持され、手動再起動に成功したことを確認しています。修正版で、その後の入れ替えを経た復旧はまだ検証していません。プラットフォームのプロキシに変更があると動作しなくなる可能性があります。

このリポジトリには VM cron、systemd サービス、自動起動登録はありません。Dots オートメーション経由の起動も未検証です。VM 内のプロセスループは、その環境が破棄されると存続できません。新しい環境でインストールを明示的に起動するか、別途検証済みのプラットフォーム実行手段を使ってください。

<a id="package-version"></a>

## パッケージのバージョンとファイルの確認

v0.4.0 に含まれる機能です。新しいリリースを公開しても、既存のインストールは自動で更新されません。

```bash
/workspace/.dots-tailscale/dots-ts version
/workspace/.dots-tailscale/dots-ts version --json
```

GitHub や tailnet には接続せず、ローカルのパッケージファイルを確認します。マニフェストに記載されたパッケージのバージョン、ソースファイルの一致状況、インストール済み Tailscale バイナリのバージョンを別々に表示します。ソースのリビジョンが不明なら、そのまま不明として扱います。マニフェストとの一致はファイルの整合性を示しますが、配布元の本人確認や、動作中のサービスが正常であることの証明にはなりません。

確認済みの同じリビジョンから取得したソースファイル 4 つと `source-manifest.json` を一緒に用意してください。新しいインストーラーはこの組み合わせを検証し、マニフェストをインストール先に保存します。不一致を消すために、変更済みのインストールファイルから期待するハッシュを作り直さないでください。メタデータのない既存の v0.2/v0.3 環境では、パッケージのバージョンは不明になります。Tailscale のバージョンだけでは判別できません。

更新やロールバックの際は、ソースファイルと対応する元のマニフェストをセットで保管し、一緒に入れ替えてください。古いコードに新しいマニフェストだけを上書きしないでください。ノードの状態、秘密鍵、SSH の識別情報、プロキシ設定は保持します。以下の手動更新の注意事項も引き続き適用されます。このコマンド自体は更新や再起動を行いません。

JSON では `package.declared_version`、`package.source_revision`、`package.integrity` とファイルごとの結果を、`tailscale.version`、`tailscale.status` と分けて表示します。整合性は `match`、`mismatch`（変更または異なるバージョンの混在）、`incomplete`（ファイルの欠落または読み取り不可）、`unknown`（メタデータの欠落、不正または読み取り不可）のいずれかです。`source_revision` は現在 `null` で、コミットを推測しません。mismatch/incomplete の終了コードは `1`、それ以外は `0` です。終了コード `0` だけではバージョンが判明したことや Tailscale バイナリが利用可能であることを示しません。`doctor` はメタデータが不明なら degraded、ファイルの不一致や不足は unhealthy と表示します。

メンテナンス時は、確認済みのソース変更後に `python3 scripts/source_manifest.py` を実行し、差分を確認してから `python3 scripts/source_manifest.py --check` で検証します。CI はファイルを書き換えず、検証のみ行います。生成は確認済みのソースチェックアウトで行い、インストール先では行わないでください。リリースにはソースファイル 4 つに対応するマニフェストも含める必要があります。既存の v0.3.1 リリースには含まれていません。

<a id="update-management"></a>

## リリース確認とローカル通知

これらのコマンドは v0.4.0 に含まれ、このバージョンのヘルパーが必要です。リリースを公開しても、既存のインストールは更新されません。リリース確認では `abruption/dots-tailscale` の安定版のうち、ソースファイル 4 つと一致する `source-manifest.json` を含むものだけを対象にします。このマニフェストを含まない古いリリース（v0.3.1 を含む）は、検証済みの更新用バンドルとして扱わず、非対応として報告します。

```bash
/workspace/.dots-tailscale/dots-ts update check --json
/workspace/.dots-tailscale/dots-ts update check --tag v0.4.0 --proxy http://browser-proxy:8889 --json
```

2つ目の例は特定の安定版を選択します。そのリリースが公開された後にのみ使用できます。`--proxy` を指定しない場合、確認では直接 HTTPS を使い、シェルのプロキシ認証情報を引き継ぎません。必要な場合は、プラットフォームのダウンロード用プロキシを明示的に指定してください。shim 用およびアプリケーション用の SOCKS/HTTP プロキシとは別の設定です。HTTPS 証明書の検証は有効のままです。GitHub トークンは不要です。

`update check` はインストール済みのコード、ノード state、設定、プロセスを変更しません。JSON の `status` は `update_available`、`current`、`local_newer`、`not_comparable`、`check_failed` のいずれかです。`selection` は最新バージョンを選んだか、バージョンを固定して選んだかを示します。終了コードは、比較が有効なら `0`、ローカルのインストールを比較できない場合は `1`、検索に失敗した場合は `2` です。終了コード `0` だから更新があると決めつけず、status を確認してください。リモートのリリースが見つかっても、不明、変更済み、または異なる版が混在するローカルソースが信頼済みのバージョンになるわけではありません。

通知は明示的に有効にした場合だけ使われ、ローカルで処理されます。更新をインストールせず、タイマーを登録せず、外部メッセージも送信しません。

```bash
/workspace/.dots-tailscale/dots-ts update notifications configure --enable --interval-hours 24
/workspace/.dots-tailscale/dots-ts update notifications refresh --proxy http://browser-proxy:8889
/workspace/.dots-tailscale/dots-ts update notifications show
/workspace/.dots-tailscale/dots-ts update notifications notify
/workspace/.dots-tailscale/dots-ts update notifications configure --disable
```

`refresh` は時間を制限したネットワーク確認を行い、結果をローカルに保存します。設定済みの間隔に従い、間隔が経過する前に再確認する場合は `--force` を明示的に指定します。`notify` はキャッシュだけを読み取り、有効期限を確認し、同じバージョンの通知を繰り返しません。対話型の起動が成功すると、短い時間制限内に stderr へキャッシュ済み通知を表示する場合があります。通知確認が失敗しても起動結果は変わりません。生の `cli` 転送や JSON 出力には通知文を追加しません。保存先が読み取り専用の場合や `refresh` に失敗した場合、古い結果を新しい確認の成功として扱うことはありません。記録時刻と期限切れ／エラー状態は `show` で確認してください。定期的な `refresh` には、別途明示的に設定した呼び出し元が必要です。VM が停止している間に VM 内のコマンドが実行されることはありません。

### 確認済みバンドルによる手動更新

インストール先、ランタイムソケット、Linux のプロセス情報を参照できる環境で実行してください。`update apply` には Linux、Python 3.9 以降、pidfd のサポートが必要です。開始前に、クラウドコンピューターを操作できる独立したターミナルを開いてください。`--confirm-recovery-terminal` は確認したことを記録するだけで、そのターミナルが利用可能かを検査するものではありません。トランザクションワーカーは呼び出し元のセッションから切り離されるため、SSH 経由で処理を開始できますが、SSH 接続自体は復旧手段になりません。

確認済みの安定版を固定して選びます。

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.4.1 --proxy http://browser-proxy:8889 --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts update status --id TRANSACTION_ID --json
```

これは使用例であり、v0.4.1 の公開を告知するものではありません。`TRANSACTION_ID` は apply が返す ID に置き換えてください。リリースのダウンロードがブロックされている場合や、未公開候補を明示的に試す場合は、確認済みのローカルソースバンドルも使用できます。

```bash
/workspace/.dots-tailscale/dots-ts update apply --bundle /workspace/reviewed-bundle --confirm-recovery-terminal --json
```

バンドルにはソースファイル 4 つと、それらに対応する元のマニフェストが必要です。ローカル選択では開発版も指定できますが、公開済みの GitHub リリースとしては扱いません。更新ツールはファイルを置き換える前にハッシュと構文を検証します。チェックサムが一致することは選択したバンドルとの整合性を示しますが、作成者を独立に信頼できることの証明にはなりません。

`started` は切り離されたワーカーが処理を受け付けたことを示し、更新の成功を意味しません。再接続した後に `update status` を確認してください。最終状態は `applied`、`failed_rolled_back`、`rollback_failed_manual_required` を区別します。ローカルの準備状態とバージョン確認だけでは外部からの SSH 接続を証明できません。`applied` またはロールバック成功後に、新しい SSH 接続を厳格なホスト鍵確認付きで開き、目的の接続を別途テストしてください。

ワーカーは、setup と proxy apply が使うものと同じ操作ロックを保持したまま、4 つのソースファイルとそのマニフェストだけをバックアップして置き換えます。再起動するのは特定済みの shim プロセスだけです。tailscaled と sshd は動作したままです。ノード state、ログイン資格情報、秘密鍵、SSH 設定、プロキシ設定をコピーしたり初期化したりしません。同梱 Tailscale バイナリのバージョンを変更するリリースは、この更新ツールの対象外として拒否されます。識別できないプロセスを終了したり、ソケットを削除したりしません。

インストール済みのマニフェストが一致している必要があります。メタデータのない古いインストールでは、インストール済みの 4 ファイルすべてを、別途入手して確認したリリースと照合してください。インストール済みファイル自身から期待するチェックサムを生成しないでください。出所を確認できない場合や、異なるソースが混在している場合は手動確認のため中止します。既存インストールに対する意図的な移行でソースチェックアウトを使う場合も、更新ツールを実行する前に、そのチェックアウトが自身のマニフェストと一致している必要があります。

未完了のトランザクションがあると、新しい更新は開始できません。ワーカーの強制終了後や VM の入れ替え後は、独立したターミナルから状態とコードのみのバックアップを確認してください。再試行のためにジャーナルを削除したり、次の処理が安全だと決めつけたりしないでください。ロールバック後も準備状態を確認できない場合は、証拠を保持し、確認済みのバックアップから手動で復旧してください。この更新ツールは VM を自動復旧するサービスではありません。

トランザクションの記録は `.update-transactions/TRANSACTION_ID/` に保存されます。`journal.json` は進行段階、`stage/` は選択したバンドル、`backup/` は以前のソースのみを保持し、`supervisor/` には updater のコードのスナップショットを保存します。外部から接続を確認するまでは記録を残してください。`update status` の終了コードは、実行中または適用済みなら 0、拒否、ロールバック、中断、手動復旧が必要な場合は 1 です。実行中の終了コード 0 は完了を意味しません。

`applied` は、選んだコードがマニフェストと一致し、ローカルの準備状態の確認を通過したことを表します。shim の CONNECT/WebSocket 通信全体を検証した結果ではありません。再起動する shim には upstream のホスト・ポートと絶対パスの SSL 証明書設定を引き継ぎますが、その他のシェル環境やデバッグ設定は引き継ぎません。stdout/stderr は破棄するため、以前の `shim.log` への新しい出力を期待せず、トランザクションの固定エラーコードと別途行う接続確認を使ってください。

中断したトランザクションは、証拠を削除せずに手動復旧を完了できます。

1. 独立したターミナルで worker が終了したことを確実に確認します。PID が見えないことや status が不明なことだけでは、終了した証拠になりません。
2. 必要なら確認済みのコードバンドルを復元して起動し、マニフェスト、ローカルの準備状態、新しい外部 SSH 接続を厳密なホスト鍵検証付きで確認します。古いヘルパーでは、別途確認済みのソースランナーによるマニフェスト確認が必要な場合があります。
3. worker が動いておらず、インストール先の `.operation.lock` を取得した状態で、確認済みのトランザクションディレクトリ全体を `.update-transactions/` の外にある、自分が所有する権限 700 の `.update-recovery-archive/` へ移します。記録とバックアップは一緒に残してください。これで未完了トランザクションの検索対象から外れます。apply の制限を回避するために、実行中または未確認の記録を移してはいけません。

確認済みの開発候補を 2026-10-21 に Dots で試験しました。通常の適用、意図的な起動失敗からのロールバック、外部 SSH の確認結果と範囲は[更新の検証記録](validation/v0.4.0-update-trial.md)を参照してください。

<a id="development-helpers"></a>

## v0.4.0 ヘルパーと任意のアプリケーションプロキシ

これらのコマンドは v0.4.0 と v0.3.1 に含まれます。v0.2.0 では利用できません。`dots-ts` は Python 3 の標準ライブラリを使い、自身の場所からインストール先を特定します。グローバルな `tailscale` コマンド、ソケットのシンボリックリンク、シェルプロファイルの変更は不要です。

```bash
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts doctor --json
/workspace/.dots-tailscale/dots-ts doctor --target api.example.internal:8443
```

`cli` はインストールのランタイムソケットを使って Tailscale CLI を実行し、引数をそのまま渡します。生の出力と対話動作を保ち、ヘルパー独自のタイムアウトはありません。アドレスなどの機密情報を含む CLI 出力が表示されることがあります。長時間実行するコマンドには独自の制限を設けてください。単独ではログインもデーモン起動もしません。

`doctor` は読み取り専用です。プロセスの起動、認証、ファイル修復、プロキシ設定の変更はしません。すべてのアプリケーションを準備完了と判定するのではなく、層ごとにローカル確認結果を機密情報を伏せて報告します。`overall` と各結果を確認してください。`degraded` でも終了コードが `0` の場合があります。`--target HOST:PORT` を指定すると、アプリケーションの HTTP/SOCKS リスナーではなく、ローカルの Tailscale デーモン経由で範囲を限定した TCP 到達性確認も行います。資格情報は送信せず、TLS、API 認証、認可も証明しません。ターゲット名の指定は明示的なネットワークリクエストなので、接続する意図のあるサービスを選んでください。アプリケーションの確認方法と検証一覧は、[プライベート HTTP API の利用例](private-http-api.ja.md)を参照してください。

### 実行コンテキストの診断

`doctor` が示すのは、現在の実行コンテキストと、そこで見えるプロセスだけです。追加された JSON の `context` オブジェクトには、インストール／ランタイムの可視性、メタデータに基づく読み書き権限、ファイルシステムの `read_only`/`read_write`/`unknown` 状態が含まれます。これらはメタデータとアクセス権の確認であり、実際の書き込みは行いません。`advice` には固定のガイダンス ID が入り、`skipped_checks` は未実施の確認項目を示します。人向けの出力にはガイダンスの説明が表示されます。

ソケットが見つからない場合でも、デーモンは別の場所で動作しており、単に現在のコンテキストから見えないことがあります。その場合も現在のコンテキストについては `unhealthy`、終了コード `1` となります。アクセス拒否や応答しないソケットも失敗です。確認結果が不明な場合や、サービスが正常でもインストール先が読み取り専用の場合は `degraded`、終了コード `0` となります。`healthy` が true になるのは `overall=healthy` の場合だけです。終了コード `0` だけで準備完了と判断せず、各フィールドを確認してください。ソケットのメタデータと接続結果では、欠落、アクセス拒否、利用不可を区別します。

インストール／ランタイムが読み取り専用またはアクセス不可の場合、あるいはソケットが欠落または応答しない場合は、クラウドコンピューターの独立した GUI ターミナルを開き、そこで `doctor` を実行してください。再インストール、ログイン、権限変更、state のリセットが必要だと決めつけないでください。ユーザー ID が同じでも、ファイルやソケットの可視性が同じとは限りません。この診断は別の namespace に入ったり、マウントを変更したりしません。

### プロキシの設定と適用は別々に行う

```bash
/workspace/.dots-tailscale/dots-ts proxy show
/workspace/.dots-tailscale/dots-ts proxy configure --socks-port 1080 --http-port 8080
```

設定コマンドは `/workspace/.dots-tailscale/outbound-proxy.json` に loopback リスナーの意図した設定を記録します。デーモンを再起動せず、リスナーもすぐには利用可能になりません。SOCKS5 と HTTP プロキシは既定で無効です。2つのリスナーには別々のポートを使ってください。ネイティブ Tailscale では同じポートを使えますが、この実装では当初は共有ポートを拒否します。設定は `/workspace` に保持されますが、環境の入れ替え後にはソケットと実行中プロセスを作り直す必要があります。

設定の変更を適用するにはデーモンを明示的に再起動する必要があり、Tailscale SSH が切断される可能性があります。最初に独立した Dots GUI ターミナルを確保し、現在の設定と接続状況を記録して、ロールバックを準備してください。そのターミナルから `/workspace/.dots-tailscale/dots-ts proxy apply` を実行します。`SSH_CONNECTION` または `SSH_CLIENT` が設定されている場合、変更の適用は拒否されます。すでに適用済みの設定なら何も行いません。置き換えに失敗してロールバックが成功すると、以前のデーモンが復元され、保存した設定は未適用のまま残ります。既存の SSH セッションを復旧手段とみなしたり、ローカル再起動の成功だけでリモート接続も可能だと判断したりしないでください。デーモンの状態、ローカル SSH、外部からの厳格な鍵確認を使った SSH、対象アプリケーションを再確認し、失敗した場合は GUI ターミナルからロールバックしてください。`proxy configure` だけでは本番環境にプロキシは展開されません。

```bash
/workspace/.dots-tailscale/dots-ts proxy disable
```

`disable` は無効状態の設定を保存します。すでに稼働しているリスナーは、変更を明示的に適用するまで残ります。無効化や適用によってノードの state、鍵、インストールが削除されることはありません。

アプリケーション用プロキシは、明示的に設定されたローカルクライアント向けに loopback で待ち受けます。これはデーモン用 TLS/WebSocket shim の `127.0.0.1:18089` とは別のものです。その shim をアプリケーション用 HTTP プロキシとして使わないでください。個々の curl リクエストまたは子プロセスにプロキシを設定してください。グローバルな環境設定、ブラウザー、MCP、ChatGPT コネクターには伝播しません。loopback プロキシに接続できるローカルプロセスは利用を試みられるため、ネットワーク方針と API 認証は引き続き必要です。

### Git を使わずに v0.4.0 をインストール

ソースファイル 4 つと `source-manifest.json` を別のソースディレクトリにダウンロードします。1つでもリクエストに失敗したら中止し、インストール前にファイルとハッシュを確認してください。

```bash
(
  set -euo pipefail
  mkdir -p /workspace/dots-tailscale-v0.4.0
  cd /workspace/dots-tailscale-v0.4.0
  DOTS_TS_REF=v0.4.0
  for file in tailscale-setup.sh shim.py dots-ts dots_proxy.py source-manifest.json; do
    curl -fsSL --proxy http://browser-proxy:8889 --connect-timeout 5 --max-time 60 \
      "https://github.com/abruption/dots-tailscale/releases/download/${DOTS_TS_REF}/${file}" \
      -o "$file"
  done
  sha256sum tailscale-setup.sh shim.py dots-ts dots_proxy.py source-manifest.json
)
```

すべてのダウンロードに成功し、ファイルの確認を終えたら、新規インストールには次のコマンドを実行します。

```bash
cd /workspace/dots-tailscale-v0.4.0
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

セットアップを実行する前に、ソースファイル 4 つと `source-manifest.json` を同じディレクトリに置いてください。リリースアセットのホストを利用できない場合は、同じタグとファイル名で `https://raw.githubusercontent.com/abruption/dots-tailscale/v0.4.0/` から取得するか、信頼できる別の端末から確認済みのファイルを転送します。既存の v0.4.0 環境では[更新管理](#update-management)を使い、旧版環境では[従来の手動手順](#restart-update)を確認してください。`install` は内容が異なるインストール済みソースファイルを上書きしません。

### 任意の非対話認証

ブラウザーを使わず認証する必要がある場合は、Tailscale auth key を非公開のファイルで渡し、明示的に `auth` を実行します。

```bash
chmod 600 /workspace/.dots-tailscale/auth.key
DOTS_TS_AUTH_KEY_FILE=/workspace/.dots-tailscale/auth.key \
  bash /workspace/.dots-tailscale/start.sh auth
```

安全な方法で別途キーファイルを作成してください。キーをチャット、ソースコード、コマンドライン引数、Git に貼り付けないでください。`DOTS_TS_AUTH_KEY_FILE` には、現在のユーザーが所有する通常ファイルの絶対パスを指定します。シンボリックリンクは不可で、権限は `600` または `400` でなければなりません。キーは `--auth-key=file:PATH` として Tailscale に渡されます。

backend がすでに Running の場合、`auth` はキーファイルを読み取らず、また要求もせずに認証を省略します。認証済みを示すマーカーがある状態で state が失われている場合は、このモードでも停止し、既存のノード ID を黙って置き換えることはありません。スクリプトは tailnet のタグや ACL 方針を変更せず、再認証を強制せず、設定をリセットしません。auth コマンドにはプロセスの60秒、接続の45秒の制限があります。ブラウザー認証へのフォールバックはありません。auth key による実際の登録は未検証です。通常の `start` が auth key に自動で切り替わることもありません。

## HOME の継続性を確認する

Dots のリポジトリ checkout から次の probe を実行します。

```bash
python3 /workspace/dots-tailscale/home-persistence-probe.py
```

初回実行では `$HOME/.dots-home-persistence-marker.json` にランダムなマーカーを作成し、`/workspace/.dots-persistence-probe` の下に baseline、workspace マーカー、最新結果、履歴を保存します。以降の実行では最初の baseline と比較します。probe が読むのは自身のマーカーとプロセスのコンテキストであり、認証 state やユーザーのドキュメントは読みません。

| `assessment` | 意味 |
| --- | --- |
| `baseline_created` | 初回の観測を記録 |
| `markers_intact` | 2つのマーカー ID が baseline と一致 |
| `home_marker_changed_workspace_intact` | workspace マーカーはそのままで、HOME マーカーが変化または消失 |
| `marker_continuity_changed` | その他のマーカー継続性の変化 |

マーカーが失われたことはマーカーの消失を示しますが、HOME ディレクトリ全体がリセットされた証拠ではありません。hostname、boot ID、PID 1 の開始時刻は状況判断の材料になります。boot ID だけではコンテナーの入れ替えとホストの再起動を区別できません。workspace の baseline も消失した場合、次回実行時に新しい baseline が作られるため、比較用の結果を VM の外にも保存してください。probe を SSH 経由で実行し、結果をクライアント側に保存することもできます。

```bash
ssh agent@dots-sandbox 'python3 /workspace/dots-tailscale/home-persistence-probe.py' > observation.json
```

SSH キーとホスト別名は自分の設定に合わせてください。結果には hostname とマーカー ID が含まれるため、公開前に匿名化してください。

カスタムパスは `--home` と `--probe-root` で指定できます。この probe の定期実行は登録されません。

<a id="restart-update"></a>

## v0.2/v0.3 の従来の手動更新

この節は、パッケージのメタデータがない旧版環境向けの手動手順です。v0.4.0 では、ソースファイル 4 つとマニフェストを一緒に扱う[検証付き手動更新ツール](#update-management)を使ってください。動いているブランチからインストール済みファイルを直接更新するのではなく、選択した commit または release を使い、`tailscale-setup.sh` と `shim.py` を `/workspace/.dots-tailscale` の外で一緒に準備して確認します。そのバージョンと2つの SHA-256 ハッシュを記録してください。インストールを変更する前に、準備した shell の構文を検証し、Python ソースを解析してください。

v0.3.1 に更新する場合は、同じ commit の `dots-ts` と `dots_proxy.py` も準備します。下の互換性ブロックが検証するのは元の setup/shim の組み合わせだけで、新しいヘルパーは対象外です。さらに、2つの Python ヘルパーを実行せずにコンパイルし、4ファイルすべてのソースハッシュを記録してください。展開とロールバックの手順では、`setup.sh` と `shim/shim.py` に加え、`dots-ts` と `dots_proxy.py` もインストールルートに置き、モードを `700` にします。`outbound-proxy.json` とノード ID ファイルは保持してください。文書やコードの更新に付随する作業として、プロキシ設定を適用したりデーモンを再起動したりしないでください。

```bash
python3 -c 'from pathlib import Path; [compile(Path(p).read_text(), p, "exec") for p in ("dots-ts", "dots_proxy.py")]'
sha256sum tailscale-setup.sh shim.py dots-ts dots_proxy.py
```

別の staging checkout を `/workspace/dots-tailscale` に用意します（必要ならパスを変更してください）。`<REVIEWED_COMMIT>` は確認済みの40文字の SHA に置き換えます。このブロックはブランチと detached HEAD のどちらでも、繰り返し実行できます。追跡済み、ステージ済み、未追跡の変更を破棄せず、そのまま検出して停止します。fetch、リビジョン確認、checkout、検証が失敗すると subshell が停止します。ブロック全体が成功した場合に限り展開を続けてください。変更対象は staging checkout だけで、インストール済みファイルや認証 state は変更しません。プロキシ設定は fetch にのみ適用します。証明書検証は有効のままにしてください。

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

shim を変更する場合は、次の手順で展開します。

1. Tailscale SSH とは独立した、クラウドコンピューターのプラットフォーム GUI ターミナルを開きます。復旧に使えるよう、そのターミナルは開いたままにします。インストール済み `setup.sh` と `shim/shim.py` のハッシュ、ノードアドレス、SSH 公開ホスト鍵のフィンガープリントを VM の外に記録します。認証済みの基準状態を確認し、候補バージョンとは別に記録してください。
2. 古い `setup.sh` と `shim/shim.py` のソースファイルだけを、ロールバック用に非公開で保存します。v0.3.1 では既存の `dots-ts` と `dots_proxy.py` も含め、更新前にファイルがなかった場合はその事実を記録してください。認証 state、CA 鍵、SSH ホスト鍵、authorized_keys はそのままにし、別のインストール先へ state をコピーしないでください。プロセス停止前に、時間を区切った切り離し再起動の手順とロールバック手順を準備します。切り離して実行する処理は SSH セッションが切れても続く必要があります。旧版 v0.2/v0.3 のツールには、そのアップデーターは含まれていません。
3. 正確なコマンドラインとインストール済みスクリプトのパスを使って、実行中の shim を特定します。独立したターミナルから、特定した shim の PID だけを停止します。プロセス名を使った広範な終了操作はしないでください。終了したことと、重複する shim が残っていないことを確認します。確認済みの `shim.py` を `shim/shim.py` に、`tailscale-setup.sh` を `setup.sh` に、それぞれモード `700` で配置します。v0.3.1 では確認済みの `dots-ts` と `dots_proxy.py` もインストールルートにモード `700` で配置します。
4. 準備した切り離し起動を実行し、インストール済みソースのハッシュを staging 側と比較します。shim が1つだけであることと、想定されるデーモンプロセスが動作していることを確認します。独立ターミナルで Tailscale CLI の状態とローカル SSH バナーを確認し、続いて外部から DERP 接続と、厳格なホスト鍵検証を使った実際の SSH コマンドを試します。結果を候補バージョンと合わせて記録してください。
5. 検証に失敗した場合は、独立ターミナルから特定済みの候補 shim だけを停止し、古いソースファイルを戻して再起動します。古いハッシュと基準時の接続を再確認してください。ロールバック中も既存 state と鍵を保持し、予期しないホスト鍵の変更があれば受け入れずに調査してください。

更新の近道として `install` を実行したり、インストールディレクトリを削除したりしないでください。インストール処理は意図的に競合ファイルを拒否し、インストール先には ID 情報が含まれます。setup スクリプトだけを更新する場合も、バージョンとハッシュを記録して起動検証を行う必要があります。それだけでは、別途変更した shim が読み込まれたことを確認できません。

制御された再起動の成功で確認できるのは、その環境でプロセスが復旧することです。VM の入れ替え後も保持されることの確認にはなりません。継続性 probe は観測用です。自然な環境変更を待ち、結果を外部に保存して、認証状態が継続したかを別途評価してください。この probe のために強制リセットや再起動を行う必要はありません。

## ファイルの保存場所と SSH の権限

| パス | 内容 |
| --- | --- |
| `/workspace/.dots-tailscale/state` | Tailscale の認証 state |
| `/workspace/.dots-tailscale/shim` | shim、CA、秘密鍵、証明書 |
| `/workspace/.dots-tailscale/sshd` | SSH 設定、ホスト鍵、`authorized_keys` |
| `/tmp/dots-tailscale-$(id -u)` | ユーザーごとのソケット、PID、実行ログ |

同じ Tailscale ノードの state を、同時に稼働する複数の VM にコピーしないでください。`/workspace` は自動的に非公開になる場所ではありません。アクセスを管理できるディレクトリだけを使ってください。SSH サーバーは、親ディレクトリ `/workspace` の権限によって OpenSSH が公開鍵認証を拒否することがあるため、`StrictModes no` を使用します。セットアップは保存ディレクトリをモード `700`、SSH キー、`authorized_keys`、CA 秘密鍵をモード `600` に制限します。起動時の確認対象は Tailscale CLI とローカル SSH バナーまでで、リモートからの公開鍵ログインを毎回試すことはありません。再起動時には 1 MiB を超えるログを1世代だけローテーションします。プロセスが動き続けている間、ログサイズに上限はありません。

`/proc` 内でアクセスできないプロセスがあっても探索を続け、CLI に応答する既存の tailscaled を再利用します。識別できないデーモンのソケットは自動削除しません。

## 生成ファイルを保護する

Tailscale state、ホストとクライアントの秘密鍵、shim の CA 秘密鍵、auth key ファイル、生成したバックアップ、ランタイムログを Git に含めないでください。

<a id="current-validation"></a>

## 現在の検証状況

![Dots デスクトップターミナルでインストールファイル、状態、Tailscale のバージョンを示すスクリーンショット](assets/dots-tailscale-ssh-verification.png)

メンテナー提供のこのスクリーンショットには、Dots デスクトップターミナルの既存インストールファイルと状態、および Tailscale `1.102.4` が写っています。新規インストールや VM の入れ替えを示すものではありません。

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

- 以前のバージョンでは、インストールが24時間を超えて稼働することを確認しています。環境の入れ替え後も `/workspace` のインストールファイルが保持され、`start.sh` による手動再起動に成功しました。
- 2026-10-14、修正版 shim は実環境の DERP ping と、厳格なホスト鍵確認を用いた SSH 公開鍵ログインおよびコマンド実行に成功しました。この版で、その後の VM 入れ替え後の復旧はまだ確認されていません。
- `check` はファイルとローカル状態を報告します。`running: Tailscale and local SSH ready` は backend が Running で、ローカル SSH バナーが応答することを意味します。リモートの公開鍵ログインは確認しません。
- Auth Key による登録と、Dots オートメーション経由の起動は未検証です。

## オフライン回帰チェック

リポジトリ checkout から `python3 -m unittest discover -s tests -v` と `bash -n tailscale-setup.sh` を実行します。オンボーディングテストには Git、Bash、Python 3、OpenSSH のクライアント／サーバーツールが必要です。テストでは使い捨てのリポジトリ、鍵、`known_hosts`、モックした Tailscale CLI、分離した Unix ソケットを使います。SSH はリスナーなしでローカル stdio 経由で実行します。これらのテストでは tailnet 認証、実際のプロキシ接続、VM の入れ替えは検証しません。

## トラブルシューティング

- `cannot inspect process` または `process inspection failed`: 現在のスクリプトはアクセスできない `/proc` の項目をスキップします。既存インストールの `setup.sh` は、上記の[更新手順](#restart-update)に従って確認済みのコードに更新してください。
- `Permission denied (publickey)`: `sshd/authorized_keys` にある公開鍵のフィンガープリントを確認し、必要に応じてクライアントの公開鍵を追加してください。`running` の表示はリモートの公開鍵認証成功を意味しません。
- `existing file differs`: インストール済みファイルがソースと異なります。設定と鍵を確認し、必要なファイルだけを更新してください。インストールディレクトリ全体を削除しないでください。state とホスト鍵も含まれています。
- `socket exists but daemon cannot be identified`: 既存のデーモンとソケットを確認してください。スクリプトは識別できないソケットを自動削除しません。
- 貼り付け後に `^[[200~` などの端末制御文字が表示される場合: コマンドを中止し、1行ずつ入力してください。続行前に、実際に実行するコマンドとホスト鍵のフィンガープリントを確認してください。
