# デザインテンプレート: 資料づくりへの接続（PR 5〜7）

状態: 詳細実装設計。PR 5〜7 のコード・試験・実生成は未実施。
#22 の保存・API・設定画面・会話 CLI（PR 1〜4）から、投入・素材準備・台本・描画をつなぐ。
[共通契約](design-template-contract.md)を正本とし、[実装設計](design-template-implementation.md) §2・§6・§9 を具体化する。
以下の新しいモジュール・関数・データ項目・コマンドは提案であり、現行の機能と区別する。
独立した素材準備係を置き、通常のファイル・フォルダと、宣言された共通契約で読み取るスキルを最初から扱うことは決定済み。
保存 schema 3・画面の4項目・エンジンの台本形式を変えず、実行に必要な許可と控えはテンプレートの外に持つ。

## 1. 接続点と PR の境界

### 1.1 読んで確認した現行の動作

| ファイル・関数 | 現行の動作と接続上の注意 |
|---|---|
| `src/quick_slide/design_templates.py`: `validate`・`compile_document`・`resource_report` | 保存文書を検証し、ブランドとモードを合わせて共通指示・設定を返す。`instructions_compiled`・`render_ready=false`・`generation_executed=false`。存在確認は素材の読解・選択ではない。`compilation.json` のファイル化は呼ぶ側で行う  根拠: `src/quick_slide/design_templates.py`: validate:L112–165、resource_report:L277–300、compile_document:L320–372 |
| `src/quick_slide/store/design_templates.py`: `get_design_template`・`_view` | 省略時は current、版番号ではその版の最大 save_sequence、内部改訂 ID なら特定の保存を返す。削除後も履歴を読める。返却値に文書と保存時の `brand_revision_id` がある  根拠: `src/quick_slide/store/design_templates.py`: get_design_template:L234–251、_view:L370–383 |
| `src/quick_slide/server/template_routes.py`: `get_template`・`_revision_number`、`server/template_service.py`: `create`・`save`・`plan` | 版番号の取得と保存方針を持つ。取得できることと、新規生成で選択可能なことは分ける。設定の GET に CLI 実行を足さない  根拠: `src/quick_slide/server/template_routes.py`: _revision_number:L48–54、get_template:L84–85 / `src/quick_slide/server/template_service.py`: create:L116–125、save:L128–177、plan:L217–239 |
| `skills/quick-slide/scripts/quick_slide_template.py`: `resolve_brand`・`resolve_template` | 既定ブランド内で名前と ID を解決し、別ブランドの ID・同名の複数一致を断る。取得時に版を指定できる  根拠: `skills/quick-slide/scripts/quick_slide_template.py`: resolve_brand:L89–107、resolve_template:L115–134 |
| `skills/quick-slide/scripts/quick_slide_start.py`: `build_parser`・`build_payload`・`run`・`payload_summary` | ブランド ID・旧 style ID 等を投入する。テンプレート指定はまだない。投入要約に原文を足さない  根拠: `skills/quick-slide/scripts/quick_slide_start.py`: build_payload:L94–150、payload_summary:L153–164、run:L405–463、build_parser:L466–519 |
| `src/quick_slide/store/intakes.py`: `create_intake`・`_validate_intake`・`run_args_from_attributes` | 原稿・題・属性等のハッシュと会話 HMAC で送り直しを判定し、デッキ・回・仕事を同一トランザクションで作る。現状は設定解決前に request_sha を作るため、テンプレート利用時には順序の変更が必要  根拠: `src/quick_slide/store/intakes.py`: create_intake:L77–133、run_args_from_attributes:L208–219、_validate_intake:L222–278 |
| `src/quick_slide/store/runs.py`: `create_run`・`update_run_args`・`set_run_record` | 引数と記録を保存する。記録はトップレベル単位の併合であり、準備の子項目だけ送ると以前の子項目が消える  根拠: `src/quick_slide/store/runs.py`: create_run:L57–70、update_run_args:L373–379、set_run_record:L381–386 |
| `src/quick_slide/store/edits.py`: `deck_run_args`・`create_edit` | 直しは新しい回を作り、最新回の引数のうち `DECK_ARG_KEYS` だけを継承する。snapshot は現在この対象にない  根拠: `src/quick_slide/store/edits.py`: deck_run_args:L38–45、create_edit:L47–70 |
| `src/quick_slide/render/look.py`: `resolve`・`_current_revision`・`brand_font`・`font_yaml`、`worker/pipeline.py`: `settle_look` | 通常は仕事の開始時に生存するブランド・旧スタイルの最新へ追随する。再開では settle_look を呼ばない。字体はエンジン設定から実行可能なファイルを探す  根拠: `src/quick_slide/render/look.py`: resolve:L52–159、brand_font:L189–228、font_yaml:L231–240、_current_revision:L243–250 / `src/quick_slide/worker/pipeline.py`: settle_look:L117–130 |
| `src/quick_slide/writer/backends.py`: `choose_backend`・`ask`・`ask_claude`・`_codex_command` | サブスク CLI を選ぶ。Claude は対応する道具無効化フラグを使う。Codex は ChatGPT 認証・read-only・設定の無視等を指定する。read-only は「道具なし」と同義ではない  根拠: `src/quick_slide/writer/backends.py`: choose_backend:L102–121、ask_claude:L190–219、_codex_command:L222–233、ask:L286–290 |
| `src/quick_slide/writer/prompt.py`: `_rules`・`_edit_rules`・`build_prompt`・`build_revision_prompt`・`build_insert_prompt` | 道具・ファイル読み書きを禁じ、`_characters`・`_layout` を書かせない。共通指示と準備結果の入力が必要  根拠: `src/quick_slide/writer/prompt.py`: _rules:L120–160、build_prompt:L208–260、_edit_rules:L300–325、build_revision_prompt:L371–459、build_insert_prompt:L711–768 |
| `src/quick_slide/writer/patch.py`: `_check_page`・`image_paths`・`parse_revision`・`apply_revision`・`parse_insert` | 禁止キーと画像パスを検査し、対象ページだけを差し替える。参照 ID の範囲検査と省略時の継承は新設する  根拠: `src/quick_slide/writer/patch.py`: image_paths:L58–76、_check_page:L79–96、parse_revision:L99–190、apply_revision:L193–209、parse_insert:L212–232 |
| `src/quick_slide/worker/pipeline.py`: `write_script`・`check_candidate`・`engine_script_view`・`engine_script_with_pictures`・`render_and_import`、`worker/edit.py`: `_writer_loop`・`_revise`・`_insert` | 台本作成、検査、挿絵の PNG 化、描画と直しの接続点。新規作成だけに準備を差し込むと、直し・差し込みで指示と参照を失う  根拠: `src/quick_slide/worker/pipeline.py`: engine_script_view:L133–138、engine_script_with_pictures:L141–155、render_and_import:L307–429、check_candidate:L553–577、write_script:L580–704 / `src/quick_slide/worker/edit.py`: _writer_loop:L378–469、_revise:L472–548、_insert:L940–967 |
| `src/quick_slide/engines.py`: `Engine.style_args`・`brand_args`・`check_command`・`deck_command`・`revise_command` | モード別の引数を組み立てる正本。現状の style は組み込み名を前提とする経路があり、テンプレートのファイル指定を明示的に扱う必要がある  根拠: `src/quick_slide/engines.py`: style_args:L77–89、brand_args:L91–105、check_command:L107–115、deck_command:L120–128、revise_command:L136–155 |

