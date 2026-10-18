# デザインテンプレート: 2026-10-18 決定の変更設計と実装の段取り

状態: 設計。コード・試験は変更していない。以下の関数と行番号は設計時の HEAD に対する入口であり、新しい関数名・データ項目は実装案。
[共通契約](design-template-contract.md)、[生成設計](design-template-generation.md) §6.1、ADR [0030](adr/0030-template-follows-latest-brand.md)・[0031](adr/0031-static-template-resources.md) に従う。
PR は 5 → 6a → 7a → 7b → 8。今回の変更は既存の 5・6a と保存・画面の修正として先に行い、6b は作らない。

## 1. 変更の順序と範囲

1. 保存済みデータの fixture を用意し、旧改訂・snapshot の検証経路と、作業用文書への変換を分ける（§3）。
2. 投入時ブランド選択・identity を変更する（§2）。
3. スキルの印による停止を外し、原文を同じ準備世代から台本係へ渡す（§4）。
4. Markdown・API・CLI・画面の旧添付を外し、案内と会話の入口をそろえる（§3・§5・§6）。
5. 変異で確認し、対象試験と全体試験を実行する（§7）。描画の停止は維持する。

新規資料への適用が未対応である案内を、設計の変更だけで消さない。設計の確認と実装の動作確認を分けて報告する。

## 2. 決定1: 最新ブランドで投入し、その後は固定する

| 変更・確認箇所 | 段取り |
|---|---|
| `src/quick_slide/store/template_snapshots.py:38` `resolve_design_template` | 現在の `brand_revision(view["brand_revision_id"])` を、選んだ親ブランドの current 改訂取得へ変更。`_selection_brand:97` の返り値または同トランザクションの store 読取で current を得る。親の生存・所有・モード・期待テンプレート改訂の検査を保つ |
| 同ファイル `save_template_snapshot:135`・`template_snapshot:147` | snapshot の brand 改訂 ID・config・assets・sha を投入時の最新で確定。既存行を更新しない。読み出し時に current を引かない。実装時には関数名で位置を再確認する |
| `src/quick_slide/store/intakes.py:84` `create_intake` | 選択・identity の確定・送り直し判定・snapshot・deck/run/job の保存を同じトランザクションで行う現行境界を維持。返すブランド版も snapshot と一致させる |
| `src/quick_slide/render/look.py:52` `resolve`・`:262` `_template_record` | テンプレート利用回は snapshot の brand を使い続ける。保存時ブランド版と投入時ブランド版を混同せず、最新への追随処理をここへ足さない |
| `src/quick_slide/store/edits.py:47` `deck_run_args`・`:59` `create_edit`、`store/template_snapshots.py` `deck_template_snapshot_ref`、`src/quick_slide/store/management.py:135` `duplicate_deck`・`:178` `_duplicate_pages`、再開経路 | snapshot の継承を維持。まだ完成した回がない資料も deck 側の参照を使う。資料の複製で新規テンプレート選択を呼ばない |
| `src/quick_slide/store/design_templates.py:357` `_insert_revision`・`:370` `_view` | テンプレート保存時の brand_revision_id を記録する既存処理は残す。履歴を投入のブランド指定に読み替えない |
| `src/quick_slide/server/template_routes.py:125` `_compile_inputs` | 設定画面のコンパイル確認も親の最新ブランドを使う。生成回の snapshot 再生とは別の用途。現行は保存時ブランドを参照するため変更 |

identity の形は既存の `{template_id, version, revision_id, template_sha256, brand_id, brand_revision_id, brand_sha256, explicit_overrides}` を維持する。
ブランド版だけが変われば別投入。同じ確定選択なら既存の送り直し期間・会話 HMAC・force_new_deck の意味を変えない。
過去版指定でも最新ブランドを使う。テンプレートの brand_revision_id は identity に使わない。
待機中・完成済み・直し・差し込み・資料複製・再開では元の snapshot を保持する。テンプレート自体を複製したものは、次の新規投入時に親の最新を使う。
ブランドの更新でテンプレート文書を書き換えない。色などを自動抽出・上書きする機構は足さない。

