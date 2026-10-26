<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center"><a href="README.md">English</a> · <a href="README.ko.md">한국어</a> · <a href="README.zh-CN.md">简体中文</a> · 日本語</p>

Dots のクラウドコンピューターで、ユーザー権限の OpenSSH とともに Tailscale を実行します。インストールファイル、ノードの状態、SSH の ID 情報は `/workspace` に保存し、実行時のソケット、PID、ログは `/tmp` に作成し直します。

`/workspace` が環境の入れ替え後も保持される環境を対象としています。新しい環境では、その都度 start コマンドを実行する必要があります。このプロジェクトは独立したもので、OpenAI や Tailscale の公式ツールではありません。

## 実験的な外部復旧

v0.5.0 には、外部監視と MCP Events で Dots に既存の起動コマンドの実行を依頼する実験コードと運用手順を含めています。通常のインストールでは有効になりません。外部サーバーと認証・監視の運用が必要です。再起動後の接続復旧は複数回確認していますが、すべての VM 交換での復旧や即時実行を保証するものではありません。[実験機能の運用ガイド（英語）](docs/experimental-recovery.md)をご覧ください。

## Tailscale バイナリの更新

v0.6.0 では、インストール済みの Tailscale 実行ファイルを `dots-ts runtime` で手動更新できます。既存のパッケージ更新ツールとの互換性を保つため、初期インストールは 1.102.4 のままです。バイナリの更新は別途実行してください。自動更新は有効になりません。事前準備や中断時の復旧手順・制限は[更新ガイド（英語）](docs/runtime-updates.md)をご覧ください。

## デモ

![Dots ターミナルでインストール状況と Tailscale バージョン 1.102.4 を確認する画面](docs/assets/dots-tailscale-ssh-verification.png)

Dots デスクトップのターミナルで撮影された、メンテナー提供のスクリーンショットです。既存のインストール状況とステータス、および Tailscale 1.102.4 のバージョンが表示されています。提供された画像をそのまま使用しており、新規インストールや VM の入れ替えを示すものではありません。

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

## クイックスタート

Linux x86_64、Git、Python 3、OpenSSH、およびローカルポート 22 をバインドする権限が必要です。依存関係と SSH の設定については、[使用ガイド](docs/usage.ja.md)を参照してください。

確認済みの Dots SSH 環境では GitHub への HTTPS 直接接続に失敗したため、clone コマンドでは Dots のプロキシを明示的に指定しています。診断方法と設定の適用範囲は、[ダウンロード接続とプロキシ設定](docs/usage.ja.md#download-proxy)を参照してください。

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.6.0 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

初回ログインでは承認用 URL が表示されます。クライアントの公開鍵をインストール済み SSH の `authorized_keys` ファイルに追加し、接続する前にクラウドコンピューターの GUI ターミナルでホスト鍵のフィンガープリントを確認してください。環境が入れ替わった後は、`bash /workspace/.dots-tailscale/start.sh` で既存のインストールを起動します。スクリプトは自動起動を登録しません。

## 診断とプライベート HTTP 接続（v0.4.0 以降）

これらのヘルパーを使うには、[v0.6.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.6.0) のソースファイル 4 つと `source-manifest.json` をまとめてインストールしてください。

```bash
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts proxy show
```

`doctor` は状態を変更せず、ローカルの診断情報を読み取ります。`cli` はインストール済みの Tailscale バイナリとソケットを選択します。任意で使える loopback SOCKS5/HTTP プロキシは既定で無効です。有効にするには、デーモンを再起動する手順を別途確認してください。設定、アクセス方針、検証範囲については、[使用ガイド](docs/usage.ja.md#development-helpers)と[プライベート HTTP API の利用例](docs/private-http-api.ja.md)を参照してください。これらのプロキシは ChatGPT コネクター、MCP クライアント、Dots ブラウザーには自動設定されません。

## インストール済みパッケージのバージョンと更新

v0.4.0 では `dots-ts version` と `dots-ts version --json` で、dots-tailscale パッケージと Tailscale バイナリのバージョンを別々に確認できます。v0.3.1 には含まれていません。詳しくは[パッケージのバージョンとファイルの確認](docs/usage.ja.md#package-version)を参照してください。

v0.4.0 には、安定版リリースの確認、任意で有効にするキャッシュ通知、コードのバックアップとロールバックに対応した手動更新も含まれます。実行条件、独立した復旧用ターミナルの確認、別途行う SSH 検証は[更新ガイド](docs/usage.ja.md#update-management)を参照してください。

## 同梱内容

| パス | 用途 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Tailscale の確認・インストール・起動、ローカル OpenSSH の設定、Tailscale の明示的な認証 |
| `dots-ts`, `dots_proxy.py` | CLI、診断、およびプロセスごとの任意の外向きプロキシ設定 |
| `source-manifest.json` | パッケージのバージョンとソースファイル 4 つの SHA-256 チェックサム |
| `home-persistence-probe.py` | HOME と `/workspace` の継続性を示すマーカーを記録 |
| `tests/` | オフライン回帰テスト |

[英語の使用ガイド](docs/usage.md)、[韓国語の使用ガイド](docs/usage.ko.md)、[中国語（簡体字）の使用ガイド](docs/usage.zh-CN.md)、[日本語の使用ガイド](docs/usage.ja.md)、[MIT ライセンス](LICENSE)を参照してください。