### 1.2 分け方・完了条件

PR は 5 → 6a → 6b → 7（IMAGE → HTML）の順に進める。途中の状態でも未解決のテンプレート利用回を旧経路で描かない。

| PR | 実装する範囲 | 完了条件と動かし方 |
|---|---|---|
| 5 投入と控え | CLI、投入の選択解決・同一判定、DB snapshot、look の固定経路、直しへの継承 | 一時データのサーバへ CLI と HTTP で投入し、待機中の版上書き・ブランド更新・論理削除後も同じ控えを使う。修正・差し込み・再開の回で控えを照合する。準備未接続なら理由付き停止を確認。「生成可能」とは表示しない |
| 6a 通常素材と台本 | 通常ファイル・フォルダの限定読取、catalog 抽象、`_references`、検査・コピー、台本契約、最小 engine view 変換器 | catalog は偽の提供者でも試験する。スキルを含む登録は未対応として停止。素材 CLI・LLM・エンジンを含め子プロセスは一切起動せず、LLM とエンジン検査は注入した偽物で接続を試す。6a 単体では出荷しない |
| 6b 契約と許可 | `image-catalog/1` の汎用クライアント、指紋の記録、子プロセス制御、スキルフォルダ単位の許可と最小画面 | 架空の準拠 CLI と偽 LLM を使い入口から確定台本まで試験。6a の変換器で準備用 engine view を作り、ワーカーが `check_candidate` を実エンジンで実行してから台本を確定。未許可では起動しない。描画は PR 7 まで停止 |
| 7a IMAGE 描画 | IMAGE の変換・prepared-run・共通 builder・完成画面 | 公開可能な例を実生成し、直し・差し込み・再開を確認。builder の一致は同じ関数の呼出で試験 |
| 7b HTML 描画 | HTML の変換と上限検査・実生成 | IMAGE に続く第2スライス。直接 CLI の prepare/render はこの後半または別 PR に延期可能 |


どの PR も対象の単体試験後に `bash tests/run.sh` を実行し、Windows CI の結果を添える。
PR 7 は `bash skills/image-slide/tests/run.sh`・`bash skills/html-slide/tests/run.sh`・`bash skills/illust/tests/run.sh` も実行する。
配布前は `QUICK_SLIDE_FRESH_E2E=1 bash tests/run.sh`。ブラウザ試験の skip、偽モデルでの成功、実モデルでの成功は区別する。
実生成は既存のサブスク CLI を使い、従量課金 API への切替を既定にしない。

## 2. PR 5: 投入と控え

### 2.1 選択と検証の順序

start に `--design-template <名前またはID>` と `--design-template-version <正の整数>` を追加する。
版だけの指定と `--style-id` との併用を CLI とサーバの両方で断る。版を省けば受付時の最新を使う。
投入属性は提案として `design_template`・`design_template_version` を追加し、クライアント由来の snapshot は受けない。
`--brand-id` を維持し、テンプレート CLI と同じ既定ブランドの意味にそろえる。名前検索を全ブランドへ広げない。

1. `_validate_intake` で型・未知キー・モード・併用禁止を検査する。テンプレートは IMAGE（pitch）/ HTML（presentation）だけで、Motion へ流さない。
2. サーバの共通選択関数（提案: `template_selection.resolve_for_intake`）がブランドを確定する。
   未指定なら設定の既定ブランドを使う。既定が不正なら停止し、別ブランドへ黙って移らない。
3. そのブランドの生存テンプレート内で、ID の一致を先に、次に名前の完全一致を探す。同名複数は候補 ID を返して停止。
   ID が別ブランドなら明示したブランド指定を求める。削除済みの親・子、存在しない版、非対応モードを断る。
4. `get_design_template` で版を取得し、その保存に付く `brand_revision_id` のブランド文書を取得する。
   投入ブランドの所有 ID と一致することを再確認する。最新ブランドで過去テンプレート版を組み直さない。
5. 確定した内部改訂・内容とブランド版を同一判定に使い、デッキ・intake・run・job と snapshot を同じ DB トランザクションで保存する。

名前解決の意味を CLI ごとに作らない。既存 `resolve_brand`・`resolve_template` の振る舞いを共通試験にし、サーバで最終検証する。
CLI が候補を表示するため事前取得しても、受付で再解決した改訂を結果に返す。
事前表示した版と同じ保存を要求する場合だけ、期待した内部改訂 ID を送って不一致を 409 にする。競合を黙って再送しない。

### 2.2 同一判定と snapshot

テンプレート利用時は `create_intake` の request_sha 作成を選択確定後、同じトランザクション内へ移す。
既存の原稿／依頼・題・属性に、次の正規化された選択を含める。入力名や「最新」という文字だけで判定しない。

```json
{"template_id":"template-id","version":2,"revision_id":"revision-id",
 "template_sha256":"…","brand_id":"brand-id","brand_revision_id":"brand-revision-id",
 "brand_sha256":"…","explicit_overrides":{"style_note":"…"}}
```

同じ会話・同じ確定内容なら既存の送り直し期間内で同じデッキを返す。
同じ v2 の上書き後は内部改訂が変わるので別投入になる。最新省略と版指定が同じ内部改訂に解決された場合は同一にできる。
会話キーの HMAC 化、force_new_deck、原稿・依頼を一般ログや payload 要約に複写しない契約を保つ。
今回の値は初期には原稿・依頼・既存属性だけを使い、テンプレートに項目定義や新しい値コンテナを増やさない。

提案: 不変の専用 DB テーブル `template_snapshots` に次の実体を1度だけ保存する。`runs.args.template_snapshot` は `{snapshot_id, snapshot_sha256}` の参照とし、回ごとに全文を複製しない。「実体を持つ」とは、元テンプレート・ブランドの履歴がなくても専用テーブルから復元できる意味であり、外部の可変文書への ID 参照ではない。デッキと各回は同じ snapshot を参照する。

| 項目 | 内容 |
|---|---|
| `schema_version`・`compiler_version` | 控えとコンパイラの版 |
| `template` | ID・版番号・内部改訂 ID・文書全文・内容ハッシュ |
| `brand` | 所有 ID・内部改訂 ID・確定 config・資産目録とハッシュ |
| `mode`・`explicit_overrides` | 実行モードと、今回明示した指定だけ。CLI の既定値と区別 |
| `snapshot_sha256` | 上記の正規化 JSON のハッシュ |

