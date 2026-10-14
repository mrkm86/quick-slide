# Quick Slide v2

会話の続きから16:9の日本語スライドを作り、ブラウザの画面でページを指定して直し、PPTX・PDF に書き出すプラグイン。
Claude Code と Codex のどちらにも載る。

- **画像は ChatGPT のサブスク枠で描く。** Codex 組み込みの画像生成（gpt-image-2）を使い、API の従量課金を前提にしない
- **エンジンは CLI から単体で呼べる。** 画面を通さなくても、スキルとして使える

## スキル

| スキル | 役 |
|---|---|
| [`skills/quick-slide/`](skills/quick-slide/SKILL.md) | 会話の入口。原稿を整えてローカルのサーバに渡し、画面の URL を返す。会話から直す。導入の確かめ（`--doctor`）と導入（`--setup`） |

## エンジン

| スキル | 作るもの |
|---|---|
| [`skills/html-slide/`](skills/html-slide/SKILL.md) | 文字も図形も PowerPoint の部品として組んだ、あとから直せて動く PPTX（プレゼンモード） |
| [`skills/image-slide/`](skills/image-slide/SKILL.md) | 同じ台本を画像生成で1枚ずつ丸ごと描いた資料（ピッチモード） |
| [`skills/motion-slide/`](skills/motion-slide/SKILL.md) | クリックで拍が進む Web プレゼン（HTML 1枚。モーションモード。PPTX・PDF にはならず、書き出しは HTML） |

2つは同じ台本を受ける。設計判断はそれぞれの `docs/adr/`。

## 入れ方

### 1. プラグインを入れる

- **Claude Code**: このリポジトリをマーケットプレイスとして足して入れる

  ```text
  /plugin marketplace add <このリポジトリの URL かフォルダ>
  /plugin install quick-slide@quick-slide
  ```

- **Codex**: このリポジトリを置いたフォルダをプラグインとして登録する（マーケットプレイスの定義は `.agents/plugins/marketplace.json`、
  プラグインの定義は `.codex-plugin/plugin.json`。スキルは `skills/`）

どちらも、リポジトリの丸写しがプラグインの置き場に入る（下の置き場はどれもプラグインの外）。

### 2. 足りないものを確かめて入れる

```bash
python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --doctor        # 何が揃っていて何が足りないか（何も書かない）
python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --setup         # 何をどこへ入れるかを出して聞く
python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --setup --yes   # 聞かずに入れる
```

会話で「Quick Slide の導入を確かめて」と頼めば、スキルが `--doctor` を流し、入れてよいか聞いてから `--setup --yes` を流す。

Windows では `python3` を `py -3` に読み替える（`python3` は Microsoft Store のスタブで動かない）。診断は Windows では、
置き場のアクセス権・OneDrive の同期先の中に無いか・長いパスも確かめる（`docs/adr/0012-windows-file-permissions.md`）。

`node`・`codex`・`claude` は、PATH で見つからなければよくある置き場（npm のグローバルの置き場・Homebrew・nvm など）も探す
（診断もサーバも同じ）。Codex の砂場の中で走った診断は、入っているものが見えないことがあるので、足りないと出ても入れる案内はせず、
自分のターミナルで `--doctor` を流すよう言う（`docs/adr/0017-cli-outside-path.md`）。

**Codex から使うときは、サーバを自分のターミナルで先に起こしておく**（Codex の砂場の中からは、置き場に書けないので起こせない）:

```bash
python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_start.py --start-server   # Windows は py -3
```

