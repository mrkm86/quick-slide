# デザインテンプレートを本番へ接続する（Issue #22・実装設計案）

状態: PR 1〜4 に加え、現在の HEAD には投入・控え・通常素材準備と台本検査のコードがある。描画は停止中。
2026-10-18 の決定に伴う変更は未実装。この文書と [変更設計](design-template-static-resources.md) を実装担当へ渡す。
試作（`tools/preview_settings.py`・`web/app/settings-preview.*`・その試験）は、本番の設定画面ができた時点でリポジトリから外した（配布物に入れないため）。以下の試作への言及は経緯として残す。

仕様の正本は [`design-template-contract.md`](design-template-contract.md)。この文書は、決定済みの契約を本番の保存・設定画面・会話の入口・ワーカーへ接続する順序と方法を提案する。
[`issue-22-input-design.md`](issue-22-input-design.md) は経緯として読む。初期案の入力フラグや個別画像登録を復活させない。
ブランド ZIP は ADR 0004、旧スライドスタイルの互換は ADR 0009 に従う。ブループリントと Motion の実装は対象外。
以下の新しい関数名・列名・ルート名は実装案であり、既存機能の説明とは分けて記す。

## 1. 読んで確認した接続点

| 現在の根拠 | 確認した挙動と接続に必要な変更 |
|---|---|
| `design_templates.py`: `validate`・`_upgrade`・`compile_document` | 保存文書 v1/v2 を v3 へ変換し、4項目から共通指示を作る。コンパイル結果は `instructions_compiled`・`render_ready=false`。素材準備を実行するものではない |
| 同: `to_markdown`・`from_markdown` | Markdown の形式版は 1。保存文書の schema 3 とは別。受け渡しは版・由来・ブランドを含まず、添付がある文書の書き出しは断る。前付けの `id` は現在のコードでは許可される（§9 の確認点） |
| `tools/preview_settings.py`: `Handler.store_template`・`document_digest`・`revision_list` | 保存と取り込みが同じ保存関数を通り、文書の指紋で競合検出する。最新の版の上書きと、新しい版の採番を分け、版とブランドの控えを持つ。外部検証フォルダとメモリが保存先 |
| `web/app/design-template-editor.js`: `SS.designTemplateEditor`（内部の `saveDocument`・`openVersions`） | 4タブ、既定の上書き、過去版の閲覧とコピーを実装。通信は独自の `request` で `/preview/` を呼ぶ |
| `web/app/settings-preview.js`: `basicBrand`・`duplicateTemplate`・`deleteTemplate`・`exportTemplate`・`importTemplate` | 本番のブランド画面を呼び出した後でテンプレート一覧を挿入する試作。`SS.api` とナビゲーションも差し替えるため、そのまま本番でロードしない |
| `store/brands.py`: `add_brand_revision`・`delete_brand`、`store/styles.py`: `add_style_revision`・`delete_style` | 改訂を追記し、所有者の current を進める。削除は `deleted_at`。テンプレートも store 内に SQL を閉じる |
| `store/intakes.py`: `create_intake`・`run_args_from_attributes` | 原稿・題・属性などのハッシュと会話 HMAC で送り直しを検出する。新しいデッキ・回・仕事を同一トランザクションで作る。新規属性の許可リスト拡張が必要 |
| `store/runs.py`: `create_run`・`update_run_args`・`set_run_record` | 回は引数と再現記録を持つ。記録はトップレベルのキー単位で併合するため、準備記録の更新で前の内容を消さない設計が必要 |
| `render/look.py`: `resolve`・`_current_revision`、`worker/pipeline.py`: `settle_look` | 仕事の開始時に設定を解決し、再開時は解決し直さない。現在は削除されていないブランド・旧スタイルの最新改訂へ進む。テンプレート利用回には固定した控えを使う分岐が必要 |
| `writer/prompt.py`: `_rules`・`_edit_rules`・`build_prompt`・`build_revision_prompt`・`build_insert_prompt` | 道具・ファイル読み書きを禁じる。初稿では `_characters`・`_layout`・画像パスも禁じる。テンプレート指示と準備結果の専用の入力を追加する |
| `writer/backends.py`: `CLAUDE_WANTED_FLAGS`・`ask_claude`・`_codex_command` | Claude は道具を空にし、Codex は読み取り専用の環境で呼ぶ。台本係へパスを渡すだけでは素材の読解にならない |
| `writer/patch.py`: `_check_page`・`parse_revision`・`apply_revision`・`parse_insert` | 画像パスの許可と禁止キーを検査し、対象ページだけを差し替える。準備済み参照 ID の検証と、省略時の継承を追加する |
| `engines.py`: `Engine.check_command`・`deck_command`・`revise_command` | 検査・描画・修正のコマンドを組む接続点。共通の実行準備から同じ引数を渡す |
| `server/app.py`: `_check_host`・`_check_origin`・`_check_mutation`・`ROUTES`、`server/settings_routes.py`: `editable`・`boundary` | Host・Origin と更新要求のセッショントークンを検査する。設定更新は期待した改訂 ID が不一致なら 409。新ルートもここを通す |
| `web/app/settings.js`: `brandEditor`・`exportBrand`・`importBrand` | 既存ブランド詳細と ZIP の操作の接続先。ブランドの未保存状態は `SS.settingsLeave` で管理する |
| `brand_package.py`: `build`・`read`・`_read` | 現在は ZIP v1 のみ。許可した名前・個数・サイズ・圧縮率・画像ハッシュを検査し、ディスクへ展開しない |
| `skills/quick-slide/scripts/quick_slide_start.py`: `detect_conversation`・`build_payload`・`request`・`run` | 会話識別子の検出、HTTP 投入、JSON 応答を持つ。`request` は現在応答を JSON として読むので、Markdown 書き出しにはバイト列を受ける共通通信部品が必要 |

