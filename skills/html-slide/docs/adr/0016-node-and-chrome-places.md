# 0016 Node のモジュールと Chrome の在りかを 1 か所で探す

- 日付: 2026-10-07
- 状態: 採用。ADR 0015 の「残したこと」（puppeteer-core を別の道具の置き場から借りている）を片付ける

## 決めたこと

1. **在りかは `scripts/browser.py` の 1 か所で探す。** Chrome で HTML を開く道具（`extract.mjs`・`snapshot.mjs`・`logo_ground.mjs`）は
   自分では探さず、呼ぶ側が見つけた在りかを環境変数 `HTML_SLIDE_NODE_MODULES`・`HTML_SLIDE_CHROME` で渡す
2. 探す順（上から。最初に当たったもの）:

   | 何 | 1. 環境変数 | 2. 設定 | 3. Quick Slide の置き場 | 4. 前からの置き場 |
   |---|---|---|---|---|
   | Node | `HTML_SLIDE_NODE` | `node_bin` | — | PATH の `node` |
   | Node のモジュール | `HTML_SLIDE_NODE_MODULES` | `node_modules` | `<根っこ>/engines/html-slide/node/node_modules` | `~/.cache/html-video/node_modules` |
   | Chrome（headless shell） | `HTML_SLIDE_CHROME` | `chrome`（実行ファイルか入れたフォルダ） | `<根っこ>/engines/html-slide/chrome/` | `~/.cache/puppeteer/` |

   - 環境変数と設定は「そこにある」と言われたものなので、無くても次へ落ちない（無いと言う）
   - 3 と 4 は中身があるほうを使う。どちらにも無ければ 3（入れる先）を「無い」と言う
   - Chrome はフォルダの中の一番新しい版（`chrome-headless-shell/<版>/chrome-headless-shell-<機種>/`。`@puppeteer/browsers` の置き方）
3. **足りなければ描く前に rc=4**（`deck`・`check`・`revise`）。先に Codex で絵を描いてから足りないと分かると、サブスクの枠を無駄に使う。
   rc=4 の文は入れ方（`references/deploy.md`・Quick Slide の `quick_slide_setup.py --setup`）を言う
4. `python3 scripts/browser.py` は見つけたものを JSON 1 行で出す（導入の確かめが読む）

## 理由

- 前は 3 つの `.mjs` がそれぞれ別の道具の置き場（`~/.cache/html-video/`・`~/.cache/puppeteer/`）を決め打ちで読んでいた。
  その道具を入れていない機体では、どこに何を入れればよいかが決まっていなかった
- 製品の置き場の下に入れれば、管理者の権限もほかの道具も要らない。前からの置き場を最後に残すので、もう入っている機体は何もしなくてよい

## 却下した案

- スキルの中に `node_modules` を置く: 配る物が数十 MB 増え、ほかの環境からシンボリックリンクで使われているフォルダに書き込むことになる
- `.mjs` の側で探す: 探し方が Python（doctor・テスト）と 2 か所になる