ブランド画像・添付・字体は DB 文書だけで復元できない。受付から利用中は参照資産を削除対象にせず、準備時に実体を検証して回へコピーする。
確定後に元が欠けたら停止する。別のロゴや字体を最新設定から引いて補わない。
文書ファイルは DB commit 後にワーカーが `compile_document` から作り、compilation のメタデータも書く。
DB snapshot を正本とし、commit 後・ファイル化前の中断は復元可能にする。DB とファイル保存を一つの原子的操作とは扱わない。
コンパイラ更新後も完了済みのコンパイル控えを再利用する。未完了で指定版コンパイラが使えなければ停止し、最新版で黙って再解釈しない。

### 2.3 look と継承

`look.resolve` の先頭で template_snapshot の有無を分岐し、提案の `resolve_template_snapshot` を呼ぶ。
テンプレート利用回では `_current_revision`・`follow_deck_settings`・旧 style の解決へ入らない。
デッキ行は `decks.brand_revision_id` に snapshot のブランド改訂 ID、`style_revision_id` に付属の既定スタイル改訂 ID を保存する。後者は既存 DB の互換値でありテンプレートの実効 style として解決しない。`follow_deck_settings` を通さず、この組を受付時のトランザクションで固定する。
共通コンパイル結果から実効 style ファイル、theme、ロゴ、フッター、ページ番号、字体を一度確定し、`args.look` と `record.look` に残す。
`brand_font`・`font_yaml` の結果と実効 theme のハッシュを控え、実際に渡す字体ファイルも保持する。
既存の字体フォールバックの理由を記録し、特定字体が必須の追加指示と矛盾する場合は停止する。

`--style`・image_look・style_note 等は「今回の明示指定」のみを優先する。CLI の既定 style でテンプレートを上書きしない。
優先順位はエンジン契約 → 今回の明示指示 → テンプレート → 既定値。同順位で衝突したら停止する。
既存資料の one_page の意味を途中で変えない。テンプレート無しは現行の最新追随経路を維持する。

`store/edits.py` の `DECK_ARG_KEYS` と `deck_run_args` に snapshot と準備済み控えの参照を追加する。
ただし単なる最新回依存にせず、仕事を実行する時点の確定台本に対応する準備記録を選ぶ。
初版のテンプレート利用資料ではページ直し・差し込みを資料ごとに直列化する。準備世代を変える操作も同じ排他枠で行い、基点台本と準備記録のハッシュが変わっていれば競合として断る。
`worker/edit.py` の直し・差し込み、pipeline の再開・描き直しも同じ固定経路を通す。

- 既存ページは選択済み資産のコピーを使い、説明書き・CLI を再実行しない。
- 新規素材が必要な差し込みは、同じ snapshot に対して新しい準備世代を作る。許可・入力の変更を再検査する。
- 未完了段の再開で説明書き・コード・依存目録が変わっていたら停止する。完了済み台本と素材を勝手に取り直さない。
- テンプレート削除・同じ版の上書きでも既存回の控えは有効。新しい版への移行は初期には新規資料として明示する。
- rebuild で最新設定へ戻す既存操作は、テンプレート利用回では無断適用しない。未対応の変更は理由付きで断る。
- 初版では rebuild とページ版の復元をともに理由付きで断る。参照記録を含む復元は後続 PR とする。

### 2.4 PR 5 の試験

一時 DB と偽ワーカーで、名前／ID・既定ブランド・同名複数・別ブランド・削除親子・無い版・版のみ・style-id 併用・非対応モードを試す。
同文送り直し、版上書き後の別投入、明示版と最新省略の同一性、force_new_deck、競合時の全体ロールバックを確認する。
受付後にテンプレート・ブランドを変更し、look・直し・差し込み・再開が元文書を使うことをハッシュで比較する。
デッキ行の2改訂 ID が受付後も変わらず、`follow_deck_settings` を呼ばないことを検査する。
commit 後のファイル化失敗から復元し、旧スタイルだけの回も従来どおり動くことを確かめる。

## 3. PR 6: 素材準備係と台本

### 3.1 工程・担当・入出力

提案: `src/quick_slide/preparation/` に共通サービス `prepare_run` を置き、ワーカーと直接入口から呼ぶ。
素材準備係は独立した LLM 呼び出しであり、台本係に素材探索を兼ねさせない。
LLM は `writer/backends.py` の choose_backend / ask を共用し、道具・Web・スキルの自動読込を与えない。
Codex の現行 read-only 指定だけでは不十分なので、準備・台本用途では`_codex_command(no_tools=True)` を追加し、Web 無しでも `backends.py:66` の `CODEX_WEB_DISABLED_FEATURES` と同じ集合を false にする。Web 検索も disabled とし、必要なフラグを適用できない CLI は未対応として停止する。
CLI の認証環境は既存 `childenv.py:child_env` を使うが、後述する素材用プロセスには別の最小環境を使う。

| 工程 | サーバのコード | LLM | 出力 |
|---|---|---|---|
| 入口確認 | 全登録パスの種類・読取可否、旧添付、サイズを検査。宣言ファイルを先に検証し、その後 SKILL.md → README.md → README.txt の最初だけを読む | 呼ばない | 入力目録、説明書きの内容・ハッシュ、未対応理由 |
| 読解 | 通常ファイルの限定読取、フォルダ目録、契約提供者の一覧を取得。追加読取要求は許可範囲内でのみ実行 | 構成・追加指示・preparation.md・読取結果を解釈。用途・語彙・候補 ID を返す。コマンドを実行しない | `resource-catalog.json`、読解記録、確認事項 |
| 台本作成 | writer.md と読解結果を専用欄に渡す。承認済み台本なら原本を検証して保持する | 台本係がページ・参照 ID・役割・理由を指定する | 内部台本、ページ別素材要求 |
| 素材解決 | 要求 ID を一覧に照合し、必要ファイルだけ取得・検証・ハッシュ化してコピー | 必要時のみ独立準備係が一覧内の候補選択を補助。任意パスは返さない | 選択資産、参照束縛表 |
| 控え・検査 | ID、ページ範囲、形式、ハッシュ、上限、必須条件を検査 | 意味上の指示について根拠と「満たした／確認できない」を返す | 準備 manifest、遵守状況。描画前の見た目は未確認 |

通常読取の初期範囲は UTF-8 の Markdown・テキスト、安全な JSON / YAML、PNG / JPEG とする提案。
フォルダはまず名前・形式・大きさの目録を作り、説明書きが指す必要ファイルだけを読む。
PDF・文書形式等は専用の限定抽出器が無ければ未対応として止め、LLM にパスだけを渡して読んだ扱いにしない。
画像の内容判断が必要で、呼出 CLI が画像入力を持たない場合は説明・語彙で選べる範囲を明示し、目視での適合を確認済みにしない。