## 2. PR の分け方と完了条件

各 PR の完了は、その範囲を実行して確かめた時点とする。保存だけ完成しても「テンプレートで資料を作れる」とは案内しない。
PR 1 → 2 の後で PR 3・4 を進められる。後続の順序は PR 5 → 6a → 7a → 7b → 8。6b は採らなかった（ADR 0031）。

| PR | 範囲 | 完了の条件・動かして確かめる方法 |
|---|---|---|
| 1 保存 | 2表、store、共通の差分・保存方針、移行 | 空 DB と現行 DB から起動。上書きでも改訂行が不変、新版採番・過去版コピー・競合・論理削除・同文の無変更・会話の分岐を store 試験で確認。移行再実行・途中失敗のロールバックも確認 |
| 2 API | 保存サービス、一覧・版・複製・削除・Markdown・2段取り込み・検証用コンパイル | 一時データの本番サーバへ HTTP で一巡。試作の API 試験を本番用に移植し、トークン欠落、Origin、競合、check の無書き込み、改名したファイルの取り込みを確認 |
| 3 設定画面 | 既存ブランド詳細への一覧、編集、版の一覧、取り込み・書き出し | ヘッドレス Chrome で保存→再読み込み→旧版閲覧→コピー→競合→複製→削除→再取り込み。4タブの入力保持、狭い画面、フォーカス、未保存離脱、既存ブランド編集の回帰を確認 |
| 4 会話 CLI | `quick_slide_template.py` の7操作、共通通信部品、SKILL の説明 | 本番サーバを一時データで起動し CLI を子プロセスで実行。JSON・終了コード、同文、同じ会話、別会話、`--overwrite`、dry-run と本保存間の競合、UTF-8 の書き出しを確認 |
| 5 投入と控え | start の2フラグ、intake の解決・同一判定、回へのコピー、look の固定経路 | 待機中に最新の上書き・ブランド更新・削除があっても受け付けた中身が変わらない。修正・差し込み・再開で元の控えを使う。テンプレート無しの互換試験も通す。準備未実装の入力は理由を出して止める |
| 6a 素材準備と台本 | §6 の担当方式を決め、読解・台本への入力・参照検査・必要素材のコピーを実装 | 偽の台本係と限定読取部品で、説明書きの優先順・欠落・未対応・依存先・指示の原文・省略と空配列・分割を確認。中断再開と元ファイル変更も確認 |
| 7a IMAGE 描画と直接実行 | モード別変換、共通 command builder、prepared-run、完成画面の案内 | 同一モード・確定台本・素材からサーバと直接入口の engine.json・設定・素材ハッシュ・正規化した引数が一致。check/deck/revise の参照順と上限も一致。公開可能な小さな例で IMAGE / HTML の実生成と修正を目視確認 |
| 7b HTML 描画 | HTML の変換・上限・共通 builder | 同モード内の入力一致と公開可能な例の実生成・直しを確認 |
| 8 ブランド ZIP v2 | テンプレート同梱、含める選択、2段取り込み、v1 互換 | v1 の既存 fixture と v2 の往復、衝突ごとの選択、検査後の他画面更新、全体ロールバック、不正 ZIP、変換済み旧添付のリソースとしての往復を確認。画面で既定オン・パス一覧・欠落通知を確認 |

各 PR で対象の unittest を先に実行し、統合時は `bash tests/run.sh` を通す。エンジン接続の PR では
`bash skills/image-slide/tests/run.sh`・`bash skills/html-slide/tests/run.sh`・`bash skills/illust/tests/run.sh` も通す。
配布前に `QUICK_SLIDE_FRESH_E2E=1 bash tests/run.sh` で空の環境から確認する。Windows の CI を完了条件に含め、ブラウザ試験が skip された場合は成功と混同せず記録する。
実モデルを呼ばない試験と実生成の品質確認は別に報告する。

既存の `tests/test_design_templates.py` は原文保持・旧形式変換・名前重複・Markdown 往復・共通コンパイラの回帰として維持する。
`tests/test_template_preview_server.py` の上書き・旧版固定・競合・削除後の履歴保持・取り込みの試験は本番にも対応物を作る。
試作のファイル採番の欠番処理は DB へそのまま移さず、トランザクション失敗で確定しないことを試験する。

## 3. 保存: 版番号と不変の改訂 ID

### 3.1 表の形（提案）

利用者が見る `revision`（v1、v2）と、保存のたびに変わる内部 `revision_id` を分ける。
「v2 を上書き」は v2 の新しい改訂行を追記する操作とし、既存行を UPDATE しない。
これで契約の「改訂は不変」と画面の「最新の版は上書き」を両立させる。

