# MuseHeart-MusicBot 日本語版

## Pythonで作成された音楽ボット 🎵

インタラクティブプレイヤー、スラッシュコマンド対応、[Last.fm](https://www.last.fm/)連携など、多機能なDiscord音楽ボットです。

> **📌 オリジナルリポジトリ**: このプロジェクトは [zRitsu/MuseHeart-MusicBot](https://github.com/zRitsu/MuseHeart-MusicBot) の日本語フォークです。

---

## ✨ 主な機能

- 🎮 **インタラクティブプレイヤー** - ボタン操作で簡単に音楽をコントロール
- ⚡ **スラッシュコマンド対応** - モダンなDiscordコマンドに完全対応
- 🎧 **Last.fm連携** - Scrobble機能で再生履歴を記録
- 🎨 **カスタマイズ可能なスキン** - プレイヤーの見た目を自由に変更
- 📢 **RPC (Rich Presence) 対応** - Discordステータスに再生中の曲を表示
- 🔊 **マルチボイスチャンネル対応** - 複数のボイスチャンネルで同時再生可能
- 📝 **Song Requestチャンネル** - 専用チャンネルでリクエスト管理

---

## 📸 プレビュー

### プレイヤーコントローラー（通常モード/ミニプレイヤー）

[![](https://i.ibb.co/6tVbfFH/image.png)](https://i.ibb.co/6tVbfFH/image.png)

<details>
<summary>
🖼️ その他のプレビュー
</summary>
<br>

### スラッシュコマンド

[![](https://i.ibb.co/nmhYWrK/muse-heart-slashcommands.png)](https://i.ibb.co/nmhYWrK/muse-heart-slashcommands.png)

### Last.fm連携

[![](https://i.ibb.co/SXm608z/muse-heart-lastfm.png)](https://i.ibb.co/SXm608z/muse-heart-lastfm.png)

### 固定/拡張モード（Song Requestチャンネル付き）

`/setup` コマンドで設定可能

[![](https://i.ibb.co/5cZ7JGs/image.png)](https://i.ibb.co/5cZ7JGs/image.png)

### フォーラム形式のSong Requestチャンネル

[![](https://i.ibb.co/9Hm5cyG/playercontrollerforum.png)](https://i.ibb.co/9Hm5cyG/playercontrollerforum.png)

💡 `/change_skin` コマンドで様々なスキンを選択できます。[skins](utils/music/skins/) フォルダのテンプレートを参考に、オリジナルスキンを作成することも可能です。

</details>

---

## 🚀 セットアップ手順

### ローカル環境（Windows/Linux）での実行

#### 必要要件

| 要件 | 説明 |
|------|------|
| **Python** | 3.9, 3.10, または 3.11 ([Microsoft Store](https://apps.microsoft.com/store/detail/9PJPW5LDXLZ5) / [公式サイト](https://www.python.org/downloads/)) |
| **Git** | [ダウンロード](https://git-scm.com/downloads)（ポータブル版は不可） |
| **JDK 17以上** | [ダウンロード](https://www.azul.com/downloads)（Windows/Linuxは自動ダウンロード） |

> ⚠️ **システム要件**: 最低 512MB RAM、1GHz CPU（Lavalinkを同じインスタンスで実行する場合）

#### クイックスタート

**1. ソースコードの取得**

```shell
git clone https://github.com/zRitsu/MuseHeart-MusicBot.git
cd MuseHeart-MusicBot
```

または [ZIPファイル](https://github.com/zRitsu/MuseHeart-MusicBot/archive/refs/heads/main.zip) をダウンロードして展開

**2. セットアップの実行**

- **Windows**: `source_setup.sh` をダブルクリック
- **Linux**: 
```shell
bash source_setup.sh
```

**3. 環境設定**

生成された `.env` ファイルを編集し、以下の項目を設定：

| 項目 | 説明 |
|------|------|
| `TOKEN_BOT_1` | Discordボットのトークン |
| `DEFAULT_PREFIX` | コマンドのプレフィックス |
| `MONGO` | MongoDB接続URL（推奨） |
| `SPOTIFY_CLIENT_ID` | Spotify Client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify Client Secret |

**4. ボットの起動**

- **Windows**: `source_start_win.bat` をダブルクリック
- **Linux**: 
```shell
bash source_start.sh
```

#### 更新方法

```shell
bash source_update.sh
```

> ⚠️ 更新時、手動で行った変更が上書きされる可能性があります

---

### クラウドサービスでのデプロイ

<details>
<summary>
☁️ Render.com
</summary>
<br>

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/zRitsu/MuseHeart-MusicBot/tree/main)

1. **TOKEN_BOT_1** にボットトークンを入力
2. **DEFAULT_PREFIX** にプレフィックスを設定
3. **SPOTIFY_CLIENT_ID** と **SPOTIFY_CLIENT_SECRET** を設定
4. **MONGO** にMongoDBの接続URLを入力
5. **Apply** をクリックしてデプロイ開始（約13分以上かかります）

</details>

<details>
<summary>
💻 Gitpod
</summary>
<br>

[![Open in Gitpod](https://gitpod.io/button/open-in-gitpod.svg)](https://gitpod.io/#https://github.com/zRitsu/MuseHeart-MusicBot)

1. `.env` ファイルを開き、ボットトークンとMongoDB URLを設定
2. `main.py` を右クリック → **Run Python File in Terminal**

**注意事項:**
- 電話番号による認証が必要です
- [Workspaces](https://gitpod.io/workspaces) で **pin** をクリックして14日間の削除を防止
- 無料プランには制限があります（[詳細](https://www.gitpod.io/pricing)）

</details>

<details>
<summary>
🔄 Repl.it
</summary>

[セットアップガイド（画像付き）](https://gist.github.com/zRitsu/70737984cbe163f890dae05a80a3ddbe)

</details>

---

## ⚠️ 注意事項

### 使用について

- このソースコードは、プライベート使用または自分が管理するサーバーでの使用を想定しています
- 大規模な公開ボットとしての使用は、最適化の観点から推奨されません
- 公開配布する場合は、元の[ライセンス](/LICENSE)に従う必要があります

### カスタマイズについて

- ソースコードの変更には、Python、disnake、Lavalinkの知識が必要です
- 変更を加えた場合のサポートは提供されません（カスタムスキンを除く）
- 更新時に変更が失われる可能性があります

### 問題報告

問題が発生した場合は、[Issue](https://github.com/zRitsu/MuseHeart-MusicBot/issues) で詳細を報告してください。

---

## 📜 ライセンス

このプロジェクトは元のリポジトリの[ライセンス](/LICENSE)に従います。

---

## 🙏 クレジット・謝辞

### オリジナル開発者
- **[zRitsu](https://github.com/zRitsu)** - MuseHeart-MusicBot オリジナル作者

### 使用ライブラリ・プロジェクト
- [DisnakeDev](https://github.com/DisnakeDev) - disnake
- [Rapptz](https://github.com/Rapptz/discord.py) - discord.py
- [Pythonista Guild](https://github.com/PythonistaGuild) - wavelink
- [Lavalink-Devs](https://github.com/lavalink-devs) - Lavalink & Lavaplayer
- [DarrenOfficial](https://lavalink-list.darrennathanael.com/) - Lavalink サーバーリスト

### その他
- バグ報告やフィードバックを提供してくださったすべてのコミュニティメンバーの皆様
- その他の依存関係は [dependency graph](https://github.com/zRitsu/MuseHeart-MusicBot/network/dependencies) をご確認ください