## 3. 決定5: テンプレート文書の attachments を撤去する

### 3.1 新しい形と読み込み境界

新規保存の文書は `resources: [{id, name, kind, path, usage}]` だけを持ち、`attachments` は持たない。
保存 schema は4へ上げる実装案とし、旧 schema 1・2・3 を読み込めるようにする。Markdown の形式版1は項目が変わらないので維持する。
コンパイラは新版を発行し、旧版4の指示・ハッシュの互換検証経路を残す。snapshot schema は既存形を読めるまま、変換を snapshot の保存内容へ逆流させない。

変換はサーバ側の共通部品に集約し、画面だけの変換や API ごとの変換にしない。
純粋な文書の検証と、管理下コピーの実体を解決する処理を分ける（後者は DataRoot/store を引数で受ける）。

- 旧 `attachments: []` は外す。新 schema で未知キーとして受け入れる対象は増やさない。
- 中身があれば `{id, name, kind:"file", path:<管理下コピーの絶対パス>, usage}` へ写す。ID・名前・使い方を保持し、既存 resources に足す。
- 旧 v1/v2 の materials も同じ最終変換へつなぐ。元の文章・届け先の意味を usage に保持する既存移行を保つ。
- `file`・`url` を任意パス・取得 URL として信用しない。保存元の管理下コピーを特定し、包含・通常ファイル・内容ハッシュを照合する。ネットワーク取得・外部への再探索はしない。
- 変換は冪等にする。ID・名前の衝突や上限超過で黙って上書き・改名・削除しない。実体不明・欠落・ハッシュ違いは救済対象を残したまま理由付き停止（§8）。
- 置き場は `paths.py:DataRoot` の accessor で解決する。エンジンの置き場は places.py を使う。直し用の attachments_dir を移行先として流用しない。

### 3.2 保存済みのバイトと内容ハッシュ

`design_template_revisions.document_json/content_sha256` と `template_snapshots.snapshot_json/snapshot_sha256` は不変。SQL の UPDATE や再保存による一括変換をしない。
旧行を読むときは、元の形式に対応するアルゴリズムで元の内容を検証し、その後で作業用文書へ変換する。
既存 snapshot の `template.content_sha256`・外側の sha・compiler_version を新しい文書で再計算して一致を求めてはいけない。
検証済みの raw snapshot と、変換後の実効文書を別の値として扱う。実効文書で旧 snapshot を置換して返さない。

保存の「変更なし」は前後両方を同じ新しい実効文書へ正規化して比較する。
現在の `_save_existing` は新しい `content_sha256(doc)` と DB に保存された旧ハッシュを直接比較するため、ここを必ず直す。
空の attachments を外しただけ、または同じ管理下コピーをリソースへ表現し直しただけで版を増やさない。
比較用の正規化から attachments の専用欄を除き、通常リソースの name/kind/path/usage を使う。機械 ID・並び順を無視する既存の意味は維持する。
新規改訂に記録する内容ハッシュと、旧行の保存時ハッシュは区別する。旧ハッシュを更新して帳尻を合わせない。

新規投入の template_sha256 は変換後文書に対応する新アルゴリズムを使い、新 snapshot の文書と一致させる。
過去の投入 identity・request_sha は更新しない。形式・ハッシュ算法の切替をまたぐ送り直しで旧デッキが再利用されない可能性は許容する実装案であり、旧 snapshot の改変で一致させない。
旧コンパイルが完了している回は、そのファイルを当時のハッシュ・版で検証して再利用する。新コンパイラとの文字列比較で旧成果物を破損扱いしない。
旧版の再コンパイルが必要な回も元の文書と旧版アルゴリズムで復元する。復元不能なら既存の compiler mismatch の理由付き停止を維持し、新版で黙って上書きしない。

### 3.3 変更する入口