登録パスが一つでも欠落していれば停止する。「使い方に任意と書いた」ことから欠落した登録自体を無視しない。
原稿の素材明示は優先し、候補が無ければ既定の絵・別の人物・新規生成へ落とさない。
説明書き・一覧は信頼しない資料データとして区切り、権限を増やす指示として扱わない。
構成と追加指示の原文は別に全員へ渡し、LLM の要約や resource-catalog に設定上の効力を持たせない。

### 3.2 画像集読み取り契約 `image-catalog/1`

これは新設する規範契約である。Quick Slide は汎用クライアント1つと対応契約版の一覧だけを持ち、スキル専用の adapter registry・argv 対応表・語彙を持たない。契約適合は読み取り性の証明ではない。利用者がスキルのフォルダを信用して実行を許可することを別途要する（§6.1）。

#### 宣言・発見・指紋

初版は登録フォルダ直下の UTF-8 `quick-slide-read.json` のみを宣言に使う。SKILL.md の前付け方式は将来に延期し、二重定義を作らない。テンプレート resources の既存 kind は増やさない。宣言があれば契約のスキル、なければ通常フォルダとして扱う。不正・未知版の宣言は通常フォルダへフォールバックせず停止する。6a は宣言または SKILL.md のある登録を未対応で停止する。

宣言を先に検証し、entry・依存・ルートの形式とパスの包含を確認した後で説明書きを読む。説明書きから実行入口を推測しない。`--help` も実行しない。

```json
{
  "contract": "image-catalog/1",
  "entry": "reader.py",
  "network": "none",
  "dependencies": ["lib/catalog.py"],
  "roots": [{"id": "images", "path": "images", "access": "read"}]
}
```

全項目必須、未知キー禁止。宣言にも以下の文字列の制御文字禁止を適用する。entry・依存名・root の path は各1〜1024文字、dependencies は最大256件、roots は最大32件、宣言全体は256 KiB 以下。root id は ID 型。entry は `.py` の通常ファイル、dependencies は entry 以外のコード・設定を列挙した重複なし配列（空可）。いずれも宣言フォルダ相対の POSIX パス。roots は1件以上、id は後述の ID 型で一意、access は read のみ。path は登録フォルダ相対またはローカルで明示登録した絶対ルートであり、外部ルートは画面の登録・確認時に表示する。設定や画像を相対読取する実装はそのルートも宣言する。動的 import・下位コマンド・未宣言依存を必要とする実装、`.cmd` 配布は初版未対応。network は none のみで、スキル側はネットワーク処理を行わない契約とする。宣言の検証は実装の挙動の証明ではなく、OS 隔離済みという表示はしない。

実装指紋は SHA-256。宣言の元バイト列、entry と dependencies の各内容ハッシュを作り、相対名で昇順にした `[{path,sha256}]` と宣言ハッシュを RFC 8785 の正規化 JSON にしてハッシュ化する。symlink は依存に認めない。runtime 指紋はサーバの Python 実行ファイルの内容ハッシュ・Python 版・OS・CPU を同じ方法でハッシュ化する。説明書きとルートの解決結果・設定内容ハッシュは準備の入力 digest に別途含める。画像の内容は選択時にハッシュ化する。

指紋は資料を作ったときの追跡用であり、実行許可の条件にしない。準備時に読み取った宣言・entry・依存の各ハッシュと実装・runtime 指紋を `reference-records` / manifest に記録し、取得結果のハッシュと対応付ける。検分済み指紋の台帳は設けず、コード・依存の更新による停止・再確認も行わない。

許可した登録フォルダから直接実行する。許可時にコードを不変領域へコピーする仕組みは設けない。ルート ID の実体対応はサーバの専用設定に保持する。記録用の読み取りと実行の間にもフォルダは変わり得るため、記録した指紋が実行中の全バイトを保証するとは扱わない。各操作の前後に読み取った指紋と取得結果のハッシュを控え、競合を後から辿れるようにする。指紋の差だけでは停止しないが、契約違反・範囲外パス・取得画像の不整合は従来どおり拒否する。保存場所は `paths.py:DataRoot` の accessor を追加して決め、エンジン側は places.py を使う。

#### 固定操作と JSON

起動はサーバの `sys.executable` の絶対パスによる `[python, "-I", "-B", entry, "qs-read", "list"]` または末尾 `["qs-read", "get", "--item", item_id]` のみ。list に絞り込み引数はなく全件を返す。絞り込みは Quick Slide 内で行う。自由文・LLM のコマンドを argv にしない。

以下の表を規範とし、6b で同内容の JSON Schema（draft 2020-12、宣言・list・get・error）を同梱して双方の fixture を検証する。現在この文書だけを変更し、schema ファイルはまだ追加しない。

共通規則: stdout は BOM なしの厳密な UTF-8 JSON オブジェクト1つだけ（末尾空白可）。重複キー、NaN、JSON 以外のログ、未知キーを拒否する。全オブジェクトは additionalProperties=false。全応答の contract は文字列定数 `image-catalog/1`、未知版・省略は失敗。文字数は Unicode code point 数。全文字列で C0・DEL・C1 制御文字を拒否し、説明文にも改行・タブを許さない。長すぎる値は切り詰めず失敗とする。説明文は信頼しないデータ欄に JSON として渡し、命令として解釈しない。構造検査だけでプロンプトインジェクションを防げるとは扱わない。

| 型・応答 | 必須フィールド | 任意フィールド・制約 |
|---|---|---|
| ID 型 | string、`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` | 大文字小文字を区別。先頭 `-` 不可 |
| list 成功 | contract、items（配列、空可）、problems（配列、空可） | 他キーなし。item_id は一覧内で一意、同じ入力内容では list/get 間で安定 |
| items の要素 | item_id（ID）、description（string、1〜1024文字） | subject_id（ID）、use・pose（各 string、1〜128文字）。use/pose は自由文であり enum・検索引数ではない |
| problem | code（ID）、message（string、1〜512文字） | item_id（ID）。problems が1件でもあれば初版は準備失敗。部分成功にしない |
| get 成功 | contract、item_id（要求 ID と一致）、files（1件以上の配列）、problems（配列） | 一覧にない item_id は取得しない |
| files の要素 | file_id（ID、組内で一意）、root_id（宣言済み ID）、path（string、1〜1024文字）、media_type（`image/png` または `image/jpeg`） | role（string、1〜128文字、自由文）。files の配列順を保持し、組の全ファイルを1 item として扱う。欠落時は組全体を拒否 |
| error 応答 | contract、error（code: ID、message: string 1〜512文字） | error 内に retryable（boolean、省略時 false）。成功形との混在不可 |

成功は終了コード0、契約エラーは非0かつ error 応答。非0・壊れた応答・stderr だけの失敗はすべて失敗として扱い、stdout/stderr を代わりのカタログにしない。stderr は診断専用で LLM に渡さない。get の path は root_id のルート相対 POSIX パスだけで、空成分・`.`・`..`・逆スラッシュ・コロン・絶対名を禁止する。1行パスだけを返す方式は受けない。

