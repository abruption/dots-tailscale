<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center"><a href="README.md">English</a> · <a href="README.ko.md">한국어</a> · <a href="README.zh-CN.md">简体中文</a> · 日本語</p>

Tailscale と SSH を使って、自分の端末から Dots のクラウドコンピューターに接続できます。dots-tailscale は root 権限や systemd を使わずに必要なツールをインストールし、インストールファイル、Tailscale のノード状態、SSH の識別情報を `/workspace` に保存します。環境が変わってもこのディレクトリが残っていれば、そのまま再利用できます。

**現在のリリース: [v0.7.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.7.0)。** 新規インストールの Tailscale を 1.104.4 に更新し、バイナリ検証と既存パッケージの明示的な移行手順を追加しました。復旧リレーは削除された Python 環境も検出します。OpenAI や Tailscale の公式ツールではない独立プロジェクトです。

**v0.6.0 など旧基準からの更新:** Tailscale の既定値が 1.102.4 のパッケージでは、先にランタイムを更新してから[明示的な基準バージョン移行（英語）](docs/security-baseline.md)を行ってください。通常の `update apply` の変更ガードは維持されます。

## できること

| 操作 | 内容 |
|---|---|
| インストール・再接続 | ローカルの OpenSSH とともに Tailscale を実行します。保存されたインストールを再起動するときは、既存の状態と鍵を再利用します。 |
| 接続の確認 | `dots-ts version` と `doctor` で、ファイルの整合性、プロセス、ソケット、バックエンドの状態を確認できます。 |
| プライベートサービスへの接続 | tailnet 内の接続や、個別に設定したアプリ向けのローカル SOCKS5/HTTP プロキシを利用できます。 |
| パッケージの更新 | `dots-ts update` でリリースを確認し、スクリプトを手動更新できます。キャッシュを使ったローカル通知も任意で有効にできます。 |
| Tailscale の更新 | `dots-ts runtime` でバージョンを確認し、CLI とデーモンを一組として手動更新できます。進捗と復旧用の記録はファイルに残ります。 |

自動起動や自動更新は有効になりません。後述する外部からの復旧実験には、別途設定が必要です。

## はじめに

**新規インストール**は Dots のクラウドターミナルで行います。Linux x86_64、Python 3、Git、OpenSSH と、ローカルのポート 22 を使用できる権限が必要です。[依存ツールの一覧と SSH 設定](docs/usage.ja.md)も確認してください。すでにインストール済みの場合は再インストールせず、次の更新手順に進んでください。

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.7.0 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

初回ログインで表示される承認 URL を開いてください。接続元の公開鍵をインストール先の SSH `authorized_keys` に追加し、接続前にクラウドターミナルでホスト鍵のフィンガープリントを独立に照合します。詳しい手順は[使用ガイド](docs/usage.ja.md)を参照してください。