| 表 | 列・制約 |
|---|---|
| `design_templates` | `id TEXT PRIMARY KEY`、`brand_id TEXT NOT NULL REFERENCES brands(id)`、`current_revision_id TEXT`（改訂を指す）、`created_at TEXT NOT NULL`、`updated_at TEXT NOT NULL`、`deleted_at TEXT NULL` |
| `design_template_revisions` | `id TEXT PRIMARY KEY`、`template_id TEXT NOT NULL REFERENCES design_templates(id)`、`revision INTEGER NOT NULL CHECK(revision>=1)`、`save_sequence INTEGER NOT NULL CHECK(save_sequence>=1)`、`document_json TEXT NOT NULL`、`content_sha256 TEXT NOT NULL`、`based_on_revision_id TEXT NULL REFERENCES design_template_revisions(id)`、`brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(id)`、`origin_runtime TEXT NULL`、`conversation_hash TEXT NULL`、`save_kind TEXT NOT NULL`、`created_at TEXT NOT NULL` |

`UNIQUE(template_id, save_sequence)` と `(template_id, revision, save_sequence)` の索引、ブランド別一覧用の索引を置く。
同じ版に改訂が複数あるので `UNIQUE(template_id, revision)` は置かない。current が同じ所有者の改訂を指すこと、
`based_on_revision_id` が同じテンプレートの改訂を指すことは store 内で検査する。作成時の循環参照は同一トランザクション内で扱う。

名前・説明・対応モードは `document_json` の正本から取得する。名前の一意制約は置かない。
文書は `validate` の v3 とし、`id/brand_id/revision/based_on` と4項目だけを持つ。
内部の改訂 ID、会話 HMAC、保存方針を文書へ混ぜない。旧添付の実体解決は管理下のファイルだけに限定し、任意の URL を信頼しない。

### 3.2 保存の規則

| 操作 | 保存結果 |
|---|---|
| 新規 | サーバが ID を作り v1、`based_on=null`。取り込みでもファイルの ID は使わない（ファイルの ID は「同じテンプレートか」の照合にだけ使う） |
| 画面の保存 | 最新 vN の新しい内部改訂を追記し current を進める。vN の由来は維持 |
| 新しい版として保存 | `MAX(revision)+1`。元にした内部改訂を固定し、文書の `based_on` にはその版番号を入れる |
| 過去版をコピーして保存 | 過去版は読み取り専用。コピーした内容を最新の次の版として追記し、由来はコピー元の版。最新を上書きしない |
| 別テンプレートへ複製 | 新 ID・名前に「のコピー」・v1・`based_on=null`。旧履歴は移さない |
| 削除 | `deleted_at` を付ける。通常一覧・新規投入から外し、不変の改訂と回の控えを残す |

版番号で引くときは、その版の最大 `save_sequence` の文書と、その改訂が控えたブランド版を返す。
内部改訂 ID で引けば上書き前も一意にたどれる。過去版コピー後の保存には、コピー元の改訂 ID と現在の current の両方を渡す。
コピー元の `based_on` を利用者入力で偽装させず、選ばれた改訂からサーバが計算する。

競合検出は試作の文書指紋をやめ、`expected_revision_id` と current の一致を保存トランザクション内で検査する。
上書きでも新しい内部 ID になるため、同じ版の編集同士でも検出できる。削除・複製・取り込みにも適用する。
新規作成では対象が無いことを同じトランザクションで検査する。競合時は 409 とし、再取得後の黙った再送はしない。

中身の比較は4項目に限定した正規化 JSON のハッシュで行う。改訂・由来・URL・行の機械 ID を除き、本文は要約も空白圧縮もしない。
モードの集合と、順序に意味のないリソースの並びを正規化する。旧添付もリソースへ変換して比較する。保存済みの content_sha256 と新しい正規化での比較ハッシュは分ける（変更設計 §3）。
Markdown の読み込みで行 ID が変わっても同文と判定できるようにする。差分は「基本・構成・追加指示・リソース」の単位で返す。
最新と同文なら新しい版・内部改訂とも作らない。無変更でも期待した改訂の検査は先に行う。

会話 CLI の既定は新しい版。同じ会話が作った版が今も最新で、最後に保存した主体もその会話なら、その版へ上書きする。
会話の生値は `StoreBase.conversation_hash_for` で HMAC 化し、それ以外に残さない。
途中で別の会話・画面が編集したら自動上書きの資格を失い、新しい版にする。
識別子を取れなければ新しい版。同じ会話が昔作った過去版は上書きしない。
明示的な `--overwrite` は最新だけを対象にし、競合検査は省かない。

### 3.3 移行と置き場

`db/migrations/` の次の未使用番号に SQL を追加する（調査時点の末尾は `0010_page_previews.sql`）。
適用済み SQL は変えない。`db/migrate.py` の `migrate`・`_apply_one` が行う履歴ハッシュ検証、バックアップ、原子的な適用を使う。
既存ブランド・スタイル・デッキにテンプレートを自動作成せず、旧デッキの関連は空のままにする。
試作の `bundle.json` を本番起動時に探索・自動移入しない。通常の持ち込みは Markdown の明示取り込みとする。
旧添付は読み込み時に管理下のコピーを指す kind=file のリソースへ変換する。保存済み行を書き換える一括移行はしない。v1/v2 も同じ変換へ接続する（変更設計 §3）。