### 3.3 登録＝実行の許可・実行・安全な取得

**登録したことを、実行してよいという許可とみなす**（2026-10-17 利用者決定）。テンプレートのリソースに登録されたフォルダに `quick-slide-read.json` があれば、そのテンプレートで資料を作るときに契約の `list`・`get` を実行してよい。設定画面・会話 CLI・ファイル取り込みのどの経路で登録されたかを区別しない。別の確認ボタン・許可データ・資料ごとの許可は設けない。

理由: 利用者は自分の機体で、自分が登録したスキルを使う。取り込んだテンプレートのパスは取り込んだ機体の中を指すので、他人のテンプレートだけでは、その機体に無いプログラムは動かない。会話側の AI が危ないコードを動かすためにわざわざ Quick Slide に登録する経路は現実的でない（それなら直接実行できる）。

その代わり、設定画面のリソースのタブに、小さく薄いグレーで次の注意を出す: 「ここに登録したファイルやフォルダは、資料を作るときに AI が中を読み、スキルならそのプログラムも動かします。中身を確かめた、安心できるものだけを登録してください。」

**スキルの中身が変わっても止めない。** スキルは Quick Slide の外で頻繁に更新されるので、コード・依存の指紋の変化を理由に再確認を求めない。実行のたびに宣言・entry・依存の内容ハッシュ（§3.2 の実装指紋）を計算して控え（reference-records と manifest）に記録だけし、あとで「どの時点の中身で作ったか」を辿れるようにする。読み取り中にフォルダが変わる競合は、記録した指紋と取得結果のハッシュで後から分かる扱いとし、止めない。

実行は登録フォルダから直接行う（不変領域へのコピーはしない）。実行直前には、そのフォルダがまだ登録されていること・宣言ルートの包含を確認する。リソースから外されたフォルダには新しい子を起動しない。完了済み控えの再利用は再実行ではない。

素材プロセスには `childenv.child_env` を使わない。同関数（`src/quick_slide/childenv.py:34–48`）は環境全体を基に特定変数を除き PATH を広げるため、最小環境にならない。新設実行器は空辞書から `PATH`（サーバが使う runtime のディレクトリのみ）、`LANG=C.UTF-8`、`LC_ALL=C.UTF-8`、専用 `HOME`・`USERPROFILE`・`APPDATA`・`LOCALAPPDATA`・`XDG_CONFIG_HOME`・`XDG_CACHE_HOME`・`XDG_DATA_HOME`・`XDG_STATE_HOME`・`XDG_RUNTIME_DIR`、専用 `TEMP`・`TMP`・`TMPDIR` だけを設定する。Windows では検証した OS の `SYSTEMROOT` も必須。ほかの XDG_*、認証、proxy、PYTHONPATH は継承しない。Python は `-I -B` で起動し、stdin は DEVNULL、cwd は専用領域、shell=False とする。

`src/quick_slide/oscompat.py:popen_group`（217行）・`kill_group`（229行）・`end_group`（264行）を使い子孫を終了・後始末する。既存 `worker/proc.py:run_process`（46行、stdout 蓄積は58行以降の out_chunks と read_out）は出力を無制限に保持するため、そのまま使わず素材用のバイナリ読取を新設する。stdout/stderr の累計バイトを受信ごとに監視し、上限超過・timeout・取消時は組全体を止める。監査イベントには準備 ID・リソース ID・操作・argv の SHA-256・終了コード・stdout/stderr バイト数だけを残し、本文・実パス・argv 原文は残さない。

初版の設定可能な上限は3群に絞る: (1) 1操作30秒・準備全体120秒（最大100操作）、(2) 1操作の stdout 2 MiB・stderr 256 KiB、(3) 入力予算（§3.5）。各群内の値は固定 policy とし、個別 UI は後回しにする。

ファイルは `realpath` 後の `commonpath` で宣言ルート内を確認し、開いた後の fstat で通常ファイルとサイズ・同一性を確認する。Windows は normcase で比較し、8.3 短縮名・ADS（`file.png:stream`）・別ドライブ・UNC・デバイス名を断る。短縮名かどうか確実に判定できない環境は停止する。symlink/junction による逸脱も断り、可能な OS では no-follow で開く。開いたハンドルの実体パスも再確認できなければ停止する。

同じハンドルで画像ヘッダから画素数を先に判定し、上限内だけを読み、ハッシュ化・コピーする。前後の fstat（サイズ・mtime_ns・識別子）とコピー内容ハッシュを照合し、読取中の変更を検知したら停止する。不変の画像入力を要求し、検知できない敵対的書換への隔離保証はしない。全件を再帰コピーせず選択した組だけを保存する。意味・見た目の一致は描画後の確認事項として残す。

```text
宣言検証 → 登録を確認 → 指紋を記録
  → qs-read list（全件） → サーバで絞り込み → 準備係が候補を解釈
  → 台本係がページ別 ref ID を選択 → qs-read get --item <id>
  → 組全体を検査・コピー・ハッシュ化 → ページだけへ束縛
```

### 3.4 台本と参照のデータ契約

提案: 準備カタログの参照 ID は run の準備世代内で安定とし、資源名・一覧 item_id・許可された役割を結ぶ。
台本係に渡す ID はサーバが列挙したものだけ。取得前は候補、取得とコピー後に実体ハッシュの付いた「準備済み参照」とする。
台本係が候補を選ぶ段と、描画可能と認定する段を分ける。

```json
{
  "preparation_id":"preparation-id",
  "references":[{"id":"ref-17","resource_id":"resource-id","item_id":"item-17",
    "roles":["character"],"description":"説明書きに基づく用途"}],
  "bindings":[{"page_id":"p-2","ref_id":"ref-17","role":"character",
    "name":"案内役","pose":"説明する","reason":"ページの指定に従う"}]
}
```

内部台本はページに `_references: [{id, role, slot?, name?, pose?, reason}]` を持つ案とする。
`slot` は画像枠のキーであり、JSON Pointer 等の任意書換指定にはしない。エンジンへ渡す前にこの内部キーを除く。
選択後の manifest には `ref_id → assets 内の相対パス・sha256・media_type` とページ別束縛を持つ。
キャラクター、持込画像、構図見本、共通参照を別の role とし、ページ限定の参照を共通参照へ昇格しない。

`writer/prompt.py` の初稿・直し・差し込みに、共通の `writer.md` 全文、準備カタログ、対象ページの既存束縛を専用欄で渡す。
承認済み台本の枚数・順序・主張・素材指定が構成より優先する。原稿の材料と指示の区分は維持する。
参照を選ぶ LLM に実パス・コマンドを生成させない。本文やタイトルに参照 ID を描くことも禁じる。

`writer/patch.py` の `_check_page`・`parse_revision`・`parse_insert` を参照 context 引数付きにし、初稿にも同じ validator を通す。
検査は ID の存在、準備世代、許可 role、対象ページ／slot、name / pose の型、重複・競合、未取得・欠落・ハッシュ違いを含める。
`apply_revision` は次を区別する。

