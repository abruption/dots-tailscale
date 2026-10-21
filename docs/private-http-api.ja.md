# Dots からプライベート HTTP API にアクセスする

[English](private-http-api.md) · [한국어](private-http-api.ko.md) · [简体中文](private-http-api.zh-CN.md) · 日本語 · [使用ガイド](usage.ja.md#development-helpers)

この手順では、同じ tailnet 内にある別のデバイス上の HTTPS API を、任意のローカル Tailscale プロキシ経由で Dots ターミナルのプロセスから呼び出します。この手順に必要なヘルパーは v0.3.0 で導入されました。v0.3.0 以降のヘルパーファイル一式をインストールしてください。v0.2.0 以前には含まれていません。

## 接続経路と前提条件

```text
Dots curl process -> 127.0.0.1:8080 HTTP CONNECT proxy
                 -> Tailscale -> private API device:8443 -> HTTPS API
```

任意で使える SOCKS5 の接続先は `socks5h://127.0.0.1:1080` です。`h` により、接続先の名前解決がプロキシ経由になります。HTTP CONNECT も接続先ホスト名をプロキシに渡します。ポート `18089` のデーモン shim を代わりに使わないでください。これは Tailscale 自体の Dots プラットフォームプロキシ経路に使われるもので、アプリケーションのリクエスト用ではありません。

次の項目をそれぞれ準備します。

- 対象の tailnet で認証済みの Dots Tailscale ノード。v0.3.0 以降のヘルパーファイル一式をインストールしておきます。
- tailnet 経由で到達できるプライベート API デバイス。意図したプライベート／Tailscale インターフェイスとポートで HTTPS リスナーが待ち受けている必要があります。API デバイスの `127.0.0.1` だけで待ち受けるリスナーには Dots から接続できません。手軽だからといってパブリックインターフェイスに公開しないでください。
- 選択したプロキシ経由で名前解決でき、API 証明書とも一致する接続先名。信頼済みの公開 CA を使うか、プライベート CA ファイルをクライアントに明示的に渡してください。IP アドレスを使う場合も、証明書の ID が一致する必要があります。
- 必要な送信元、接続先、TCP ポートだけを許可する tailnet 権限に加え、サーバー側の bearer 認証と API 認可。Tailscale に接続できても、API 認証の代わりにはなりません。
- 認証が必要な読み取り専用 `/health` エンドポイント。`{"status":"ok"}` のような、小さく非公開情報を含まないレスポンスだけを意図的に返すものを用意してください。サンプルのエンドポイントはサーバー側の同等機能に置き換え、データをエクスポートしたり変更したりするエンドポイントは使わないでください。

## ネットワークアクセスを制限する

次の有効な JSON は、Tailscale ポリシーで有効な JSON5 でもあります。送信元タグ、接続先タグ、許可する TCP ポートを1つ指定する例です。名前と管理者の所有権はポリシーに合わせて置き換えるプレースホルダーです。ポリシーファイル全体を置き換えず、関連する項目を既存のポリシーに統合してください。

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

権限を持つ tailnet 管理者が、送信元タグを対象の Dots デバイスに、接続先タグを API デバイスに割り当てる必要があります。これらのスクリプトはタグを割り当てず、ポリシーも編集しません。grants は追加方式です。この範囲を絞った項目を加えても、既存のより広い grants や ACL ルールは取り消されません。アクセスが制限されたと判断する前に、既存のルール、タグの所有権、API ホストのファイアウォールルールを確認してください。実際のノード ID を使って、tailnet ポリシーエディターの検証とポリシーテストを行ってください。公式の [grants ドキュメント](https://tailscale.com/kb/1324/grants)も参照してください。

## ローカルリスナーを意図的に有効にする

まず[設定と適用の手順](usage.ja.md#development-helpers)を読んでください。プロキシリスナーは既定で無効です。設定だけでは希望する状態が保存されるだけで、稼働中のデーモンは再起動しません。

```bash
/workspace/.dots-tailscale/dots-ts proxy show
/workspace/.dots-tailscale/dots-ts proxy configure --socks-port 1080 --http-port 8080
```

ポートは別々にしてください。この実装では、最初は SOCKS5 と HTTP に同じポートを指定できません。希望する設定は `/workspace/.dots-tailscale/outbound-proxy.json` に保存されます。ランタイムのリスナーは環境の入れ替え後に作り直す必要があります。設定の適用には、SSH を切断する可能性のある、別途実行する保護付きデーモン再起動が必要です。`dots-ts proxy apply` の手順に進む前に、独立した Dots GUI ターミナルとロールバック手順を確保してください。適用後は外部から厳格な鍵確認を使った SSH 接続を再確認してください。

これらはローカルプロセス用の loopback リスナーであり、他のマシン向けの認証済みゲートウェイではありません。リスナーにアクセスできるプロセスなら、接続を試みられます。プロキシを使うのが自分のスクリプトだけでも、API 認証と範囲を絞った tailnet 権限を維持してください。

## 認証付きリクエストを1回、時間とサイズを制限して実行する

**curl 8.4.0 以降が必要です。** このバージョンから `--max-filesize` は、サイズが不明なレスポンス（chunked レスポンスなど）も制限します。この例はトークンを求めたり API リクエストを送ったりする前に curl のバージョンを調べ、バージョンを取得できない場合や古い場合は停止します。

HTTP リスナーを適用した後、Dots ターミナルで次を実行してください。URL は HTTPS サービスのホスト名とポートに置き換えます。この例では Python 標準ライブラリを使い、表示せずに bearer token を読み取り、そのヘッダーを標準入力から curl に渡します。トークンはコマンドライン引数、URL、シェル履歴、環境変数、ファイルには入りません。リクエストが終わるまでプロセスメモリに保持されます。HTTP トレースを有効にしたり、トークンを表示したりしないでください。

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

リクエストはリダイレクトを追いません。資格情報を別の場所に転送せず、正規のエンドポイントを確認してください。HTTPS 証明書とホスト名の検証は有効のままです。レスポンスは 4 KiB までに制限され、ステータスと期待した形式かどうかだけが出力されます。サンプルサーバーのレスポンス仕様は意図的に非公開情報を含まないものです。非公開レコードを含むレスポンスにこの確認を使わないでください。

代わりに SOCKS5 を使うには、そのリスナーを有効にした後で `proxy` だけを `socks5h://127.0.0.1:1080` に変更します。`--noproxy ''` により、curl のバイパスリストが選択したプロキシを迂回するのを防ぎます。`NO_PROXY`/`no_proxy` と、継承した CA 関連の設定 (`CURL_CA_BUNDLE`、`SSL_CERT_FILE`、`SSL_CERT_DIR`) を消すのは、curl の子プロセスだけです。シェルや他のアプリケーションには影響しません。プライベート CA を使う場合は、確認済みの `ca_file` を `--cacert` で明示的に指定してください。システムのプロキシ、ブラウザー設定、MCP 設定、ChatGPT プラグイン／コネクター設定は変更しません。別のクライアントから使う場合は、そのクライアントがこのプロキシに明示的に対応し、設定できる必要があります。任意のインテグレーションから自動でこの API に接続できるわけではありません。

## 接続段階ごとの確認と拒否ケース

`dots-ts doctor` はローカルの各層を報告します。任意の `doctor --target api.example.internal:8443` は時間を制限した TCP probe であり、認証付き HTTPS の確認ではありません。TCP 接続に成功しても、証明書が有効であることやトークンが認可されることは確認できません。

| 確認項目 | 期待する結果と解釈 |
| --- | --- |
| ローカルインストール、ソケット、デーモン、プロキシ設定 | `doctor` と `proxy show` で保存済み設定と実行状態を区別し、各層の結果を確認 |
| 許可された送信元から対象ホスト／ポートへの接続 | TCP 接続が成功し、認証付きの読み取り専用ヘルスチェックが HTTP 200 と合意済みの機密情報を含まないレスポンスを返す |
| API トークンの欠落または意図的に無効な値 | サーバーがリクエストを拒否。通常は HTTP 401、一部のサービスでは 403。機密情報を含む本文は返さない |
| 許可ポリシーの範囲外にあるデバイスまたは接続先ポート | API の応答前に接続が拒否またはタイムアウトする。ポリシーテストと、より広い許可がないことを確認 |
| 信頼されていない CA またはホスト名の不一致 | TLS 検証に失敗する。検証を無効にせず、証明書または信頼設定を修正 |
| プロキシ経由で接続先名を解決できない | DNS／プロキシエラー。名前解決を tailnet 権限や API 認証とは分けて調査 |
| 設定を無効にして適用済み | ローカルリスナーは利用不可。`disable` だけでは希望状態を記録するだけで、稼働中のリスナーが停止した証明にはならない |

HTTP 401 は HTTP サーバーから応答があったことを意味し、ネットワークポリシーによる拒否ではありません。タイムアウトだけでは、ACL、ファイアウォール、ルーティング、DNS のどれが原因か特定できません。必要最小限の確認を使い、トークンや非公開のレスポンス本文を記録しないでください。

<a id="current-validation"></a>

## 検証状況

2026-10-18 (KST) の実環境確認では、ネイティブ SOCKS5／HTTP プロキシの通信、両リスナーの無効化、厳格なホスト鍵確認を使った SSH 再接続を検証しました。2種類のプロキシを通じた HTTPS 確認は全10件に成功しました。内訳は、正しい認証 (`200`)、トークンの欠落と誤り (`401`)、禁止されたエンドポイント (`403`)、信頼されていない CA (TLS 失敗) です。別途、ポリシーを変更する前・拒否を確認した時・元に戻した後の確認で tailnet ポリシーによる拒否を検証し、元のポリシーに戻しました。一時的な API サーバーと認証情報は削除済みです。[プロキシの結果](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243157252)、[ポリシーの結果](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243247818)、[HTTPS の結果](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243275452)を参照してください。

これらの確認では、自然な VM の入れ替え後の復旧や自動起動は検証していません。比較した環境では、Dots の直接 Bash からは `/workspace` が読み取り専用に見え、ランタイムソケットも見えませんでした。一方、GUI ターミナルと SSH からは書き込み可能なファイルと稼働中のソケットが共有されていました。プロキシ設定を適用する場合は独立した GUI ターミナルを使ってください。直接の agent 実行からサービスを再起動できると思い込まないでください。ライフサイクルの調査は [#20](https://github.com/abruption/dots-tailscale/issues/20#issuecomment-6243404537) で継続中です。
