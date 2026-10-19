# P5（書き出しと仕上げ）のメモ

状態: P5 で決めたこと・仮置きにしたことの控え。ADR に切り出すまでの置き場。設計は `docs/DESIGN.md` §5.3・§7 の P5。

## 書き出し（`server/export_routes.py`）

```
画面の PDF・PPTX のボタン ─ POST /api/decks/<id>/exports {format} → {"export": {…, "download_url"}}
                         └ GET /api/exports/<id>/file → そのファイル（Content-Disposition: attachment）
```

- **配るのは見せる回（画面に出ている回 = 描き終えた一番新しい回）の控え**（`runs/<回>/deck.pptx`・`deck.pdf`）。作り直さない。
  非表示のページはエンジンに渡す台本から抜いてあるので入っていない
- 形式は `pptx`・`pdf`・`images`（ページの画像 `slides/slide-NN.png` の ZIP。`runs/<回>/exports/pages.zip` に時刻を固定して
  作るので、同じ回なら同じバイト）。**画面のボタンは PDF・PPTX の 2 つのまま**（画面の決まり。ZIP は API だけ）
- ファイル名はスライドの題（OS で使えない文字と制御文字は「_」・拡張子込みで 120 文字まで・空なら「スライド」）＋拡張子。
  `Content-Disposition` は `filename*=UTF-8''…`（RFC 5987）と ASCII の代わりの名前
- **書き出しは記録する**（表 `exports`・移行 `0004_exports.sql`: 回・形式・控えのパス・sha256・大きさ・名前）。
  配るときに sha256 を照らし、違えば 409 `export_changed`（同じ URL はいつも同じバイト）。同じ回・形式・中身・名前は 1 行にまとめる
- **見せる回にその形式が無いとき**（エンジンがその出力だけ作れなかった・まだ 1 回も描き終えていない）は、いまの台本を
  いまの設定で描き直す仕事（直しの op `render`。理由 `export` を頼みごとに残す）を作り、終わるのを口の中で待ってから配る
  （上限 900 秒・`render_timeout_seconds`＋60 の小さいほう）。超えたら 202 と「作っています。もう一度押してください」
  （画面は URL が無ければその 1 文を出す）。台本係は呼ばない。描き直した回が画面の回になる
- 断り: 形式の誤り 400 `export_format`・ページが無い 409 `no_pages`・ごみ箱の中のスライドの書き出し 404・知らない書き出し 404
- `capabilities.export` はページがあれば押せる（P4 までは「まだ使えない」）
- ごみ箱から完全に削除すると、書き出しの記録も消える（`_PURGE_DELETES`）

## 導入（Part B）

### エンジンの変更（html-slide・ADR 0016）

Node のモジュールと Chrome の在りかを `skills/html-slide/scripts/browser.py` の 1 か所で探す。上から順:

| 何 | 1. 環境変数 | 2. 設定 | 3. Quick Slide の置き場 | 4. 前からの置き場 |
|---|---|---|---|---|
| Node | `HTML_SLIDE_NODE` | `node_bin` | — | PATH の `node` |
| Node のモジュール | `HTML_SLIDE_NODE_MODULES` | `node_modules` | `<home>/engines/html-slide/node/node_modules` | `~/.cache/html-video/node_modules` |
| Chrome | `HTML_SLIDE_CHROME` | `chrome` | `<home>/engines/html-slide/chrome/` | `~/.cache/puppeteer/` |

- 環境変数と設定は無くても次へ落ちない（無いと言う）。3 と 4 は中身があるほう。どちらにも無ければ 3 を「無い」と言う
- `.mjs`（`extract.mjs`・`snapshot.mjs`・`logo_ground.mjs`）は探さない。呼ぶ側（`render.py`・`html_slide.py`・`deck.py`・
  `catalog.py`）が見つけた在りかを環境変数で渡す。直に呼ばれたときだけ前からの置き場を見る（ほかの環境からの使い方を壊さない）
- **足りなければ描く前に rc=4**（`deck`・`check`・`revise`）。先に Codex で絵を描いてから足りないと分かると枠を無駄に使う。
  rc=4 の文は入れ方（`references/deploy.md`・`quick_slide_setup.py --setup`）を言う
- `places.py`・`cache.py`・`outputs.py` は変えていない（2 つのエンジンで同じまま）

### サーバ

- **プレゼンの描く前の確かめ**: 台本係を呼ぶ前に、エンジンの `browser.py` に聞く（足りなければ `dependency` で止める。
  前はピッチの Codex のログインだけを見ていた）。検査（`check`）が rc=4 のときも `dependency`