| 指定 | 処理 |
|---|---|
| 1ページの直しで `_references` を省略 | 元の参照と配置を継承する |
| `_references: []` | 明示削除。必須素材指定と矛盾したら停止する |
| 分割で複数ページにする | すべての分割先に配列を明示。空も可だが、省略から元の絵を全ページへ広げない |
| 差し込み | 新ページごとの指定を検証し、既存ページは変えない |
| route=image / keep | 元の束縛を維持。参考添付はその修正操作だけに適用する |

`_characters` は一律禁止を解除するのでなく、準備済み character 参照を持つページのサーバ生成値だけ許す。
LLM 出力では `_references` を基本とし、任意パス入りの `_characters` は引き続き拒む。
承認済み台本に既存 `_characters` があれば、準備済み参照へ同定して一致を検査してから正規化する。
エンジン形式としての `_characters` 許可は準備 context によって限定し、`_layout` も登録済み構図への変換以外は許さない。
_hidden / _pin / theme / footer / page_numbers の権限を同時に緩めない。

### 3.5 記録・上限・中断再開

`record.preparation` は世代ごとに `stage`・入力 digest・完了ファイルのハッシュ・フォルダの許可対象 ID・停止理由を持つ。
`set_run_record` のトップレベル併合に合わせ、更新時は既存 preparation 全体を読み、世代を残した全体を保存する。
LLM・CLI・画像コピー中はサーバの錠を保持しない。段の開始と完了だけ短い DB 操作にする。
一時名へ書き、検証後に置換し、manifest 完了後だけ次段を許す。実行中断を成功と記録しない。
完了済み段は入力・出力ハッシュが一致するときだけ再利用し、CLI が不完全終了した一覧を再開材料にしない。
準備係 LLM の構文・スキーマ不正は初回＋修正再試行2回（合計3回）まで。各回に検証エラーだけを返し、権限・範囲を広げない。上限後は失敗とし、必須条件の未確認を通常描画へ落とさない。

準備世代は資料内の単調増加整数を短い DB トランザクションで採番し、失敗番号も再利用しない。回は preparation_id と世代・manifest hash を明示参照し、単なる最大世代を選ばない。完了した同一入力 digest の世代だけを再利用する。
準備の失敗（素材が見つからない・契約の不備など）は新 state を増やさず、既存の failed と理由で表す。直したあとの明示再試行は新しい回を作る。`store/runs.py:update_run_args`（373–379行）は進行中だけ更新可能なので、失敗回の args に世代を後付けせず、新回の作成時に参照を渡す。

読取量の初期提案は1説明書き256 KiB、テキスト合計2 MiB、目録5,000件、画像1枚20 MiB・4,000万画素、選択画像合計200 MiB。
上限は共通 policy から読み、UI と CLI に超過理由を返す。黙った本文の要約・一覧の先頭切捨てはしない。
追加指示の確認結果は条件・根拠・判定・未確認理由を持つ。コードで確認できる条件と LLM の意味判断、描画後の目視確認を分ける。
必須の未確認条件は停止し、実行前には確かめられない見た目は「描画後に確認」と明記する。

### 3.6 PR 6 の試験

公開可能な小さな架空の画像集 CLI を試験 fixture とし、実スキルの名前・コード・画像・設定をコピーしない。
偽 LLM は渡された全文・一覧・ID を検査し、返答を固定する。子プロセス起動の有無と argv を記録して検証する。

- 説明書き優先順、一般ファイル／フォルダ／旧添付、UTF-8、欠落、壊れた形式、上限ちょうどと超過。
- 未知契約版、未許可、許可取消、フォルダ差替え、未知操作・引数は起動前に拒否。コード・依存更新は再確認なしで実行し、更新後の指紋を記録する。
- 「生成しない」「dry-run」付きの書込操作も拒否。説明書きの悪意ある指示や LLM の任意コマンドを無視。
- 環境の秘密値が子に渡らず、shell を起動しない。timeout・大量 stdout/stderr・孫プロセスを終了できる。
- 明示登録した外部ルート内の画像取得は成功。symlink / junction 逸脱、UNC、ドライブ違い、URL、特殊ファイル、巨大画像は停止。
- 一覧の不備・重複 ID、取得で別画像・範囲外画像・欠落、読取中の変更を検出する。
- 初稿・直し・差し込みの原文保持、未知 ID、role / slot / ページ逸脱、省略・空配列・分割、対象外ページ不変。
- 段の全境界で中断し、コピー後に元画像を変更・削除しても完了控えで再開する。未完了入力の変更は新しい準備世代で扱う。スキルのコード・依存更新だけでは停止・再確認せず、完了済み控えを勝手に取り直さない（§2.3 の停止条件はスキルの実装指紋で実行を束縛する意味にはしない）。

6a は子プロセス起動数0と、準備用 engine view の `_references` 除去・変換を偽 engine で検査する。6b は登録されたフォルダの契約の list/get の実行、リソースから外したフォルダは起動しないこと、コード・依存を更新しても止まらず指紋が控えに記録されることを試す。リソースのタブに注意書きが出ることをブラウザ試験で確かめる。
Windows CI では実際の argv・空白や日本語のパス・junction・子プロセス終了を確認する。
OS の隔離を採る場合はその OS で禁止書込・禁止通信を試し、利用不能時の停止も確認する。

## 4. PR 7: 描画と直接実行

### 4.1 モード別変換

6a で最小版を先に作る `preparation/engine_input.py:materialize_engine_input` が確定台本・準備済み参照・実効設定から engine.json を作る。
6b 以降はワーカーが台本確定前にこの変換器を呼び、`_references` を除去し取得済み参照を変換した engine view を `check_candidate` に渡す。6a ではこの接続を偽の検査器で試験する。現行 `worker/pipeline.py:check_candidate`（553–578行）は挿絵変換後にエンジン検査を起動するため、内部台本を直接通さない。
`worker/pipeline.py` の check_candidate と render_and_import、`worker/edit.py` の修正が同じ変換結果を使う。
検査のときだけ別の参照や最新設定を使う経路を作らない。非表示ページは既存の engine_view の扱いに合わせ、対応表はページ ID で保持する。

| 用途 | IMAGE（pitch / image-slide） | HTML（presentation / html-slide） |
|---|---|---|
| 既存画像を使う | 型の画像枠の `image.path` 等へコピー先を入れる | 同じ型の画像枠へ配置し、必要なら既存 overlays の配置情報へ変換する。生成へ置き換えない |
| キャラクターを描き込む | ページの `_characters: [{name,image,pose}]` へ変換 | 生成枠が必要な指定だけ `_characters` を使う。既存画像をそのまま置く指定なら path を使う |
| 構図の手本 | 検査済み layout YAML を `--layout`、ページに `_layout` | IMAGE の `_layout` を流用しない。型の layout・画像枠・配置へ変換できなければ停止 |
| 全ページ共通の参照 | 明示されたものだけ `--ref` として渡す | 同じ CLI 引数はない。既存枠への対応がない必須指示は未対応として止める |
| 追加指示・ブランド | コンパイル済み style.yaml・theme・描画指示・ロゴ等を使う | 台本・配置・画像生成 prompt に指示を渡す。style.yaml を渡しただけで反映済みとしない |