| ファイル・関数 | 変更 |
|---|---|
| `src/quick_slide/design_templates.py:20` TOP_KEYS・`:42` `new_document`・`:54` `_upgrade`・`:112` `validate` | 新 schema から添付欄・専用キーを撤去。旧読み込みだけ互換変換を通す。元の辞書を破壊しない |
| 同ファイル `to_markdown:177`・`from_markdown:197`・`resource_table:303`・`compile_document:349` | 変換済み resources を書き出し、添付付きだから書き出せない分岐を撤去。取り込みは resources だけ。対応表と指示にも別枠を作らない。旧版コンパイラの互換は隔離 |
| `src/quick_slide/store/design_templates.py:32` `normalized_sections`・`:45` `content_sha256`・`:49` `changed_sections`・`:313` `_revision_doc`・`:328` `_save_existing`・`:370` `_view` | raw の保持と新しい表示・比較を分離。raw を読む用途をすべて表示用に変えてはいけない。無変更判定・版一覧・過去版コピー・複製にも同じ実効変換を使う |
| `src/quick_slide/store/template_snapshots.py` `resolve_design_template`・`template_snapshot`、`src/quick_slide/render/look.py`、`src/quick_slide/preparation/service.py:39` `load_compiled`・`:59` `_without_paths` | raw のハッシュ検証を先に行う。コンパイラ版に対応した検証後、パスなしのモデル用 view を組む。新版 validate を旧版の再現に無条件で使わない |
| `src/quick_slide/server/template_service.py:31` `check_document`・`:48` `_attachments_of`・`:128` `save` | 新しい添付を保存する経路・所有判定と、Markdown 保存時の attachments 復元を撤去。旧実体解決に必要な検査だけを互換部品へ移す。旧 GET→新 POST、同文、plan、本保存、過去版コピーをそろえる |
| `src/quick_slide/server/template_routes.py:102` `get_template`・`:107` `list_revisions`・`:113` `export_markdown`・`:209` `_read_import`・`:220` `check_import`・`:235` `import_template` | API の表示文書・改訂一覧・Markdown・2段取り込みを新しい形へそろえる。期待した改訂 ID・Host/Origin/トークン・無書込 check・409 を保つ。Content-Disposition の attachment は HTTP の用語なので残す |
| `skills/quick-slide/scripts/quick_slide_template.py:184` `cmd_show`・`:194` `cmd_export`・`:221` `cmd_update` | サーバの Markdown を使う現行構成を維持。新しい添付引数や CLI 独自移行を足さない。show/export/update/copy/dry-run の往復と最終 JSON を検証 |
| `web/app/design-template-editor.js:7` `empty`・`:290` `redrawMaterials`・`:328` 名前検査・`:461` 版の表示 | 新規文書・画面・保存 payload・版一覧から添付欄と別枠・画像プレビューを撤去。移行後は通常リソースの編集・削除を使う |

旧添付をリソースにした Markdown も通常仕様どおりパスを含む。素材そのものの同梱や他の機体への可搬化は今回追加しない。
PR 8 のブランド ZIP でも同じ Markdown を使い、旧添付があることを理由にテンプレートを除外しない。

## 4. 決定2〜4・6: 実行しない読取と台本係への原文