データの根とキャッシュの解決は既存の `places.py` 系を通し、サーバ側の回の置き場は `paths.py` の `DataRoot` に集約する。
`DataRoot.run_dir`・`run_settings_dir` を使い、準備用ディレクトリが要れば同クラスへメソッドを足す。
試作の検証フォルダや cwd を本番データの既定にしない。Windows のドライブ・UNC・`~/`、UTF-8、置換時のファイルハンドルを検査する。
外部リソースのパスはユーザーの設定データであり、アプリ自身のデータ置き場とは分ける。

## 4. 共通の保存とサーバ API

新設 `store/design_templates.py` の公開メソッドを、薄い保存サービス（提案: `template_service.save_template`）から使う。
GUI・CLI・Markdown 取り込み・ブランド ZIP の取り込みで分岐した SQL や版の判断を持たない。

保存サービスは、対象ブランドと生存状態の確認 → 文書の組み立てと `design_templates.validate` →
旧文書の検証・管理下コピーの解決とリソースへの変換 → 期待した改訂の検査 → 同文比較 → 保存方針と由来の決定 → 追記と current 更新を行う。
返す形は `{document, current_revision_id, action, changed_sections, message}`。
ID・版・由来は要求の編集本文ではなく操作の指定から計算する。コンパイルは保存後の確定した文書から行い、
コンパイル後にモデルを呼んだり素材を選んだりしない。長い I/O は `app.store_lock` の外へ出し、確定時に改訂を再検査する。

新ルートの案（`T` はテンプレート ID）。問い合わせの版番号は試作に合わせ `revision=N` とする。

| メソッド・本番ルート | 内容／試作との対応 |
|---|---|
| `GET /api/settings/design-templates?brand_id=…` | 一覧。`/preview/data` の templates に対応。`/api/settings` のブランド目録とは分けて取得 |
| `GET /api/settings/design-templates/T[?revision=N]` | 最新または指定版の文書・内部改訂 ID |
| `POST /api/settings/design-templates` | 新規作成。試作の ID 指定保存の初回に対応 |
| `POST /api/settings/design-templates/T` | `mode=overwrite|new_version|conversation`、編集本文、`expected_revision_id`、必要ならコピー元の改訂 ID。試作の `POST /preview/templates/T` に対応 |
| `POST /api/settings/design-templates/T/copy` | 別 ID に複製。複製元の期待した改訂と任意の新名を受ける |
| `POST /api/settings/design-templates/T/delete` | 論理削除。試作の `/preview/templates/T/delete` に対応 |
| `GET /api/settings/design-templates/T/revisions` | 版番号ごとの最新版を新しい順に返す。日時・由来・変更タブ・読み取り用文書と内部 ID。試作の同名末尾に対応 |
| `GET /api/settings/design-templates/T/markdown[?revision=N]` | Markdown 単体。試作の `/markdown` に対応。Content-Disposition と no-store を返す |
| `POST /api/settings/design-templates/import/check` | `{text, brand_id}` を検査し、同じ ID の対象・選択肢・欠落リソース・期待する改訂を返す。保存しない。試作の `/preview/template-import/check` に対応 |
| `POST /api/settings/design-templates/import` | 同じ本文・ブランド・選択・期待した改訂を再送し共通保存。試作の `/preview/template-import` に対応 |
| `POST /api/settings/design-templates/plan` | CLI の保存要求を dry-run で評価。保存関数の同じ計画部分を呼び、DB・ファイル・イベントを書かない |
| `GET /api/settings/design-templates/T/compiled?revision=N&mode=…` | 保存したテンプレート版と親の最新の保存済みブランド版から検証用 JSON。試作の `/compiled` に対応 |
| `GET /api/settings/design-templates/T/compiled/export?revision=N&mode=…` | 検証用の指示 ZIP。試作の `/export` に対応。Markdown の書き出しと区別 |
| `POST /api/settings/resource-path/pick` | 試作の `/preview/pick` に対応する OS ダイアログ。§9 の方式決定後に実装 |

`/preview/catalog` は既存の `/api/settings`、`/preview/brand` は既存のブランドプレビュー API を使う。
`/preview/files/` は既存の assets 配信に置き換える。新規添付 UI は作らないので `/preview/material` のアップロード入口は本番へ移さない。
静的ルートは ID ルートより先に登録し、`ROUTES` の ID 検証に合わせる。試作の固定 ID は本番で新規採番する ID の仕様にしない。

更新要求は既存の `X-Quick-Slide-Session`、Host・Origin の検査を通す。check・plan・pick も POST なので同じトークンが要る。
ブラウザは通常の `SS.api`、CLI はサーバ記録を読む既存通信の仕組みを使い、トークンを引数・ログ・JSON 結果に出さない。
エラーは `boundary` と同じ HTTP/JSON の形で、入力不正 400、対象無し 404、改訂・選択肢変更 409 を返す。
編集の検証エラーにはタブと項目の位置を付け、入力を保持したまま該当タブへ移る。本文・パスを診断ログやイベントへ複写しない。

## 5. 本番設定画面への移植

`settings.js` の `brandEditor` で既存の色・ロゴ・フッター・透かし・右側見本を保持し、下部にテンプレート一覧を描く。
試作の `basicBrand` の DOM 後挿入を正式な描画関数へ置き換え、一覧は既存の行の見た目、名前と版の札、対応モード、編集、⋮ とする。
⋮ は複製・エクスポート・削除。欄の先頭に追加・インポートを置く。独立した設定トップや旧スタイル設定画面を作らない。