| 何 | `--setup` が入れるか | どこへ |
|---|---|---|
| Python 3.12 以上 | 入れない（python.org か Homebrew。Windows は python.org か `winget install -e --id Python.Python.3.13 --scope user`） | — |
| python-pptx・lxml・Pillow・PyYAML | 入れる（`pip --user`。Python が外から管理されているときは手順を言う） | 利用者の場所 |
| Node 18 以上・npm | 入れない（nodejs.org か `brew install node`。Windows は `winget install -e --id OpenJS.NodeJS.LTS`） | — |
| puppeteer-core | 入れる（`npm install --prefix`） | `~/.quick-slide/engines/html-slide/node/` |
| Chrome（headless shell） | 入れる（`@puppeteer/browsers`） | `~/.quick-slide/engines/html-slide/chrome/` |
| Playwright と Chromium（モーションだけ要る） | 入れる（`pip --user`・`<その Python> -m playwright install chromium`） | Playwright の置き場 |
| 游ゴシック（プレゼンが文字の幅を測る） | 入れない（mac は PowerPoint for Mac に同梱、Windows は OS に入っている。無ければ html-slide の設定の `fonts:`） | — |
| `codex` CLI（絵を描く・台本係にもなる） | 入れない（`npm install -g @openai/codex`。全体に入れるため） | — |
| `claude` CLI（台本係） | 入れない（Claude Code に付いてくる） | — |

全体に入れるもの（`npm -g`・`sudo`）は入れない。管理者の権限も要らない。

### 3. ログインする

- 絵を描く: ターミナルで `codex login`（**ChatGPT のアカウントで**。API キーでは描かない）
- 台本係: 会話しているアプリ（Claude Code か Codex）がログインしていればよい

Quick Slide はログインを代わりにしない（`--doctor` がコマンドを言う）。

### 4. 使う

会話で「この内容をスライドにして」と言う。スキル `quick-slide` がサーバを起こし（ポート 7801）、画面の URL を返す。
直すのは画面（ページを選んで指示）か会話（「3枚目の見出しを短く」）。書き出しは画面の右上の PDF・PPTX のボタン。

最初にサーバを起こすと、プレゼン・ピッチ・モーションの**見本**が 1 冊ずつ入る（それぞれのモードの紹介。タグ「見本」）。
消してよく、消した見本は次の起動で戻らない。戻したいときは画面の 設定 › 見本 の「見本を入れ直す」。
見本の中身と作り直し方は [`samples/`](samples/README.md)。

## 置き場

| 何 | 既定 | 変え方 |
|---|---|---|
| データ（DB・スライド・設定・単体で使ったときの出力） | `~/.quick-slide/` | 環境変数 `QUICK_SLIDE_HOME` |
| エンジンの設定 | `~/.quick-slide/engines/<スキル>/config.yaml` | `HTML_SLIDE_CONFIG`・`IMAGE_SLIDE_CONFIG` |
| puppeteer-core と Chrome（headless shell） | `~/.quick-slide/engines/html-slide/node/`・`chrome/` | 設定の `node_modules`・`chrome`、環境変数 `HTML_SLIDE_NODE_MODULES`・`HTML_SLIDE_CHROME` |
| 描いた絵のキャッシュ（上限つき） | `~/Library/Caches/quick-slide/<スキル>/` | `HTML_SLIDE_CACHE`・`IMAGE_SLIDE_CACHE`・設定の `cache_dir` |

置き場はエンジンの `scripts/places.py`（Node のモジュールと Chrome は html-slide の `scripts/browser.py`）で決める。
ごみ箱から完全に削除すると、そのスライドの置き場と描いた絵のキャッシュも消える。

## 状態

- macOS で動かしている（Windows・Linux はまだ確かめていない）
- 会話から 1 冊（P2）・直す（P3）・設定をエンジンへ（P4）・書き出しと導入（P5）まで。段の控えは `docs/P1-NOTES.md`〜`docs/P5-NOTES.md`

作業に入る前に [AGENTS.md](AGENTS.md) を読むこと。

## 同梱している第三者のソフトウェア

- **yomiyasu**（日本語の文章のリンター・MIT）— 台本係が書いた台本の検査に使う。写しとライセンス文（`LICENSE`・`UNICODE-LICENSE.txt`）、版と更新の手順は
  [`src/quick_slide/vendor/yomiyasu/`](src/quick_slide/vendor/yomiyasu/README.md)

## ライセンス

未定。
