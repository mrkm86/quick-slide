# AGENTS.md — Quick Slide v2 で作業するエージェントへの指示

## 0. 最初に守ること

1. **このリポは配布物そのもの。** プラグインの登録はリポジトリの丸写しで、`.gitignore` は見られない。Codex は `.git` ごと配る（履歴も出る）。
   - 直下に置くのは配ってよいものだけ。実験・検分の置き場はリポジトリの外（`../quick-slide-v2-scratch/` など）
   - **人名・社内の名前・顧客や他製品の名前・社内のパスを書かない。** コード・コメント・テスト・文書・コミットメッセージのどれにも
   - コミットしていない編集もそのまま配られる
2. **画像は ChatGPT のサブスク枠で描く。** 実行時に絵を描くのは `codex` CLI。従量課金 API を既定にしない
3. **置き場は `places.py` を通す。** データの置き場（`QUICK_SLIDE_HOME`・既定 `~/.quick-slide/`）やキャッシュの場所を、ほかの場所に書かない。
   文書でも絶対パスを書き散らさない（改名・移管のときに直す場所を1か所にする）
4. **仕様の判断に迷ったら実装せず質問する。**

## 1. 構成

| パス | 中身 |
|---|---|
| `.claude-plugin/`・`.codex-plugin/`・`.agents/plugins/` | プラグインのマニフェスト（Claude Code と Codex） |
| `skills/html-slide/` | エンジン: 編集できて動く PPTX |
| `skills/image-slide/` | エンジン: 画像生成で1枚ずつ描く資料 |
| `skills/quick-slide/` | 会話の入口（原稿を整えて投入し、スライドの画面の URL を返す） |
| `src/quick_slide/`・`web/app/` | 常駐サーバ（台本係・描き係のワーカーを含む）と画面（段の進め方は `docs/DESIGN.md` §7、控えは `docs/P1-NOTES.md`〜`docs/P5-NOTES.md`） |

エンジンは状態を持たず、ほかのスキルを呼ばない（素材はファイルで受ける）。

## 2. エンジンの正本はここ

- `skills/html-slide/` と `skills/image-slide/` が正本。ほかの環境からシンボリックリンクで使われていることがある
- そのため **このリポは普段 `main` を checkout したままにする。** 試しは `git worktree` で行う（ブランチを切り替えると、リンク越しに使っている側も変わる）
- 2つのエンジンで同じ中身のファイル（`scripts/cache.py`・`places.py`・`outputs.py`）は、image-slide のテストがバイト単位で見比べる。片方を直したら両方に写す

## 3. テスト

```bash
bash skills/image-slide/tests/run.sh   # 本物の Codex は呼ばない
bash skills/html-slide/tests/run.sh
bash tests/run.sh                      # サーバと画面（本物のヘッドレス Chrome で画面を開く試験を含む）
QUICK_SLIDE_FRESH_E2E=1 bash tests/run.sh   # 上に加えて「配った機体」の通し（写しと空の HOME で導入から書き出しまで。puppeteer-core と Chrome を落とす）
```

テストは使い捨ての `QUICK_SLIDE_HOME` とキャッシュで走り、実の置き場を読まない・書かない。

## 4. 進め方

- 設計判断はスキルの `docs/adr/` か、リポ全体なら `docs/adr/`（1判断1ファイル。採った案と却下した案、その理由）
- **動かして確かめていないものを「完了」と書かない**
