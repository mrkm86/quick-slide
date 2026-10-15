# 0023 自動のテストは、まず Windows だけで回す

## 採用

GitHub Actions（`.github/workflows/test.yml`）で、PR のときに Windows でテストを回す。
回すのは `bash tests/run.sh` と、エンジンのテスト（`skills/*/tests/run.sh`）。本物の Codex・Claude は呼ばない（今のテストのまま）。
公開前の検査（`tools/public_check.py tree`）は別の workflow（`public-check.yml`）で、どの PR でも Linux で回す。

- **main への push では回さない。** PR で回したのと同じ中身で、1 回 25 分あまり（Windows は 2 倍で数える）
- **試験が読まない文書（`docs/`・`README.md`・`AGENTS.md`・`LICENSE`）だけの変更では回さない。** `CHANGELOG.md` は
  版の試験（`test_version`）が形を見るので回す。公開前の検査は文書こそ要るので、分けて必ず回す。
  必須のチェックにするときは、文書だけの PR で Windows の回が始まらず「待ち」のままになることに気をつける
- **リリースの PR でも回さない。** `tools/release.py prepare` が書くのは `CHANGELOG.md` と版の置き場 3 か所
  （`src/quick_slide/__init__.py`・マニフェスト 2 つ）だけで、これらを読む試験は版の試験だけ（OS に依らない）。
  1.0.0 の PR では見出し 1 行のために 25 分待った。形は `public-check.yml` の `release.py check` が Linux で毎回見る。
  ほかのファイルが混ざった PR は今までどおり回る（GitHub は、変わったファイルが全部 `paths-ignore` に当たるときだけ飛ばす）。
  そのため、ふだんの PR で `CHANGELOG.md` に 1 行足しても、コードの変更と一緒なら回る

- **macOS はリポジトリを公開したあとに足す。** 非公開のあいだは Actions の無料枠（月 2,000 分）を使い、
  macOS は 10 倍、Windows は 2 倍で減る。決めたときの見込みは、両方だと 1 回およそ 100 分ぶんで月 20 回ほど、
  Windows だけなら 1 回 16〜24 分ぶんで月 80〜120 回ほど。実際の Windows は 1 回 25 分あまり（2 倍で 50 分ほど）で、
  月 40 回ほど。macOS は手元でいつも回している。
  使用料の上限は既定で 0 ドルなので、使い切っても請求は来ず、回らなくなるだけ
- **最初は必須のチェックにしない。** Windows で通るのを確かめてから、main の保護で必須にする
- 依存ソフトは `quick_slide_setup.py --setup` と同じもの（Python のパッケージ・puppeteer-core・Chrome の headless shell・
  Playwright の Chromium）を使い捨ての置き場に入れ、エンジンには環境変数（`HTML_SLIDE_NODE_MODULES`・`HTML_SLIDE_CHROME`）で教える。
  画面の試験（`tests/live`）は、ランナーに入っている Chrome を使う
- サーバと画面の試験は 2 つに分ける。本物の Chrome やサーバのプロセスを起こして時間を待つ試験（`tests/live`・再起動・
  ページの操作）は 2 並列で先に回し、ほかは 4 並列。4 並列で全部を回すと、回ごとに違う試験が遅れて落ちた
  （落ちた試験を単独で 5 回回すと通る）。2 並列で落ちなくなれば 4 並列に上げる。組は workflow の `TIMING_GROUP`、
  分けるのは `tests/run.sh` の `-k`（選ぶ）と `-x`（除く）
- 手で起こすときは、回す 1 行（`only`）を渡せる。Windows だけで落ちる試験を、全部を回さずに確かめる
- 公開前の検査には名前の一覧を渡さない（一覧は公開できない）。見るのはメールアドレスと、その機械のもの。
  push のあとに動くので、止めるのは push 前の hooks の役目で、ここはすぐ気づくための最後の砦

## 却下

macOS と Windows の両方を今から回す: 非公開のあいだは無料枠を月 20 回ほどで使い切る。
Linux で回す: 利用者の機械は macOS と Windows で、Windows で壊れたことに気づくのが目的。
`--setup` そのものを CI で走らせて依存ソフトを入れる: CLI の在りかやログインの確かめまで巻き込み、落ちたときに試験の失敗と見分けにくい
（`--setup` は `tests/test_p5_setup.py` と `QUICK_SLIDE_FRESH_E2E=1` の通しが見る）。
