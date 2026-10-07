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