`SS.designTemplateEditor` は専用ファイルに残し、通信と戻り先を本番の `SS.api`・ルータから渡す。
本番ルートは `/settings/brands/<brand-id>/templates/<template-id>` と追加用の入口を登録する案。
`settings-preview.js` の hash ルータ、`SS.api` の差し替え、架空の目録・ブループリント操作をロードしない。
必要な CSS と共通 DOM 部品だけを本番へ移し、試作全体の CSS に依存させない。

契約どおり4タブで入力を保持し、保存は全タブが対象。通常保存は最新の上書き、▾ で新しい版。
版の札から日時・変更タブ・`based_on` と読み取り専用の内容を確認し、過去版はコピーして新しい版にする。
保存中の追加入力は現在の `editSerial` に相当する判定で未保存として残す。
`expected_revision_id` は取得した文書ごとに保持し、競合時に自動更新しない。
ブランドとテンプレートの未保存状態を `SS.settingsLeave` に接続し、行き来で黙って捨てない。
テンプレートの保存がブランドの未保存内容を一緒に保存することも避け、コンパイル対象は親の最新の保存済みブランド版とする。生成回の再生は投入時の snapshot を使う。

リソースは畳んだカード、鉛筆で個別編集、末尾で追加、順序変更なし。名前は移行された旧添付も含むリソース内で重複不可。
旧添付の UI・プレビュー・版一覧の別枠を撤去し、通常のリソースカードで扱う。
取り込みはファイルの format で判断し、先に内容・衝突・欠落を確認してから保存する。
同じ ID は新しい版／別 ID 追加／中止、ID が無ければ v1。削除済み ID の再利用・復活はしない。
書き出すファイルにはパスがそのまま入る（仕様。書き出し前の確認は出さない）。ファイル名はサーバの Content-Disposition を使う。

試作の `?debug` は開発の確かめ用として残せるが、通常 UI に指示原文・コンパイル工程を増やさない。
有効でも保存済み版だけを表示し、`render_ready=false` と未実行の工程を示す。認可や入力検査を回避する条件にはしない。
`tools/preview_settings.py` と settings-preview 一式は開発用の試作として分離し、本番起動・実データ探索へ接続しない。
模式図は生成結果として表示しない。編集画面の情報用 notice と、完成画面での指示どおりか確かめる一文を契約どおり追加する。

## 6. 投入・実行準備・描画への接続

### 6.1 選択を投入時に固定する

`quick_slide_start.py` の `build_parser`・`build_payload` に `--design-template <名前または ID>` と
`--design-template-version <正の整数>` を追加し、`deck_attributes` の同名の選択情報へ入れる案とする。
版だけの指定は断る。版省略は最新。名前は選んだブランド内で探し、重複時は候補 ID を返して指定し直してもらう。
ID でもブランド一致を検査する。ブランド未指定時の解決方法は §9 で決める。Motion と非対応モードは投入前に断る。
テンプレートを `--style-id` へ変換しない。両者の同時指定は §9 の判断までは明示エラーとする。

`store/intakes.py` の `_validate_intake` と `create_intake` で、選択を内部改訂 ID・版番号・内容ハッシュへ解決する。
テンプレートの最新または指定版の解決、親ブランドの投入時点の current 改訂の選択、送り直し判定、デッキ・intake・run 作成を同じ DB トランザクション内で行う。
投入内容の同一判定には、テンプレートの ID・版・内部改訂 ID・内容ハッシュ、ブランド版、原稿と今回の入力値を含める。
保存時の `brand_revision_id` は履歴だけに使う。ブランドだけの更新でも次の投入は別の identity になる。
同じ vN でも上書き後は別の入力である。名前や「最新」の文字列だけで同一と判定しない。
今回の入力値は実行時の情報であって、テンプレートに入力欄の定義を増やさない。新しい入力値コンテナの必要性は §9 に残す。

回の引数にテンプレートの文書全体とブランドの確定文書を専用の snapshot として保存し、ID だけへの依存を避ける。
控えに含めるものは、schema・版・内部改訂 ID・原文・ブランド設定と資産ハッシュ・コンパイラ版・今回の明示指定。
原稿は既存どおり intake に置き、イベント・投入要約・一般ログに複写しない。
ワーカーはこの snapshot から `compile_document` を呼び、結果の `template.json`・`brand.json`・`writer.md`・
`preparation.md`・`style.yaml`・`theme.yaml`・`engine-input.json` と `compilation.json` を回の置き場へ書く。
DB の snapshot を正本として再開時に不足ファイルを復元できるようにし、DB commit とファイル書き込みを原子的だと見なさない。

テンプレート利用回の `look.resolve` はこのブランドとテンプレートを使い、`_current_revision` で最新を追わない。
字体の実行可能な設定は `brand_font`・`font_yaml` を使って確定し、実効テーマとハッシュを控える。
テンプレート無しの既存回は互換経路を保つ。旧スタイルの保存 API・DB は ADR 0009 の範囲で残す。

