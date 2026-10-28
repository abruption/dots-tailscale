<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center"><a href="README.md">English</a> · <a href="README.ko.md">한국어</a> · <a href="README.zh-CN.md">简体中文</a> · 日本語</p>

Tailscale と SSH を使って、自分の端末から Dots のクラウドコンピューターに接続できます。dots-tailscale は root 権限や systemd を使わずに必要なツールをインストールし、インストールファイル、Tailscale のノード状態、SSH の識別情報を `/workspace` に保存します。環境が変わってもこのディレクトリが残っていれば、そのまま再利用できます。

**現在のリリース: [v0.6.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.6.0)。** Tailscale バイナリの手動更新に対応し、別途配備する復旧監視ツールの診断情報を改善しました。これは独立したプロジェクトであり、OpenAI や Tailscale の公式ツールではありません。

**開発候補について:** 未公開の `0.7.0-dev` ソースでは、新規インストールの既定値を Tailscale **1.104.4** に変更します。以下の v0.6.0 のコマンドは公開済みリリースをインストールします。既定値が 1.102.4 の既存パッケージは、先に Tailscale を更新し、[明示的なセキュリティ基準バージョンの移行（英語）](docs/security-baseline.md)を行う必要があります。通常の `update apply` の基準バージョン変更ガードは維持します。

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
  git clone --branch v0.6.0 --depth 1 https://github.com/abruption/dots-tailscale.git
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
/workspace/.dots-tailscale/dots-ts update check --tag v0.6.0 --proxy http://browser-proxy:8889 --json
```

検証済みの v0.4.0 以降を使っている場合は、[パッケージの更新手順](docs/usage.ja.md#update-management)に従って、先に v0.6.0 をインストールしてください。信頼できるパッケージ情報がない旧版では、ガイドに記載された手動確認が必要です。新しく取得したソースの CLI だけを使って、古いインストールを操作する方法には対応していません。

対応する v0.6.0 のツールをインストールしたら、Tailscale の公式安定版を確認できます。

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

**公開済み v0.6.0 で新規インストールする場合、Tailscale は 1.102.4 です。** この既定値を維持することで、以前のパッケージ更新ツールから新しいツールを導入できます。パッケージを更新しただけでは Tailscale は更新されません。バイナリを変更する場合は、表示されたバージョンと Tailscale の公式アドバイザリを確認し、[手動更新ガイド（英語）](docs/runtime-updates.md)に従ってください。更新先の明示的な指定、オフライン用ファイル、処理状況の確認方法を説明しています。

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
- `/workspace` が残ることは利用条件であり、プラットフォームによる保証ではありません。実行時のソケット、PID、ログは `/tmp` に作成し直します。新しい環境では、起動コマンドを実行する手段が引き続き必要です。
- ローカルの `Running` 表示だけでは、外部から接続できるとは限りません。新しい SSH 接続と、実際に使うアプリの接続経路を確認してください。1.104.2 への更新が Tailworm の修正を意味するわけではありません。

![インストール確認と Tailscale 1.102.4 を表示した過去の Dots ターミナル画面](docs/assets/dots-tailscale-ssh-verification.png)

このスクリーンショットは管理者が提供した過去のインストール・状態確認の記録です。現在のパッケージバージョンや VM 交換後の復旧を示すものではありません。その後の更新試験については、上記の日付付きの記録を参照してください。

## 外部からの復旧実験

外部サービスを運用できる方は、[Dots を使った外部からの復旧実験（英語）](docs/experimental-recovery.md)を確認できます。外部監視、認証付き MCP Events サーバー、許可済みの Dots タスクを使い、接続障害の後に起動コマンドの実行を依頼します。通常のインストーラーは、この基盤を配備したり復旧タスクを有効にしたりしません。

現在の実験には外部サーバーと SSO の設定が必要です。イベント処理には遅延があり得ます。別のアカウントでの設定再現や、任意の VM 交換後の復旧は未検証です。watchdog との連携や、明示的に有効化する自動更新は今後の課題であり、v0.6.0 で有効になる機能ではありません。

## ドキュメントと構成ファイル

- [使い方とトラブルシューティング](docs/usage.ja.md) · [Tailscale の手動更新（英語）](docs/runtime-updates.md)
- [プライベート HTTPS API の利用例](docs/private-http-api.ja.md) · [復旧実験（英語）](docs/experimental-recovery.md)
- [リリースノート](docs/releases/v0.6.0.md) · [変更履歴](CHANGELOG.md) · [MIT ライセンス](LICENSE)

| パス | 用途 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | インストール・起動、ローカル OpenSSH、プロキシに対応した Tailscale 通信 |
| `dots-ts`, `dots_proxy.py` | 診断、パッケージ・Tailscale の更新、任意のアプリ用プロキシ設定 |
| `source-manifest.json` | パッケージバージョンとソースファイル 4 つのチェックサム |
| `home-persistence-probe.py` | HOME と `/workspace` の継続性を確認するマーカーの記録 |
| `tests/`, `research/` | 回帰テストと、別途配備する研究用ツール |