- 止まっている帯（rc=4・`dependency`・`auth`）の文は導入の確かめを言う:
  「`python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --doctor` で足りないものを確かめ、`--setup` で入れて
  から（会話で「Quick Slide の導入をして」と頼んでも入ります）」

### `quick_slide_setup.py`（標準ライブラリだけ・`quick_slide_start.py --doctor|--setup` からも）

- `--doctor`（何も書かない）: Python の版・4 つのパッケージ（版つき）・Node（18 以上）・npm・puppeteer-core と Chrome
  （`browser.py` に聞く。どこで見つけたか）・游ゴシック（PowerPoint for Mac 同梱か、html-slide の設定の `fonts:`。
  無ければ `fonts:` の書き方を言う）・`codex` CLI と `codex login status`（ChatGPT か・API キーか・未ログイン。出力そのものは
  出さない）・台本係の CLI（`claude` / `codex`。在りかだけ見る。呼ばない）・データの置き場（書けるか・空き）・ポート
  （設定のポートが空いているか・Quick Slide が使っているか）。日本語の行のあと、最終行に JSON 1 行
  （`checks[]`・`ready`（presentation・pitch・images）・`setup`）。足りなければ rc=4
- `--setup`: 確かめてから、入れるものとコマンドを出し、`--yes` が無ければ聞く（端末でなければ入れずに止まる）。入れるのは:
  Python のパッケージ（`pip --user`。Python が外から管理されている＝PEP 668 なら入れずに手順を言う）・puppeteer-core
  （`npm install --prefix <home>/engines/html-slide/node puppeteer-core`）・Chrome（puppeteer-core と一緒に入る
  `@puppeteer/browsers` の `install chrome-headless-shell@stable --path <home>/engines/html-slide/chrome`。無ければ `npx`）。
  環境変数・設定が別の場所を指しているときは入れない（指定を言う）。Node・`codex` CLI・PowerPoint・ログインは入れない
  （`manual` にコマンドを並べる）。全体に入れるもの（`-g`・`sudo`）は使わない。入れたあともう一度確かめて JSON を出す
- スキル `quick-slide`: 初めて使うとき・rc=4 と言われたとき・「導入して」と言われたときに `--doctor` → 利用者に聞いて
  `--setup --yes` → `manual` は会話でコマンドを伝える（ログインを代わりにしない）

## 「配った機体」の通し（Part C・`tests/test_p5_fresh_machine.py`）

`QUICK_SLIDE_FRESH_E2E=1` のときだけ走る（ネットワークから落とすため）。リポジトリの丸写し（`.git` 抜き）を一時フォルダに置き、
何も無い HOME と、本物の python・node・npm だけの PATH（`env` は空から作る。`PYTHONDONTWRITEBYTECODE` も付けない）で:
`--doctor` → `--setup --yes` → `--doctor` → 写しの入口でプレゼン 1 冊（`--no-images`・偽物の台本係・`--wait`）→ API で PPTX・PDF →
ピッチ 1 冊（偽物の codex）→ PPTX → 外の跡を探す。

2026-10-07 にこの機体（macOS・arm64）で 1 回流した結果:

| 段 | 秒 | 大きさ |
|---|---|---|
| `--doctor`（足りない: puppeteer-core・Chrome） | 0.3 | — |
| `--setup --yes`（npm と Chrome headless shell 155 を落とす） | 11.7 | node_modules 24.3 MB・Chrome 210.7 MB |
| `--doctor`（ready: 3 つとも真） | 0.2 | — |
| プレゼン 1 冊（4 ページ・サーバの起動を含む） | 6.8 | PPTX 41 KB・PDF 54 KB |
| ピッチ 1 冊（4 ページ） | 3.4 | PPTX 32 KB |

- 外の跡: 一時 HOME・写し・一時フォルダのテキストと DB に、本物の HOME の場所・`~/.cache/html-video`・`~/.cache/puppeteer` は
  出なかった。描いた絵のキャッシュは一時 HOME の `Library/Caches/quick-slide/` の下。写しには `__pycache__` ができず、
  大きさも変わらなかった。HOME の直下にできたのは `.quick-slide`・`.npm`（npm のキャッシュ）・`Library`（と試験の偽の Codex の置き場）
- 字体は PowerPoint for Mac 同梱の游ゴシック（一時 HOME の外。機体のアプリ）を読んだ
- ポートは自動割当にした（この機体で動いているサーバとぶつけないため。`settings.json` を先に置いた）

## 残り（Part D）

### 完全に削除したときのキャッシュの区画（`render/caches.py`）