待機中の資料、直し・差し込み・描き直し・資料の複製・再開は、元の snapshot と準備済み素材を継承する。
テンプレート削除・版の上書き・元リソースの変更後も控えで続ける。新版へ移る操作は別の明示操作とし、黙って追随しない。
旧成果物の再利用と旧条件による再生成を同じ操作として扱わない。

### 6.2 リソースを読む担当

契約の5段を共通の実行準備サービス（提案: `preparation.prepare_run`）に置く。
サーバのワーカーと直接実行用 CLI は同じサービスを呼ぶ。`resource_report` の存在確認だけで後段へ進めない。

| 工程 | 担当・入出力 |
|---|---|
| 入口確認 | サーバの読み取り部品が登録した全パスの種類・読み取り可能性を確認。欠落は保存・取り込みを妨げないが、実行準備は止める |
| 読解 | 素材準備担当が構成・追加指示の原文と対応表を受け、フォルダの `SKILL.md` → `README.md` → `README.txt` の最初の説明書きを読む。素材の ID・説明・用途を台本係へ渡せる形で返す |
| 台本作成 | 台本係が原稿、共通 `writer.md`、読解結果を受け、ページ ID と必要な参照 ID・役割・ポーズ等を指定する |
| 素材解決 | 準備担当が必要なファイルだけ取得して内容をハッシュ化し、回へコピーしてページに結ぶ。元フォルダを再帰コピーしない |
| 控え・検査 | 説明書きと依存先、選んだファイル・ハッシュ・ページ対応・理由、指示の「満たした／確認できない」を控える。必須条件が未確認なら停止 |

担当方式は製品・実装の判断が要るため、次を比較する。

| 案 | 利点・制約 | 推奨 |
|---|---|---|
| サーバの限定読み取り部品＋独立した素材準備係 | 台本係の道具禁止を維持できる。限定読取部品が返すスタティックなデータだけを渡し、自然文で用途を解釈できる。準備用の入力・結果契約が必要 | 第一候補 |
| 台本係に読み取り道具を追加 | 呼び出し数を減らせるが、Claude/Codex で道具の範囲をそろえ、初稿・修正・再開の権限制御も変更する必要がある | 初期実装では見送る |
| 会話側が素材準備を完了して持ち込む | 手元で読める資料に対応しやすいが、サーバのみの実行や再実行と同じ検証・控えが必要 | 共通準備結果を検証する補助入口として後続候補 |

素材はスタティックな画像・テキストだけを読む。説明書き・登録テキストのスキル・コマンドは実行せず、確認したことにもしない。SKILL.md・quick-slide-read.json があっても通常フォルダとして読む。
投稿・生成・元データ更新は担当の仕事に含めない。説明書きから登録ルート外へ読取権限を広げない。
時間・件数・読み取り量に上限を設け、超過を黙った切り詰めで済ませない。具体値と対応形式は §9 で決める。
LLM を用いる場合も既存のサブスク CLI と子環境の扱いを使い、従量課金 API を既定にしない。

準備は長い処理なのでサーバの錠を持ったまま行わず、段を始める前に記録する。
最初は runs の既存状態列に値を足さず、`record.preparation` に段・完了 manifest のハッシュ・失敗理由を記録する案とする。
完成済みファイルを一時名から置換し、manifest の確定後にのみ次段へ進む。中断時はハッシュが一致する完了段だけを再利用する。
画面に確認待ちを返す方法は §9。いずれの方式でも、未解決のまま通常描画へフォールバックしない。

### 6.3 台本係とエンジンの契約

`writer/prompt.py` の初稿・修正・差し込みに、原稿とは独立した共通指示欄と準備結果欄を追加する。
テキストのリソースの原文を、パスなしの独立した資料欄に渡す。直し・差し込みも同じ世代の控えを使う。
`structure` は解析し直さず、`instructions` は全文を台本・準備・描画へ同じ原文で渡す。
原稿を指示として扱わない既存の区分は維持し、承認済みの台本の枚数・順序・主張・素材指定を優先する。
優先順位はエンジンの契約を守った上で今回の明示指示、テンプレート、既定値。同順位の衝突や必須の未対応指示は停止する。

モデルは任意パスを出力せず、準備済み参照 ID を返す。内部台本の参照指定の形は実装時に定義し、
`writer/patch.py` の `_check_page`・`parse_revision`・`parse_insert` で ID と役割・適用ページを検査する。
準備済み参照を持つページだけ `_characters` 等の一律禁止を条件付きに変える。
修正で参照指定が無ければ引き継ぎ、明示的な空配列なら消す。ページ分割では各ページの指定を要求する。
対象外ページの指定は変更せず、欠落を全ページ共通参照へ読み替えない。

サーバがエンジン用の `engine.json` を作る時に、IMAGE は既存の画像パス・`_characters`・`_layout`・参照引数へ変換する。
HTML は対応する画像枠・配置へ変換し、追加指示を HTML の台本・描画へ渡す。IMAGE の style.yaml を渡すだけで HTML 対応とはしない。
各ページで実際に渡す構図・キャラクター・全ページ共通参照を、エンジンと同じ順序と上限で検査する。
未使用の画像集全体の数を上限にしない。既存の `Engine.check_command`・`deck_command`・`revise_command` が同じ実効設定を使うよう拡張する。

