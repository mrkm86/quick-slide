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

## 追記（2026-11-19）: push のたびには回さず、main に入れる前に回す

- **回るのは、PR の下書きを外したとき（Ready for review）と、ラベル `windows` を付けたとき。** push のたび（`synchronize`）と、
  PR を開いたとき（`opened`）には回さない
- 理由: 1 回 25 分ほどかかり、同じ PR に続けて push すると前の回を取り消す。レビューの直しを 6 回 push した PR では、
  回が毎回取り消され、最後の push まで 1 回も終わらなかった（待ち時間と分数だけを使った）。途中の回は、手元の macOS の
  テストと査読で足りていて、Windows で見たいのは「main に入れる中身」だけ
- 進め方: PR は下書きで出す。レビューの直しが収まったら下書きを外す（ここで回る）。そのあとに push したら、
  ラベル `windows` を付け直す（付いていれば、外してから付ける）。回っていないコミットには Windows の結果が付かないので、
  PR の画面で見て分かる。マージするのは、最後のコミットに Windows の結果が付いてから
- 下書きにせずに開いた PR は、ラベルを付けるまで回らない
- 文書だけ・リリースだけの PR で回さない決まり（`paths-ignore`）と、手で起こす道（`only`）は、今までどおり
- 却下: **main への push で回す**（入れたあとに壊れたと分かる。直すのにもう 1 本 PR が要る）。
  **マージの待ち行列（merge queue）で回す**（個人のアカウントのリポジトリでは使えない）。
  **査読の承認（approve）で回す**（査読は手元の端末で行っていて、GitHub の上の承認を使っていない）

## 却下

macOS と Windows の両方を今から回す: 非公開のあいだは無料枠を月 20 回ほどで使い切る。
Linux で回す: 利用者の機械は macOS と Windows で、Windows で壊れたことに気づくのが目的。
`--setup` そのものを CI で走らせて依存ソフトを入れる: CLI の在りかやログインの確かめまで巻き込み、落ちたときに試験の失敗と見分けにくい
（`--setup` は `tests/test_p5_setup.py` と `QUICK_SLIDE_FRESH_E2E=1` の通しが見る）。