`skills/html-slide/scripts/imagegen.py:characters_of`（48行）・`text_of`（100行）と `skills/html-slide/scripts/deck.py:build_html`（432行） の処理では、キャラクターは最初の生成枠に結ばれ、枠がない場合の警告もある。
必須の登場をその警告のまま完成扱いにしない。正確な既存絵の掲載と、参照をもとに描き込むことを選択理由に記録する。
HTML の静的レイアウトは engine.json の型と配置で表し、画像生成の追加指示はサーバが共通原文を該当 prompt に渡す。
必要な指示が既存の入力で表せない場合は、描画アダプタの対応を追加するまで停止する。自由文を黙って破棄しない。

### 4.2 参照画像の並びと上限

採用する上限は IMAGE / HTML とも画像生成1回あたり5枚。HTML 通常生成に現状その事前検査があるという意味ではない。
`skills/image-slide/scripts/image_slide.py` の `plan`（60行）・`plan_one_page`（122行）・`prepare_revision`（175行）、
`skills/html-slide/scripts/imagegen.py:MAX_REFS`（28行）と `html_slide.py:cmd_revise`（259–261行）を根拠にする。
HTML の `imagegen.py:characters_of`（48–82行）は型・パス等を検査するが枚数は検査しない。初版はサーバの HTML 変換器がエンジンの `MAX_REFS` を共有定数として読み、通常生成の各枠も事前検査する。html-slide の通常生成自体への検査追加は別変更とし、未実装のエンジン検査で保証したとは書かない。
サーバの参照数検査と同じ実効入力でエンジン検査も通す。HTML 通常生成の枚数拒否はサーバ側の責任として別に試験する。

| 経路 | 検査対象 |
|---|---|
| IMAGE 通常 | 画像枠 → 構図の見本 → ページの characters → 共通 --ref。合計5枚まで |
| IMAGE one_page | エンジンが全ページをまとめた実際の参照列。ページごとに5枚以下でも凝縮時の合計を再検査 |
| IMAGE 微修正 | 元の参照列 → 土台の現在画像1枚 → --attach。remake は土台を数えない |
| HTML 通常生成 | その生成枠に実際に渡す characters。静的に配置する画像は生成参照数に入れない |
| HTML 修正 | その枠の characters → 微修正の土台 → --attach。上限5枚 |

参照の重複排除や順序変更で番号をずらさない。画像集の全件数は生成参照数の上限と無関係。
上限超過でキャラクターを落とす・画像を合成して回避する・微修正を remake に自動変更することはしない。
各ページ／生成枠の ordered_refs と役割を manifest に控え、描画・直し直前にも検証する。

### 4.3 prepared-run と共通 command builder

PR 6b の準備・エンジン検査完了と PR 7 のモード別描画接続完了を経て、共通契約 §6 の prepared-run を確定する。
配置先は DataRoot の回の accessor を通し、直接入口も同じ構造を使う。

```text
prepared-run/
  manifest.json
  engine.json
  settings/              # style・theme・layout・ロゴ・実行する字体
  assets/                # 今回選んだ画像だけ
  reference-records/     # 説明書き・依存目録・理由・ページ対応・遵守状況
```

manifest は形式版、mode、コンパイラ・契約・エンジン契約の指紋、テンプレート／ブランド改訂、snapshot hash、
使用スキルの宣言・entry・依存の各ハッシュと実装指紋（追跡用）、確定台本 hash、各ファイル hash、実効オプション、ordered_refs、準備世代、遵守状況、`render_ready=true` を持つ。
`render_ready` は入力が描画可能という意味であり、見た目の合格や生成済みを意味しない。
設定の compiled ZIP は従来どおり false のままにし、準備済みと誤認させない。

ファイル参照は控えの中で閉じる相対名を基本とし、builder が実行先へ絶対化する。
読み込む時は manifest の型・全ハッシュ・内部参照・逸脱パスを再検証する。
manifest に任意 argv や executable を持たせて実行しない。実行時の対応エンジンから安全な引数を再構築する。
資産や字体が欠落したら元のリソース・現在の機体設定へ取りに行かず停止する。

`engines.py` の Engine を共通 builder として拡張し、style_file / layouts / common_refs を型付きの実効設定として受ける。
check / deck / revise の style_args・brand_args は同じ確定設定から組む。既存の組み込み style 名を受ける互換経路は保つ。
IMAGE の既存 `--style <YAML>`・`--layout`・`--ref` を使い、エンジンへテンプレート解釈を持ち込まない。
直接入口が別に引数を手組みすることは禁止する。

提案する直接 CLI は `prepare`（snapshot またはテンプレート指定・原稿／承認済み台本 → prepared-run）と
`render --prepared-run <フォルダ>`（検証済み控え → 同じ Engine builder）を分ける。
共通サービスを呼び、道具なし LLM、許可・停止・上限をサーバとそろえる。直接入口の prepare でも独自の要約をしない。
既存の image-slide 直接実行はそのまま残し、テンプレート経路の直接実行だけを共通準備へ接続する。

サーバと直接実行の比較では、控えのルート・出力先・Python / エンジンの配置先だけを役割別の記号へ正規化する。
オプションの欠落・順序・素材差を正規化で隠さない。描画作業先と immutable な prepared-run を分け、生成物で入力控えを上書きしない。
再開は完了 prepared-run を読み、直しは土台画像・添付・note を含む新しい派生記録を作る。

### 4.4 完成画面と PR 7 の試験

テンプレートで作った資料の完成画面に **「指示どおりか確かめてください」** と表示する。
遵守状況と未確認項目を開けるようにし、LLM の自己申告だけで「すべて守りました」と表示しない。
停止時は不足・未対応・許可待ちの理由と次の操作を出し、完成の文言を出さない。

試験では同じ prepared-run を一時サーバと共通 builder の関数呼出へ渡し（直接 CLI は追加後に同じ試験へ含め）、engine.json・全資産・theme・style・実効引数を比較する。
check / deck / revise、5枚ちょうど／6枚、微修正の土台、one_page、HTML の複数枠・生成枠なし、非表示・並べ替え・差し込みを含める。
元リソースを変更・削除した後の再開、manifest 改ざん、資産ハッシュ違い、外部パス、未知 mode / option で停止を確認する。
ヘッドレス Chrome で完成文・遵守状況・失敗時の表示を確認し、公開可能な小さな例で IMAGE / HTML の生成と修正を目視する。
実モデルを呼ばない各 suite と実生成の結果は別に残す。

