# 0023 自動のテストは、まず Windows だけで回す

## 採用

GitHub Actions（`.github/workflows/test.yml`）で、PR と main への push のときに Windows でテストを回す。
回すのは `bash tests/run.sh` と、エンジンのテスト（`skills/*/tests/run.sh`）と、公開前の検査（`tools/public_check.py tree`）。
本物の Codex・Claude は呼ばない（今のテストのまま）。

- **macOS はリポジトリを公開したあとに足す。** 非公開のあいだは Actions の無料枠（月 2,000 分）を使い、
  macOS は 10 倍、Windows は 2 倍で減る。両方だと 1 回およそ 100 分ぶんで月 20 回ほどしか回せないが、
  Windows だけなら 1 回 16〜24 分ぶんで月 80〜120 回ほど回せる。macOS は手元でいつも回している。
  使用料の上限は既定で 0 ドルなので、使い切っても請求は来ず、回らなくなるだけ
- **最初は必須のチェックにしない。** Windows で通るのを確かめてから、main の保護で必須にする
- 依存ソフトは `quick_slide_setup.py --setup` と同じもの（Python のパッケージ・puppeteer-core・Chrome の headless shell・
  Playwright の Chromium）を使い捨ての置き場に入れ、エンジンには環境変数（`HTML_SLIDE_NODE_MODULES`・`HTML_SLIDE_CHROME`）で教える。
  画面の試験（`tests/live`）は、ランナーに入っている Chrome を使う
- 公開前の検査には名前の一覧を渡さない（一覧は公開できない）。見るのはメールアドレスと、その機械のもの。
  push のあとに動くので、止めるのは push 前の hooks の役目で、ここはすぐ気づくための最後の砦

## 却下

macOS と Windows の両方を今から回す: 非公開のあいだは無料枠を月 20 回ほどで使い切る。
Linux で回す: 利用者の機械は macOS と Windows で、Windows で壊れたことに気づくのが目的。
`--setup` そのものを CI で走らせて依存ソフトを入れる: CLI の在りかやログインの確かめまで巻き込み、落ちたときに試験の失敗と見分けにくい
（`--setup` は `tests/test_p5_setup.py` と `QUICK_SLIDE_FRESH_E2E=1` の通しが見る）。