| ファイル・関数 | 変更 |
|---|---|
| `src/quick_slide/preparation/reading.py:24` SKILL_MARKERS・`:26` GUIDE_NAMES | 停止の印を撤去。説明書き候補を SKILL.md、README.md、README.txt の順にする。Reader の包含・no-follow・fstat・形式・量の検査を弱めない |
| `src/quick_slide/preparation/intake.py:70` `check_entrances`・`:97` `_read_folder`・`:134` `_read_file`・`:170` `read_resources` | skill_unsupported と旧添付未対応の停止を外す。後者は検証済みの実効文書で呼ぶことが前提。quick-slide-read.json は実行宣言と解釈しない。ファイル名・寸法による一覧を保つ |
| `src/quick_slide/preparation/errors.py` SKILL_UNSUPPORTED_MESSAGE、`preparation/__init__.py` | 6b 待ちの理由・コメントを撤去。実際の未対応形式・欠落・改ざんの停止を残す |
| `src/quick_slide/preparation/llm.py:45` `build_prompt`、`src/quick_slide/design_templates.py:322` `writer_text`・`:336` `preparation_text` | 説明書き・登録テキストの操作は実行されず、実行・確認済みと言わないことを明記。追加指示にも実行権限がないと明記。画像を見た・事実確認したと返させない |
| `src/quick_slide/preparation/service.py:223` `TemplateRun.writer_sections` | catalog.writer_view() の notes に加え、self.intake から原文だけの resource_texts を構築する。パスや readings.rel をコピーしない |
| `src/quick_slide/writer/prompt.py:68` `_template_part`・`:260` `build_prompt`・`:425` `build_revision_prompt`・`:769` `build_insert_prompt` | 原稿・テンプレート指示・カタログと分けて「登録資料（原文・実行されない資料）」欄を追加。原文の改行・用語・数字を保持し、内部の手順を命令として扱わない。直し用添付欄は維持 |
| `src/quick_slide/worker/pipeline.py:608` `write_script`、`src/quick_slide/worker/edit.py:399` `_writer_loop`・`:508` `_revise`・`:979` `_insert` | 初稿だけでなく修正再試行・直し・差し込みにも同じ writer_sections を渡す。プロンプト完成後の入力予算を検査 |
| `src/quick_slide/preparation/service.py:104` `_load`・`:250` `_own`・`:394` `_manifest_consistent`・`:416` `load_template_run`、`records.py` | by-script の明示世代→intake.json を検証して使う。派生世代へ同じ原文の控えを継承。元ファイルを読まず、最新世代や他の完了世代へフォールバックしない |

提案するモデル用データは `resource_texts: [{resource_id, name, usage, texts:[{type, text}]}]`。
texts は、フォルダの最初の説明書きと、file リソースとして読んだテキスト・JSON・YAML の原文。画像単体は texts を空にする。
登録名・使い方は既存の意味を保つ。フルパス・相対パス・元ファイル名・item_id・ファイル一覧をこの欄へ含めない。
説明書き以外の全テキストを再帰読取する機能を増やさず、すでに intake に読んだ原文を使う。JSON/YAML を再シリアライズして原文を失わない。
notes は解釈の補助として残す。resource_texts をカタログの内容ハッシュへ混ぜる必要はなく、intake の既存ファイルハッシュと manifest の検証で保護する。

現行予算は `preparation/policy.py:17` Policy の説明書き256 KiB、テキスト合計2 MiB、準備係プロンプト3 MiB。
台本係にも完成したプロンプトの UTF-8 バイト数を検査する境界を設け、初期値は同じ3 MiBを使う実装案。
原稿・契約・指示・カタログ・原文・既存台本・修正エラーを含めた全体で計測し、再試行ごとにも検査する。超過なら対象と整理方法を示して停止する。
既存の長さ制限と混同せず、他形式・テンプレート無しの資料にこの予算を無断適用しない。増枠が必要なら数値を利用者に確認する。

パスを出さない検査は Unix・Windows・UNC・~/ の登録パスと元ファイルの相対名に固有の印を置き、準備係と台本係の全プロンプト、モデル用 JSON を捕捉して行う。
一般的な見出しや「リソース」という本文があっても原文を切らない。構造化データから view を作り、文字列置換や見出しの split でパスを消さない。
原文そのものにパスが書かれた場合の扱いは §8。パスがない原文は全文一致を検査する。

構図の見本・全ページ共通参照の役割停止、HTML 参照変換の停止、PR 7 前の描画停止は維持する。
`catalog.py` SUPPORTED_ROLES、`references.py`、`engine_input.py` の制限を今回広げない。自動画像目録のための画像モデル呼出も足さない。

## 5. 決定7: 編集画面の案内

対象は `web/app/design-template-editor.js:236` 付近の instructionsGuide、`:266` の resourceExamples と resourceGuideCard、`:404` の注意書き、追加指示・リソースの panel。
必要な見た目は既存のヘルプ・コピー・small muted の部品でそろえる。新しい設定値は保存しない。

