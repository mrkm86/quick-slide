# P1（土台）のメモ

状態: P1 で決めたこと・仮置きにしたことの控え。ADR に切り出すまでの置き場。

## 置いたもの

| パス | 中身 |
|---|---|
| `src/quick_slide/` | サーバ（`server/`）・アクセス層（`store/`）・DB（`db/`）。v1 の生成・企画・合成・書き出し・ワーカーは持たない |
| `web/app/` | 画面（v1 の丸写し。直したところは下） |
| `tests/` | `bash tests/run.sh`。`tests/live/` は本物のヘッドレス Chrome で画面を開く（Chrome が無ければ skip） |

起動: `PYTHONPATH=src python3 -m quick_slide.server start`（`status`・`stop`・`serve`・`launch ensure`）。ポート 7801。

## 決めたこと

- **置き場はサーバでは `paths.py` の 1 か所。** 根っこはエンジンの `places.py` と同じ規則（`QUICK_SLIDE_HOME`・既定 `~/.quick-slide/`）。
  DB は `db/quick-slide.db`。`engines/` はエンジンの持ち物なのでサーバは作らない
- **DB は作り直した**（`db/migrations/0001_initial.sql`）。ブランド・スタイル（改訂は不変）、デッキ（`mode` は `presentation` / `pitch`。
  作るときに決め、あとから替えない）、タグ、資産、起動、出来事、ごみ箱。台本・描いた回は P2 の移行で足す
- **P2 以降の口は残して「まだ使えない」と答える**（`server/later.py`。503・`not_ready_in_v2`・日本語の本文。v1 と同じエラーの形）。
  画面は `capabilities` で押せなくしてあり、理由は 1 か所に 1 行出る
- **ページの無いデッキでもスライドの画面が開く。** `GET /api/decks/<id>` と `/progress` は画面が読む項目を全部返す
  （ページ 0 枚・止まっていない・動いていない）

## 仮置き（P4 でエンジンへ写すときに替える）

- ロゴの角はエンジンの `--logo-position` と同じ 3 つ（右下・左下・右上）。保存の形は `logo.placement = {"corner": ...}`。
  v1 の予約枠（`clean_area`）と枠の上下限は持たない。見本のロゴの位置は html-slide の既定の寸法を写した近似
- スタイルの「使うパターン」は「おまかせ」（`auto`）の 1 つだけ。エンジンの型の一覧に替える
- 書式（ページ番号・透かし・フッタ）の登録は押せないまま

## 画面（web/app）で直したところ

- コメントの中の人名を「利用者」に置き換えた（動きは変わらない）
- `settings.js` の名前の欄の例（placeholder）を一般的な名前にした
- `settings.js` のスタイルの複写: 保存の要求に複写元の改訂を添えるようにした（v1 では添えておらず、画面から新しいスタイルを作ると必ず断られていた）

## レビューを受けて直したところ

- **ループバックかどうかはアドレスの意味で決める**（`settings.is_loopback_host`。`0:0:0:0:0:0:0:1`・`127.0.0.2`・
  `::ffff:127.0.0.1`・大文字の `LOCALHOST` もループバック）。bind の可否・`host_names` の禁止・`Host:` の許可・health の
  `bind_scope`・起動時の bind 結果の確かめ・画面の「この機械の中だけ」が、みなこの 1 つを見る。「ぜんぶの口」も同じく
  `is_wildcard_host`（`0:0::0` なども拒む）。ループバックに開いたときも、bind したアドレスそのものは `Host:` に許す
- **COMMIT が落ちたら巻き戻す**（`db/connection.Transaction`）。遅延外部キーの違反は COMMIT で出る。巻き戻さないと
  共有の接続にトランザクションが残り、次の操作が入れ子とみなされて確定しない
- **ブランドの保存は「送られたキーが勝ち、送られなかったキーは前の改訂から持ち越す」**（`settings_routes.merge_brand_config`）。
  編集画面は `schema_version`・`colors`・`logo`・`footer` だけを組み直して送るので、`fonts` などは持ち越す（画面から消せないものは
  画面から消せないまま）。`colors` の中は分ける — 画面が扱う色（基本色・強調色・情報色・詳しく指定する 5 色）は送られたものだけで、
  送らない＝算出に戻す。画面が扱わない役割色（`semantic` の success / warning / error と `gray`）は、前の改訂で
  **算出値と違う（＝人が決めた）値だけ**を持ち越し、算出値は新しい基本色で算出し直す（`palette.custom_roles`）。
  見本の口（`/preview`）はどのブランドかを受け取らないので、持ち越す役割色は見本には出ない（保存の応答の見本には出る）
- **ごみ箱へ移すときは manifest が先**。書けなければ何も変えずに断る。隠したあと記録の前に止まった削除（`pending`）は、
  起動時の掃除（`recover_interrupted_deletions`・health の `startup_sweep.deletions_recovered`）が `failed`（戻せる）にし、
  退避の置き場へ動いていた画像を `trashed` にする。古い順序が残した manifest の無い `pending` は、退避の置き場に
  ファイルが無ければ、復元でデッキを戻すだけにする
- **移行の確かめ**: 外部キーを切る移行は COMMIT の前に `foreign_key_check` を通し、参照先の無い行があれば丸ごと巻き戻す。
  履歴（`schema_migrations`）は `user_version` とちょうど 1..版 で合うこと（表が無い・行が欠ける・版より先の行がある・名前が違う
  は起動を拒む）。版と履歴は 1 つの読み取りの中で読む（並行起動で、ほかのプロセスの移行の途中を見ない）
