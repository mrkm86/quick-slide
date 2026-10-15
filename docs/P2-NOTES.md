# P2（会話から 1 冊）のメモ

状態: P2 で決めたこと・仮置きにしたことの控え。ADR に切り出すまでの置き場。

## 流れ

```
会話（skills/quick-slide）─ POST /api/intakes ─▶ デッキ（モード固定）・取り込み・起動・描いた回・仕事を 1 トランザクションで
                                                     │
ワーカー（サーバの中のスレッド）が仕事を取る ──────────┘
  ① 確かめる   ピッチだけ `image_slide.py auth`（Codex のログイン）。だめなら台本係を呼ぶ前に止める
  ② 書く       台本係（claude -p / codex exec）→ decks/<デッキ>/scripts/<版>.json
  ③ 検査       ページの id → エンジンの `check`。rc=2 なら `NG:` の行を返して書き直させる（既定 3 回まで）
  ④ 描く       エンジンの `deck --out decks/<デッキ>/runs/<回>/`。標準エラーの行から進みを拾う
  ⑤ 取り込む   エンジンの終わり方を控えてから、slides/slide-NN.png を assets/ にハードリンクで張って資産にする
  ⑥ 終わり     done になった回だけが画面に出る
```

## 置いたもの

| パス | 中身 |
|---|---|
| `src/quick_slide/db/migrations/0002_scripts_runs.sql` | intakes・scripts・script_pages・page_ids・runs・run_pages・jobs |
| `src/quick_slide/store/intakes.py`・`store/runs.py` | 取り込み（送り直しは同じデッキ）・台本の版・描いた回・仕事・起動時の戻し |
| `src/quick_slide/writer/` | 台本係（指示文・`claude` / `codex` の呼び方・JSON の取り出し） |
| `src/quick_slide/render/runner.py` | エンジンの `auth`・`check`・`deck` を呼ぶ・進みを読む・終了コードを写す |
| `src/quick_slide/worker/` | ワーカー（スレッドの組）・段の流れ・子プロセス・実行ファイルの在りか |
| `src/quick_slide/engines.py`・`childenv.py` | モード → エンジンの引数・エンジンの印 ／ 子プロセスの環境（API キーを消す） |
| `src/quick_slide/server/intake_routes.py` | `POST /api/intakes` |
| `skills/quick-slide/` | 会話の入口（SKILL.md・`scripts/quick_slide_start.py`） |

## 決めたこと

- **ワーカーはサーバの中のスレッド**（既定 2 本・設定 `worker.parallel`）。v1 の別プロセスの監督と錠は持たない。
  サーバは置き場ごとに 1 つ（`runtime/server.lock`）なので錠は要らず、仕事を作った口が `wake()` で直接起こす
  （取りこぼしても 5 秒ごとに見に来る）。1 回の例外はその仕事の失敗にしてスレッドは回り続け、台帳が読めないときは待ちを
  1〜30 秒に広げる。止めるとき（`close()`）は子プロセスをグループごと止め、仕事を失敗に書いてから台帳を閉じる
- **段は始める前に台帳に書く**（runs: queued → writing ⇄ checking → rendering → importing → done / failed）。
  エンジンが終わったこと（importing。終わり方は `engine_result_json`）と、取り込んで見せたこと（done）は別の段。
  **画面に出すのは done の回だけ**（描いている間の進みは帯の 1 文が言う）
- **サーバが途中で落ちたら**（起動時の掃除）: 書く・検査の段 → 失敗にする（台本係の LLM を黙ってもう一度呼ぶと、
  利用者の知らないところでサブスクの枠を使うため）。描く段 → 待ちに戻して描くところから（描き終えた絵はエンジンの
  キャッシュから使い回す）。取り込む段 → 取り込むところから。戻すのは 3 回まで（止まり続ける仕事は理由つきで失敗）。
  「働いている」のまま残る仕事は無い。落ちたサーバの子のエンジンは、出力の口が切れたところで止まる
- **台本係の LLM**: 設定 `writer`（`auto` / `claude` / `codex`）。`auto` は会話の出所（`origin.runtime` の claude_code・codex）と同じもの、
  出所が分からなければ手元にあるもの（claude → codex）。**出所や設定で決まった CLI が無ければ、黙って替えずに止まり**、
  何を入れて何にログインするかを言う。実行ファイルは `QUICK_SLIDE_CLAUDE`・`QUICK_SLIDE_CODEX_TEXT` で差し替えられる
- **サブスクだけ**: 台本係・エンジンの子プロセスには `OPENAI_API_KEY`・`ANTHROPIC_API_KEY`・
  `AZURE_OPENAI_API_KEY`・`CODEX_API_KEY` を渡さない（`childenv.py`。入れ子の会話の印も消す）。
  OAuth のトークン（`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN`）は渡す。会話しているアプリが Claude Code を
  このトークンでログインさせていることがあり、消すと台本係が「ログインしていない」で止まった（2026-10-07 の本物の通しで発覚）