| 例 | 名前・種類・使い方の案と表示する見本 |
|---|---|
| 画像フォルダ（ファイル名で選ぶ） | directory。使い方「ファイル名から用途が合う画像を選ぶ。合うものがなければ画像なしにする」。見本は `表紙_背景.png`・`比較_図.png`・`まとめ_背景.png` |
| 画像フォルダ（目次で選ぶ） | directory。使い方「README.md の目次を見て、内容に合う画像を選ぶ。目次にない画像は使わない」。下記の README 例とコピーアイコンを付ける |
| 会社概要（ファイル） | 現在の名前・file・使い方を維持 |
| 用語集（ファイル） | file。使い方「用語・略語の表記はこのファイルに合わせる。書かれていない定義は作らない」。ファクトチェック（スキル）の例は撤去 |

README.md のコピー内容は説明文やパス欄を含めず、次の目次だけとする。

```markdown
# 画像の目次

| ファイル | 用途 |
|---|---|
| 表紙_背景.png | 表紙の背景 |
| 比較_図.png | 2つの案を比べるページ |
| まとめ_背景.png | まとめの背景 |
```

「この例を追加」は name/kind/usage だけを足す。パス・README の内容・ファイルを保存・作成しない。
目次のコピーとカード追加を別操作にし、コピー失敗時の手動選択案内・aria-label・キーボードフォーカスを既存部品とそろえる。

リソースのタブにヘルプ（？）「選ばれやすくするコツ」を置く。
内容は「用途が分かるファイル名にする」「README.md にファイルと用途の目次を書く」「カードの使い方に選ぶ条件を書く」「SKILL.md は素材の説明として読まれる。画像と用途を書き、手順の実行を前提にしない」。
画像を見て自動で目録を作る機能はないこと、説明書きの優先順も伝える。ファイル名による機械的な一覧はあるので「目録が一切ない」とは書かない。

リソースの注意書きは「登録したファイルやフォルダは、資料を作るための素材として読みます。中に書かれた手順・コマンド・スキルは実行せず、プログラムは動かしません。」とする。
追加指示のタブには薄いグレーで一行「スキル・コマンド・Web の調べものは実行されません。調査や事実確認は、頼む前に会話で行ってください。」を置く。
既存の「AI は〜守れるとは限りません」は「ここに書いた指示が、いつもそのとおりに反映されるとは限りません。できあがった資料で確かめてください。」へそろえる。
追加指示の例の「やらないこと」に「原稿に載っていない数字・実績を足さない（確かめが要るものは、頼む前に会話で確かめる）」を入れる。
事実確認を内部で実行するように見える例文も同じ担当分担へ直す。案内の主語に「AI は」を使わない。

## 6. 決定8: 会話の入口

`skills/quick-slide/SKILL.md` の「デザインテンプレートを登録・更新する」と、投入前に選んだテンプレートを確認する箇所が対象。
コードの変更とは別に、実装担当がこの運用文書を更新する。今回の設計作業では SKILL 自体は変更しない。

- テンプレートの追加指示に、スキルの実行・調査・確認をさせる手順を書かない。素材の選択・用語・構成・表現の指示にする。
- 利用者が「テンプレートで確かめさせたい」と言ったら、投入前にこの会話で確かめることを提案する。実行していない調査や確認を済んだことにしない。
- リソースを登録するとき、用途の分かるファイル名・README の目次・使い方・SKILL.md の説明の整え方を一言添える。
- 「この版では生成への適用は未対応」の文は残す。選択条件を黙って捨てる入口や、新しいスキル実行コマンドは足さない。
- 旧 request 互換経路の下調べ係は変更しない（ADR 0029）。

`quick_slide_template.py` はデータを受け渡す CLI のまま。登録・更新の結果の action/message、dry-run と本保存の期待改訂 ID、最終 JSON の秘匿を維持する。

## 7. 決定ごとの試験と変異で守る点

以下は実装担当が追加・変更する試験。設計作業では実行しておらず、成功の報告ではない。
変異は該当の修正だけを戻したときに対応する試験が落ちることを確認し、試験が期待値の置換だけにならないようにする。