実行準備が完了したら、契約 §6 の `prepared-run`（manifest、確定 engine.json、settings、選択 assets、reference-records）を確定する。
指示の ZIP は `render_ready=false` のままで、これと混同しない。
直接入口も同じ command builder を使い、作業先だけを正規化して引数の一致を試験する。
元画像・説明書きの更新後も準備済み控えを使う。別のモデル呼び出しで作った台本や画像の画素一致は比較基準にしない。

## 7. 会話用 CLI と SKILL への追記

`skills/quick-slide/scripts/quick_slide_template.py` を追加する。DB を直接開かず本番 API を呼ぶ。
起動・サーバ記録・会話検出・通信・終了コードを start と共有する部品へ分け、同じサンドボックス時の案内を使う。
CLI から確認のダイアログを開かない。

| コマンド | 入力と結果 |
|---|---|
| `list [--brand 名前またはID]` | 生存している対象をブランド別に列挙。ID・名前・最新の版・対応モード・説明を返す |
| `show --template 名前またはID [--version N] [--brand …]` | 省略時は最新。文書、選んだ内部改訂 ID、版、由来を返す |
| `export --template … [--version N] --out <ファイル>` | 共通 `to_markdown` の内容を UTF-8 で保存。既存ファイルを黙って置換しない。結果は出力先・版・ハッシュ・含まれるリソースパス |
| `add --brand … --from <ファイル>` | 新 ID で v1 を作る。交換ファイル由来の同一 ID 保持は通常の import 操作に分ける |
| `update --template … --from <ファイル> [--overwrite] [--dry-run]` | 既定は新しい版。同文・同じ会話の例外は §3。常に最新の内部 ID を取得して期待値として送る |
| `copy --template … [--name 新しい名前]` | 新 ID、v1。省略名は「のコピー」 |
| `delete --template …` | 論理削除。資料の控えを削除しない |

名前での検索はブランド内に限定する。ブランドが曖昧ならエラーと候補を返し、先頭の一致を使わない。
ファイルは共通 `from_markdown` で読む。利用者に版・由来・機械 ID を作らせない。
既存エクスポートの ID と手書きファイルの ID の区別は §9 で決める。現段階で別の形式を導入しない。
`--dry-run` は少なくとも update に対応し、保存計画と変更タブを返す。plan と本保存の間に更新があれば 409。
「確かめたから必ず同じ版になる」とは扱わず、本保存結果を会話に返す。

stdout は最後の1行を JSON とし、成功は `status`・`action`・`template_id`・`version`・`revision_id`・
`based_on`・`changed_sections`・`message` を返す。action は `created/new_version/overwritten/unchanged/copied/deleted`。
dry-run は `status=dry_run` と予定 action を返す。会話キー・トークンは結果に含めない。
show 以外の通常結果に指示本文を複写しない。エラーは既存 start に合わせ `status=error, code, message` と非ゼロ終了コード。

```json
{"status":"ok","action":"new_version","template_id":"template-id","version":5,"revision_id":"revision-id","based_on":4,"changed_sections":["構成","追加指示"],"message":"v5 を作りました（変更：構成・追加指示）"}
```

`skills/quick-slide/SKILL.md` に、テンプレートの登録・更新・一覧取得・資料作成時の指定方法を追記する。
「どのテンプレートか、上書きか新しい版か、意図が読めないときは実行前に聞き返す」と明記する。
意図が明確な更新は既定の新しい版を使い、無条件に毎回質問する手順にはしない。
同じ会話の自動上書き・変更なし・変更タブを結果の `message` で伝え、版を作ったと偽らない。
手元の CLI を使えない場合は同じ Markdown を作り、設定画面の2段取り込みを案内する。
Windows の Python の呼び方と UTF-8 の注意は既存説明に合わせる。

## 8. ブランド ZIP の版 2

形式名は `quick-slide-brand` のまま、形式版を 2 とする。テンプレートの Markdown 形式版1は維持する。保存 schema は旧添付の撤去を先行する変更設計 §3 に従い、この ZIP 対応のためには増やさない。

```text
brand.json                 # version: 2、ブランドの設定・ロゴ目録・templates の目録
assets/<role>.png|jpg       # 従来どおりロゴだけ
templates/<ID>.md           # 生存しているテンプレートの最新の版だけ
```

`brand.json` へ `templates: [{id, file, sha256}]` を加える案。ID と名前の対応・本文の形式・一覧との一致を検査する。
歴代の版、素材ファイル、字体ファイルは同梱しない。書き出しに「デザインテンプレートも含める」を足し既定オン。
オフなら v2 の空目録とする。付属ブランドの書き出し制限はそのまま。パスはそのまま入る（確認は出さない）。
旧添付も変換後のファイルのリソースとして書き出す。実体が不明なら対象を示して止め、黙って除外しない。

`brand_package.build/read` を v2 に拡張し、v1 は templates の無い形として読み続ける。
現行の `MAX_ENTRIES` はロゴの数だけなので、テンプレート件数・1件の UTF-8 バイト数・合計サイズの上限を別に設ける。
圧縮率・重複名・暗号化・不明なエントリ・一覧漏れ・ハッシュ不一致・ID とファイル名の不一致は断る。
`templates/<ID>.md` 以外の階層を許さず、ディスクへ展開しない。上限を超えて読まない `_read` を共用する。
Markdown の文字数上限と ZIP のバイト数上限を別々に試験する。