## 5. 合格の確かめ方

自動試験に加え、既存のスキルから書き起こしたテンプレートで比較する。
元のスキル・原案・画像・生成物・実行ログはローカルのデータ領域だけで扱い、このリポジトリや PR・Issue に入れない。
公開するのは一般化した手順と、公開可能な fixture の試験結果だけとする。

### 5.1 元の手順との比較

1. 元のスキルのページ構成・追加指示・参照画像の選び方を、テンプレートの4項目へ書き起こす。
   元スキルの生成・投稿等の操作は素材準備の権限にしない。使うテンプレート版・ブランド版と実体ハッシュを固定する。
2. 一つの承認済み原案を用意する。ページ ID、枚数と順序、各ページの主張、画像を出すページ・役割、明示的に出さないページを確認する。
   テンプレートの構成より承認済み原案を優先する。比較途中で片方だけ原案を直さない。
3. 経路 A は元スキルの手順に従い、image-slide を直接使って作る。使用台本、画像対応、描画設定・引数・生成物を控える。
4. 経路 B は同じ原案を Quick Slide へテンプレート指定で投入する。準備の一覧・選択・取得、確定台本、prepared-run と生成物を控える。
5. 描画前に両経路のページと主張・素材対応を照合する。違いがあれば、原案解釈・テンプレート化・読解・素材解決・変換のどこで生じたかを特定する。
6. 同じ寸法で各ページを横に並べ、下表で比較する。差を修正する場合は原因と新しい版を明示し、同じ入力で比較をやり直す。

| 観点 | 合格の見方 |
|---|---|
| ページ数と順番 | 承認済み原案どおり。勝手な追加・統合・削除がない |
| 各ページの主張 | 見出し・要点・数字の意味を保持。別ページへ主張を移していない |
| 参照画像 | ページ ID ごとの ref ID・ファイル hash・役割を照合。指定ページだけに出て、人物・用途・ポーズが取り違えられていない。実際の生成画像にも現れているか目視する |
| 見た目 | 色・ロゴ・字体・帯・余白・構図・文字密度・絵の扱いを確認。追加指示にある条件を1件ずつ判定する |
| 未確認 | 自動検証で確かめられない意味・造形・視認性は目視結果を記録。満たせない必須条件があれば不合格 |

この比較は「元スキルの意図をテンプレートへ移せたか」の受入確認である。
別々の LLM 呼出で台本を作ったなら、同じ台本だったとは扱わず、差分を記録する。画素単位の一致も求めない。

### 5.2 共通経路の一致

上の比較とは別に、一つの確定台本と選択済み素材から作った prepared-run を、共通 builder を関数呼出で使って描く。直接 CLI の追加後には同じ試験を入口からも行う。
同じモード内で engine.json・資産・実効設定・正規化 argv が一致することを機械で確認する。
これが共通契約の「同じ結果」の確認であり、元スキルとの目視比較だけで代用しない。
HTML でも公開可能な原案を使って同じ比較を行う。IMAGE と HTML の見た目を一致させる試験にはしない。

## 6. 製品の判断が要る点

担当方式とスキル対応の開始時期、版省略時の最新、style-id 併用拒否は本設計で再び未決に戻さない。
以下は判断時の選択肢の記録である。§6.1 の決定を優先し、初版の具体的な範囲・停止方法は§1〜4で定める。2026-10-17 のフォルダ単位の許可と追跡用指紋の決定を初版に反映する。

| 論点 | 選択肢と推奨 |
|---|---|
| 読取 CLI の信頼範囲 | 利用者が登録フォルダを信用し、宣言された契約の固定操作だけを実行する。指紋の検分台帳・不変コピーは不要。OS 隔離で任意 CLI を動かす形は採らない |
| 許可の寿命と画面 | **決定済み**（§6.1）。リソースへの登録を実行の許可とみなし、別の許可の仕組みは置かない |
| 停止後の体験 | 理由を出して明示再試行／同じ回に確認待ち専用 UI。初期は理由付き停止と明示再試行を推奨。許可待ちの対象フォルダ ID を保ち、許可後の再試行でも対象と契約・ルートを再検証する |
| 通常ファイルの対応形式と上限 | 限定読取から開始／文書抽出器も含める。初期は §3.1 の形式と §3.2・§3.5 の上限を推奨。未対応は変換案内を出して停止し、スキル CLI 対応は6bを含む初回出荷に含める |
| 控えの保存期間 | 元資料と同じ期間／独立期限。参照される snapshot・準備世代・画像・字体を元資料とともに保持することを推奨。削除・容量管理で再現可能性が失われる場合は明示する |
| 既存資料を新版へ移す入口 | 新資料作成／組み直しに選択追加。初期は新資料作成を推奨。旧成果物の再利用と、新条件での再準備・再生成を区別する |

### 6.1 決定（2026-10-17、利用者）

- **読取 CLI の信頼範囲**: 利用者が信用したスキルフォルダから、`image-catalog/1` の宣言・固定 argv の list/get・JSON スキーマ・パスの包含に従って直接実行する。検分済み指紋の台帳と、許可時の不変コピーは設けない。OS の隔離で任意の CLI を動かす形は採らない。
- **許可**: テンプレートのリソースに登録したことを、そのスキルの読み取りを実行してよいという許可とみなす（設定画面・会話 CLI・取り込みを区別しない）。確認ボタン・許可データ・資料ごとの許可は設けない。スキルの中身が変わっても止めず、使ったときの中身の指紋を控えに記録する。リソースのタブに「中身を確かめた、安心できるものだけを登録してください」という注意を小さく出す。
- **スキル更新と追跡**: コード・依存の変更で停止・再確認せず、実装指紋で実行を束縛しない。資料作成時の宣言・entry・依存のハッシュを `reference-records` / manifest に記録する。読取中のフォルダ更新は、操作前後に記録した指紋と取得結果のハッシュで後から辿る扱いとし、実行コードの不変性は保証しない。
- **特定のスキル専用の処理を Quick Slide に入れない**: スキル側が、Quick Slide の画像集の読み取りの形（一覧と取得を JSON で返す）で答える入口を持つ。
- **維持する制約**: 最小環境・時間と出力量の上限・shell を使わないこと・宣言ルート外を読まないこと・監査イベント、6a/6b の分割を維持する。ほかの論点（停止後の体験・通常ファイルの形式と上限・控えの保存期間・既存資料を新版へ移す入口）は、上の表の推奨を採る。

## 7. この文書の確認範囲

前提文書、PR 1〜4 の保存・API・CLI、投入・look・ワーカー・台本係・エンジンの接続箇所と、既存の画像集の説明書き・一覧／取得処理を読んで作成した。
既存のスキルの CLI は実行していない。設計中の素材準備、権限管理、prepared-run、実生成の動作確認はしていない。
実装 PR では各節の試験を実行し、実測結果と未確認項目を添えて初めてその範囲を完了とする。