| 決定 | 試験と必要な変異 |
|---|---|
| 1 ブランド | `tests/test_template_intake.py:183` の保存時ブランドを使う試験を変更。テンプレート保存→ブランド更新→過去版指定の投入でも最新を使う。ブランドのみの更新で identity・deck が変わる一方、同一ブランド改訂の送り直しは再利用。保存時 brand_revision_id の履歴は残る。**変異**: 保存時ブランド参照へ戻すと落ちる |
| 1 固定の継承 | `tests/test_template_snapshot.py` の待機後更新・削除、直し・差し込み、未完了資料の複製、再開・コンパイル復元を拡張。新しい投入だけ最新になり、古い資料は snapshot ID・brand ID・theme hash を維持。`tests/test_design_template_store.py` の保存時履歴の試験は保持。**変異**: worker/edit/duplicate/resume のいずれかが最新を再解決すると該当試験が落ちる |
| 2 通常フォルダ | `tests/test_template_preparation.py:146` 説明書き優先順、`:169` スキル拒否、`:1317` サーバ拒否を変更。SKILL.md・README.md・README.txt に異なる印を置いて最初の1つだけ届くことを検査。quick-slide-read.json 単独、不正な宣言、SKILL と宣言併存も通常読取。宣言を実行・検証しない。**変異**: skill_unsupported を戻す、優先順を戻すと落ちる |
| 2 実行しない | 実行可能な偽 reader と、手順・コマンド・スキル呼び出し入りの説明書きを配置。準備係・台本係・エンジンを偽物にし、素材読取を起点とする subprocess.run/Popen・os.system・popen_group 等を呼ぶと即失敗させる。ファイルに副作用の印も作られない。サーバ試験では本物の起動などを検査区間の外に置き、正規のモデル CLI まで禁止した試験にしない。**変異**: 偽 reader を起動する1行を差すと落ちる。プロンプトの禁止文を外した場合も捕捉して落ちる |
| 3 自動目録なし | 画像モデル・外部カタログ・素材 CLI へ接続する偽物を呼出禁止にし、用途の違うファイル名・README の説明が選択入力に届くことを検査。画像を見て解釈したとする記録が出ない。ヘルプ表示は実ブラウザで確認 |
| 4 原文 | `tests/test_template_preparation.py:716` 初稿と `:805` 直しの流れを拡張。notes に無い独自の数字・用語・末尾の文を原文に入れ、初稿・修正再試行・直し・差し込みすべての prompt で全文一致。**変異**: 原文欄を外す、notes だけに戻す、直し・差し込みの片方だけ原文を外すと、それぞれ落ちる |
| 4 世代とパス | 元テキストを変更・削除し、別世代に異なる原文を置いても head 台本の世代の原文を使う。intake/manifest の改ざん・欠落では停止。登録パス・相対名の印が全モデル入力に無い。**変異**: 最新世代へ切り替える、元ファイルを再読する、hash 検査を外す、readings を丸渡しする変更で落ちる |
| 4 予算 | UTF-8 の日本語を使い、完成プロンプトの上限ちょうどと1バイト超過、直しの既存台本・再試行エラーによる超過を試す。超過で LLM を呼ばない。**変異**: 文字数で測る、原文を除いて測る、再試行で検査しないと落ちる |
| 5 保存互換 | `tests/test_design_templates.py`・`test_design_template_store.py` に旧 schema の空添付・実体付き添付・新形式を用意。元の document_json/content_sha256 が全件不変、変換は冪等、同文保存で行が増えない。内容ありは名前・ID・usage・管理下コピーを保持し、改名や破棄で通さない。**変異**: pop するだけ、旧 DB ハッシュと直接比較する、旧行を書き換えると落ちる |
| 5 snapshot と旧コンパイル | `tests/test_template_snapshot.py` に保存バイトとハッシュを固定した旧 snapshot・旧版コンパイルを用意。空・中身あり双方の読取、再開、旧版ハッシュ不一致の停止を確認。**変異**: validate 後の値で旧 sha を計算する、新版 writer_text で旧版を照合する、変換済みの値を DB に戻すと落ちる |
| 5 境界の往復 | `tests/test_design_template_api.py`・`test_skill_template.py`・`tests/live/test_design_templates_screen.py` で旧 GET→編集→保存、過去版表示・コピー・複製、Markdown export/import、CLI show/export/update/dry-run を一巡。attachments が表示・保存文書・Markdown に無く、移行済み resources が残る。移行不能なら無書込で理由が返る。**変異**: Markdown 保存で旧添付を復元する、書き出し拒否を残す、画面だけ別枠を残すと落ちる |
| 6 停止維持 | `tests/test_template_preparation.py` の unsupported_role、HTML 参照変換、PR 7 前の描画停止を維持。**変異**: layout/common を許可、旧描画へフォールバックすると落ちる |
| 7 画面 | `tests/live/test_design_templates_screen.py` に4例・目次コピー成功/失敗・例追加で path が空・ヘルプ・注意文・旧添付 UI 不在を追加。狭い画面、キーボード、タブ往復の入力保持を確認。**変異**: 例追加でパスを埋める、コピー対象に説明を混ぜる、ファクトチェック例を残すと落ちる |
| 8 会話 | `tests/test_skill_template.py` 等で必要な SKILL 文言と未対応案内を検査。会話の確認提案・整え方の案内をレビュー。**変異**: スキル実行の禁止・投入前の会話での確認・未対応案内のいずれかを外すと落ちる |