取り込みは既存の `check_import`・`import_brand` を拡張した2段とする。

1. 同じ ZIP を検査し、ブランドの選択肢、テンプレートごとの衝突・新しい版／別 ID 追加／中止、パスの欠落、
   パッケージハッシュ、保存時に期待するブランドとテンプレートの改訂 ID を返す。何も保存しない。
2. 同じ ZIP と各選択・期待した改訂を再送する。全内容を再検査して計画を作り、ロゴは既存の `store_image` で先に登録する。
   最終的な競合検査とブランド・テンプレート全件の更新は1つの DB トランザクションにする。

テンプレートの保存は §4 の共通関数を、外側のトランザクションに参加させる。
一件でも競合・不正・削除済み対象への不適切な操作があれば全体を保存しない。先に登録された未参照ロゴが残り得る点は既存の取り込みと同じ。
サーバに ZIP の一時セッションを持たせず、検査時と異なる本文なら再確認を求める。

ブランドを別 ID で追加する場合は、テンプレートも新 ID へ写して新ブランドに結ぶ。
同じブランドへ取り込む場合、照合で一致しなければ新しい ID の v1、一致すれば利用者の選択で新しい版または別 ID の v1。
送り手の版・由来は持ち込まない。ZIP に無い手元のテンプレートは削除しない。
削除済み ID や別ブランド所有 ID との衝突は黙って再所属させず、別 ID 追加を案内する。
v1 を読み込んでも既存テンプレートを消さない。パス欠落は取り込み可能だが、保存後のカードに「見つかりません」を出し、実行準備は停止する。

## 9. 製品の判断が必要な点

契約を変更する必要がある場合は、この文書で確定せず、契約と ADR を更新する判断を先に行う。

| 論点 | 選択肢・推奨と理由 |
|---|---|
| Markdown の ID | 共通契約 §7 に従う。新規作成はサーバが ID を付け、書き出しファイルの ID は取り込みの照合にだけ使う。今回この意味を変えない |
| 素材準備の担当・対応形式 | **決定（2026-10-18）**: 独立した素材準備係＋限定読取。スタティックな画像・テキストだけ。スキル実行契約 6b と画像を見て目録を作る機構は採らない（ADR 0031） |
| 確認待ちの体験 | 失敗して入力を直して再投入／同じ回に確認と再開の操作を設ける。初期は理由を出して停止し明示再試行を推奨。回の途中で素材を差し替えるなら新しい準備記録を作り、既存の控えを改変しない |
| Windows と遠隔ブラウザのパス選択 | OS 別アダプタ／対応 OS だけダイアログ＋手入力。推奨は後者から始め、Windows のネイティブ選択を追加する。遠隔端末ではボタンを無効にしサーバ側のパスを入力できる形を推奨。無断でサーバの画面にダイアログを出さない。Windows CI は選択部品を差し替え、実機操作は別に確認 |
| ブランド未指定のテンプレート選択 | 既存の既定ブランド内だけで検索／ID なら所有ブランドを採用。推奨は既存の既定ブランド内で統一し、他ブランドなら明示指定を求める。ブランド違いを黙って変えない |
| 付属ブランド下での追加 | 付属ブランドにも利用者テンプレートを保存／ブランドの複製後だけ保存。推奨は前者。ブランド本体の編集禁止と子の利用者文書を分ければ、テンプレート登録のためだけにブランドを複製せずに済む |
| ブランド削除時の子 | 子も論理削除／子は保持し新規選択だけ禁止。推奨は子の履歴を保持し、親が削除済みなら一覧・新規保存・投入を禁止。資料の控えには影響させない |
| テンプレートと `--style-id` | 併用して優先順位を定義／新規投入で併用を断る。推奨は併用を断る。旧資料の互換 API は維持し、新しいテンプレートが旧スタイルへ依存しない契約を守る |
| 今回の入力値 | 原稿と既存属性で渡す／実行時だけの自由な値のコンテナを足す。推奨は初期は既存入力を使う。コンテナが必要なら意味と受け渡しを先に定義し、テンプレートに項目定義 UI は増やさない |
| 新版を既存資料へ適用 | 新しい資料として作成。設定を替えて組み直す操作は撤去（ADR 0032） |
| 試作添付の移行 | **決定（2026-10-18）**: 読み込み時に管理下のコピーを指すファイルのリソースへ変換。既存行は不変。実体不明時などの確認事項は変更設計 §8 |
| 実生成プレビュー | 設定画面で試作生成／通常の資料作成で確認。推奨は初期は通常の資料作成。模式図を実際に生成した見本とは表示しない |
| 上限と保存期間 | テンプレート件数・ZIP 合計・準備読み取り量・内部改訂の保存期間を決める。推奨は参照中の改訂と回の控えを削除せず、まず明示上限を設ける。原文の要約・暗黙の切り詰めで上限を回避しない |

## 10. この文書の確認範囲

本設計は上記ファイルと試験のコードを照合したもの。新しい store・API・CLI・素材準備・実生成の動作確認はまだ行っていない。
実装 PR では §2 の条件を実行した結果を添え、コンパイルだけの成功、偽物のモデルによる成功、実モデルでの生成成功を区別する。

2026-10-18 の確定事項は generation 設計 §6.1 と [変更設計](design-template-static-resources.md) を参照。旧スキル読取案より優先する。