- 消すのは、キャッシュの根っこの直下にあり、印（`owner.json`）の持ち主が**そのデッキの置き場（`decks/<デッキ>/`）の中**の区画
  （image-slide は `work/`・P2 の回の `runs/<回>/`、html-slide は `scripts/`）。根っこはエンジンの記録の `cache` の親と、
  エンジンの `places.cache_root`（環境変数 → 設定 → OS の既定）の 2 つから集める。印の無いフォルダ・ほかのデッキの区画は消さない
- ごみ箱へ移すときは残す（戻したら描いた絵をそのまま使う）。空く容量の見積りにも入れる
- エンジンの `places.py` は import の仕組みを通さずに読む（エンジンのフォルダに `__pycache__` を書かない）

### ブランドの字体 → エンジンのテーマ（`render/look.brand_font`）

- エンジンは字体の名前だけでは組めない（文字の幅を字体ファイルで測る）。**エンジンの設定の `fonts:` にその名前の regular（と bold）
  があり、ファイルがあるときだけ**、テーマに `font`・`font_files` を足す。名前はブランドの本文の字体（`fonts.body`）、次に見出し、
  それぞれの代わりの名前（`fallback_families`）
- 分からなければエンジンの既定（游ゴシック）のまま組み、回の記録 `look.font`（`requested`・`applied`・`files`・`note`）に理由を残す。
  付属のブランドの字体（名前だけ）は毎回ここに「分からない」と出る（`notes` には出さない）
- 字体の名前をよその機体の字体ファイルから探すことはしない（付属のブランドが入っている字体で組み方を変えてしまうため）

### プラグインのマニフェスト

- 版は 2.0.0 のまま。`.claude-plugin/plugin.json` はスキルの置き場を書かない（既定の `skills/` を読む）。`.codex-plugin/plugin.json` は
  `"skills": "./skills/"`。説明は 3 つのスキル（quick-slide・html-slide・image-slide）を言う

### 画面（`web/app/deck.js`）

- 書き出し: 答えに URL が無ければ（202）、その 1 文を出す（`ctx.exportDeck`）
- ブランド・見せ方の選び直しは撤去（ADR 0032）。別の設定なら新しい資料として頼み直す

## 確かめたこと

- `tests/test_p5_export.py`（偽物のエンジン）: 見せる回の PPTX・PDF と同じバイト・名前・`Content-Disposition`・記録・同じ書き出しは
  1 行／ZIP は同じバイト・非表示のページは入らない・前の書き出しは前のバイト／無い形式は描き直して配る（台本係を呼ばない）／
  断り（形式・ページなし・中身が変わった・ごみ箱）／完全に削除で区画が消え、ごみ箱では残り、ほかのデッキの区画と印の無いフォルダは
  残る／字体（在りかが分かれば YAML に入る・
  代わりの名前・分からなければ既定と理由・本物の html-slide の `check` が字体ファイルつきのテーマを受ける）
- `tests/test_p5_setup.py`: 何も無い HOME で `--doctor`（足りないもの・何も書かない・ログインの文を出さない）・環境変数の在りか・
  未ログインと台本係なし・入口の `--doctor`／`--setup` は `--yes` が無ければ入れない・`--yes` で偽物の npm と `@puppeteer/browsers` が
  Quick Slide の置き場にだけ入れる・2 回目は入れない
- `tests/live/test_p5_deck_menu.py`（本物のヘッドレス Chrome）: 書き出しのボタンが押せて「ダウンロードしました」・記録が見せる回・
  メニューに設定の選び直しがない・例外なし
- html-slide の `tests/test_browser.py`: 探す順・何も無いとき・一番新しい版・JSON・足りなければ `check`・`deck` が描く前に rc=4

## まだのこと・確かめていないこと

- Windows・Linux: `browser.py` は `@puppeteer/browsers` の機種名（linux64・win64）を探すが、動かしていない。游ゴシックの在りか・
  キャッシュの置き場（`places.py` は XDG）・`pip --user` の扱いも macOS でしか見ていない
- PowerPoint の無い機体: 游ゴシックが無いと doctor は「足りない」と言い、設定の `fonts:` を案内する。ほかの字体（ヒラギノなど）で
  組んだときの文字の幅の揺れは確かめていない
- Python が外から管理されている（Homebrew の Python）機体で、パッケージが無いとき: `--setup` は入れずに手順を言うだけ
- 書き出しで描き直しを待つ間（最大 900 秒）、その要求のスレッドは待ち続ける（サーバの止め方は変えていない）
- 書き出しの ZIP は画面のボタンに出していない（API だけ）