対象試験の後に `bash tests/run.sh`。エンジンへ接続する PR 7 では両エンジンと illust の suite、配布前は fresh E2E、Windows CI も確認する。
実ブラウザの skip、偽モデルの成功、実生成の確認を分けて記録する。この変更だけで実生成済みと書かない。

## 8. 利用者に確認する点と触らないもの

確認が必要になるのは、次の例外に実データや実装上の制約が当たる場合。通常の空添付の撤去や最新ブランドへの変更を再承認の対象にしない。

- **旧添付の実体を復元できない**: 現行の文書には file/url/sha256 があるが、一般の添付保管ルートをこの HEAD から確定できない。管理下コピーを識別する根拠と、欠落・同名衝突時の救済方法を利用者に確認する。推測して edit の置き場や任意の URL へ向かわず、旧データをそのまま保持する。公開リポジトリへ実データを写さない。
- **原文そのものにパスが含まれる**: 原文保持と「パスは出さない」の両方を満たせない場合、利用者に原文の整理または渡す範囲を確認する。自動マスキング・黙った要約・原文を捨てる処理は決めない。確認まで対象の投入を止める。
- **入力予算の増枠が必要**: 上限超過で停止する案内を出し、資料の整理で足りない場合に数値・対象範囲を確認する。原文を減らして通さない。
- **版・ハッシュ算法の切替をまたぐ送り直しも旧資料へ必ず一致させたい**という要望が出た場合: §3.2 の別投入になり得る案との違いを説明し、互換 identity の期間・算法を確認する。旧保存行を書き換える案は採らない。

上の4点は、利用者の確認を受けて次のとおり決めた（実装はこれに従う）。

- 旧添付の実体を復元できない場合: 名前・使い方・元のファイル名（`file`）をそのまま `kind=file` のリソースへ移し、`path` は元のファイル名にする。カードは「見つかりません」と表示され、利用者がパスを指定し直す。捨てない。管理下の置き場を推測しない。
- 原文にパスが含まれる場合: 利用者が書いて登録した文章なので、そのまま渡す。「パスを出さない」は Quick Slide が付け足す情報（登録先の場所・相対名・item_id）に限る。
- 入力予算の超過: 今の上限のまま、理由を示して止める。要約・切り捨てはしない。
- 算法の切替をまたぐ送り直し: 別の投入として扱ってよい。互換 identity は設けない。

変更禁止の範囲は、資料の直しの `--attach`、`paths.attachments_dir`、`edit_routes`、直し用添付の保存・プロンプト・許可パスの検査。
HTTP Content-Disposition の attachment も削除対象ではない。`attachments` の文字列をリポジトリ全体で一括削除しない。
`skills/quick-slide/scripts/quick_slide_setup.py` と `tests/test_doctor_template_resources.py` は変更しない。
旧 request の互換下調べ、Motion、ブループリント、PR 7 前の構図・共通参照対応も今回広げない。