試験した Dots の SSH 環境では GitHub への直接の HTTPS 接続に失敗したため、clone コマンドではプラットフォームのプロキシを明示しています。それでもダウンロードできない場合は、[接続の診断と別の転送方法](docs/usage.ja.md#download-proxy)を確認してください。ソースファイル 4 つと、それらに対応する元の `source-manifest.json` は必ず一緒に扱います。

環境が変わってもインストール先が残っている場合は、次のコマンドで再起動します。

```bash
bash /workspace/.dots-tailscale/start.sh
```

## インストール済みの環境を更新する

管理するバージョンは 2 種類あります。

| コマンド | 更新するもの | 更新しないもの |
|---|---|---|
| `dots-ts update` | dots-tailscale のスクリプトとパッケージ情報 | Tailscale バイナリ |
| `dots-ts runtime` | インストール済みの `tailscale` と `tailscaled` のペア | dots-tailscale パッケージ |

まず、インストール済みのパッケージと、更新先のリリースを確認します。

```bash
/workspace/.dots-tailscale/dots-ts version
/workspace/.dots-tailscale/dots-ts update check --tag v0.7.0 --proxy http://browser-proxy:8889 --json
```

Tailscale の基準を 1.102.4 と宣言する v0.6.0 などの既存パッケージは、[基準バージョンの移行手順（英語）](docs/security-baseline.md)で v0.7.0 に更新してください。v0.6.0 より古いツールを使っている場合は、先に[パッケージ更新手順](docs/usage.ja.md#update-management)で同じ基準の v0.6.0 を導入します。信頼できるメタデータがない旧版は手動確認が必要です。

v0.6.0 以降のツールを導入したら、Tailscale の公式安定版を確認できます。

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

**v0.7.0 の新規インストールでは Tailscale 1.104.4 を使います。** パッケージ更新や `install` が既存バイナリを暗黙に置き換えることはありません。バイナリを更新する場合は、表示されたバージョンと公式アドバイザリを確認し、[手動更新ガイド（英語）](docs/runtime-updates.md)に従ってください。基準移行では、インストール済みの両バイナリと実行中のバックエンドがすべて正確に 1.104.4 であることを確認してからソースを変更します。

更新を適用する前に、Tailscale 接続とは独立した復旧用クラウドターミナルを操作できることを確認してください。Tailscale バイナリの更新時は外部の復旧タスクを一時停止し、新しい SSH 接続と ping を確認してから再開します。更新候補のデーモンの実行を一度でも試みた後は、起動に失敗しても自動ダウングレードは行いません。失敗時の状態が不明なら、ガイドの手動復旧手順が必要です。パッケージのコードを戻せることと、Tailscale の状態データに互換性があることは別です。

## よく使うコマンド

```bash
# パッケージの整合性と独立した Tailscale バージョンを確認
/workspace/.dots-tailscale/dots-ts version
# ローカルのインストール状態と接続状態を診断
/workspace/.dots-tailscale/dots-ts doctor
# インストール済みの Tailscale CLI を専用ソケットで実行
/workspace/.dots-tailscale/dots-ts cli status
# 任意のアプリケーションプロキシ設定を確認
/workspace/.dots-tailscale/dots-ts proxy show
```

ローカル SOCKS5/HTTP プロキシは既定で無効です。有効にするには、明示的にデーモンを再起動する必要があります。Dots のブラウザー、ChatGPT コネクター、MCP クライアントに自動設定されるものではありません。[プロキシの設定](docs/usage.ja.md#development-helpers)と[プライベート HTTPS API の利用例](docs/private-http-api.ja.md)を参照してください。

## 確認できていること

- [Dots 実環境での試験](docs/validation/runtime-update-trial-20261026.md)では、レビュー済みの開発版パッケージを使って Tailscale を 1.102.4 から 1.104.2 に更新しました。ノードと SSH の識別情報を保ち、更新後に新しい外部 SSH 接続とリレー経由の ping が成功することを確認しました。
- [v0.6.0 リリース](https://github.com/abruption/dots-tailscale/releases/tag/v0.6.0)には、最終コミットでの本体・研究用テストと、隔離環境での公式バイナリ確認を記録しています。将来のすべてのバージョンとの互換性、実環境での障害復旧、状態データをダウングレードする安全性を証明するものではありません。
- [v0.7.0 リリース](https://github.com/abruption/dots-tailscale/releases/tag/v0.7.0)では、隔離した新規インストールとソース移行のテストを追加しました。その後の [2026-10-28 実環境ソース移行試験](docs/validation/v0.7.0-migration-trial-20261028.md)では、既存インストール 1 件のパッケージを 0.6.0 から 0.7.0 に移行しました。導入済みの Tailscale 1.104.4 ランタイムとノード・SSH の識別情報を維持し、移行後に厳密なホスト鍵検証を行う新しい SSH 接続とリレー経由の ping が成功しました。新規インストール、別アカウント、実環境での障害注入、中断された処理の修復、VM 交換、ランタイム状態のダウングレード、将来のすべてのバージョンを検証したものではありません。[公開前の基準バージョン・TLS 確認](docs/validation/security-baseline-20261028.md)は別の過去の記録です。
- `/workspace` が残ることは利用条件であり、プラットフォームによる保証ではありません。実行時のソケット、PID、ログは `/tmp` に作成し直します。新しい環境では、起動コマンドを実行する手段が引き続き必要です。
- ローカルの `Running` 表示だけでは、外部から接続できるとは限りません。新しい SSH 接続と、実際に使うアプリの接続経路を確認してください。1.104.2 への更新が Tailworm の修正を意味するわけではありません。

![インストール確認と Tailscale 1.102.4 を表示した過去の Dots ターミナル画面](docs/assets/dots-tailscale-ssh-verification.png)

このスクリーンショットは管理者が提供した過去のインストール・状態確認の記録です。現在のパッケージバージョンや VM 交換後の復旧を示すものではありません。その後の更新試験については、上記の日付付きの記録を参照してください。

## 外部からの復旧実験

外部サービスを運用できる方は、[Dots を使った外部からの復旧実験（英語）](docs/experimental-recovery.md)を確認できます。外部監視、認証付き MCP Events サーバー、許可済みの Dots タスクを使い、接続障害の後に起動コマンドの実行を依頼します。通常のインストーラーは、この基盤を配備したり復旧タスクを有効にしたりしません。

現在の実験には外部サーバーと SSO 設定が必要です。イベント処理は遅延することがあり、別アカウントでの再現や任意の VM 交換後の復旧は未検証です。watchdog との連携とオプトイン自動更新は v0.8.0 の後続課題であり、v0.7.0 で有効になる機能ではありません。

## ドキュメントと構成ファイル

- [使い方とトラブルシューティング](docs/usage.ja.md) · [Tailscale の手動更新（英語）](docs/runtime-updates.md)
- [プライベート HTTPS API の利用例](docs/private-http-api.ja.md) · [復旧実験（英語）](docs/experimental-recovery.md)
- [リリースノート](docs/releases/v0.7.0.md) · [変更履歴](CHANGELOG.md) · [MIT ライセンス](LICENSE)

| パス | 用途 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | インストール・起動、ローカル OpenSSH、プロキシに対応した Tailscale 通信 |
| `dots-ts`, `dots_proxy.py` | 診断、パッケージ・Tailscale の更新、任意のアプリ用プロキシ設定 |
| `source-manifest.json` | パッケージバージョンとソースファイル 4 つのチェックサム |
| `home-persistence-probe.py` | HOME と `/workspace` の継続性を確認するマーカーの記録 |
| `tests/`, `research/` | 回帰テストと、別途配備する研究用ツール |