- **ページの同一性**は台本の各ページの `id`（両エンジンが受ける共通のキーで、描くことには使わない）。台本係に付けさせ、
  書き直すときは残すページの id を変えさせない。無い・形が違う・重なるときはエンジンに渡す前に `NG:` で返す。
  page_ids は (デッキ, id) で引くので、同じ id は同じページ
- **再現の記録**（`runs.record_json`）: 設定の控え（台本係・スタイル・画風・画像の有無・枚数・テーマとロゴ＝いまは無し）・
  台本の版と sha256・エンジンの argv・エンジンの印（スキルのフォルダの中身の sha256。取れればリポジトリの HEAD と dirty）・
  描く前の確かめの rc。エンジンの終わり方は `engine_result_json`
- **送り直し**: 同じ会話の識別子で、同じ原稿・題・属性が 10 分以内に届いたら、前のデッキと仕事を返す
  （開く URL だけ新しく出す。`force_new_deck` なら必ず作る）。識別子の無い投入は毎回新しいデッキ
- **画面に出す絵は描いた回のフォルダの `slides/slide-NN.png`**（エンジンのキャッシュは使わない）。配信は `assets/` の下だけなので、
  `assets/decks/<デッキ>/<回>/slide-NN.png` にハードリンクで張る（複製しない）。描いた回のフォルダが成果物として残る
- **終了コードの写し方**: 0 → done ／ 1 → 絵があるページだけ ready・無いページは failed、1 枚も無ければ failed
  （プレゼンの 1 は描けなかった画像の枠を仮の枠で組んだもの）／ 2 → failed（検査は通っていた）／ 4 → failed・止まっている帯に
  `codex login`（Node・Chrome の不足と読めたらその案内）／ それ以外・時間切れ・中断 → failed。途中で止まった回の絵は取り込まない
- **進み**: ピッチは `N枚（描く…）` と `✓ / ✗ N枚目` の行からページごとに（帯は「ページを描いています（3/5）」）。
  プレゼンは画像の枠の数（`画像の枠 …`・`✓ N.`）と「スライドを組んでいます」。ページごとの印（drawn）は台帳に書くが、
  画面に出すのは done の回だけなので、いまは帯の数字でだけ見える
- infographic（ピッチ）は台本の全ページを 1 枚に描くので、描いた回のページは 1 枚（id は `infographic`）

## 確かめたこと（`bash tests/run.sh`）

- 偽物の台本係（claude 風・codex 風。API キーが届いたら答えない）と偽物のエンジンで、段・書き直し・終了コード・止まった仕事の戻し
- 本物のエンジンで両モード（プレゼンは Node と Chrome が無ければ skip）。絵を描く codex は各エンジンの偽物
- 本物のサーバのプロセスを描いている最中に SIGKILL して起こし直す
- 会話の入口のスクリプトが使い捨ての置き場でサーバを起こし、投入し、1 冊できる
- 本物のヘッドレス Chrome で、描いている最中に開いた画面に、読み込み直さずにページの画像が出る・例外が出ない・ホームのカードに先頭の絵

## 確かめていないこと（本物の CLI を呼ばないため）

- `claude -p` の旗: `--output-format json`・`--tools ""`・`--safe-mode`・`--strict-mcp-config`・`--disable-slash-commands`・
  `--no-session-persistence`。出力は ADR 0023 で `--output-format stream-json --verbose --include-partial-messages` に変更し、途中経過を保存するようにした。手元の `claude --help` に載っている旗だけを付ける（`--help` を読んで決める）。標準入力で指示文を渡す
- `codex exec` の旗: `-c features.plugins=false -c features.hooks=false -c features.apps=false -c forced_login_method="chatgpt"
  -a never exec --ignore-user-config --ephemeral --skip-git-repo-check -s read-only -C <作業の根> --color never --json -o <最後の答え> -`
- 応答が失敗のときの読み分け（ログイン・利用枠）は文言の正規表現で、本物の文言では確かめていない

## 仮置き（P3 以降）

- 台本の版は画面に出さない。直す・版の採用・ページの差し込み・並べ替え・書き出しの配布は P3〜P5（`not_ready_in_v2`）
- ブランド → テーマ・ロゴは P4（いまはエンジンの既定）
- 複製したデッキには描いた回を写さない（ページ 0 枚の複製になる）
  → 2026-10-09 に直した: いまの姿（見せる回とその台本の版・ページの絵）を写す。版の履歴は写さない。プレゼンの描いた絵は、複製の台本のフォルダの区画へ張って引き継ぐ（`render/caches.seed_duplicate`。組み直しても描き直さない）
- ごみ箱へ移したデッキの、走っている仕事は止めない（終わってから資産を足す。完全に削除は終わるまで断る）
- 落ちたサーバの子のエンジンを、起こし直したサーバは探さない（出力の口が切れて止まる前提）
