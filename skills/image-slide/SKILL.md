---
name: image-slide
description: html-slide と同じ台本（資料用の JSON）から、画像生成で1枚ずつ丸ごと描いた資料を作り、PPTX・PDF・ページごとの画像・サムネイル一覧にするスキル（--format で画像だけにもできる）。画像は Codex 経由の gpt-image-2（ChatGPT のサブスク枠・API の従量課金なし）で描く。認証は実行の最初に1度だけ確かめ、全ページを並列に描く（既定8本）。構図（レイアウト）は指定せず Codex に任せる。スタイルは visual（既定・プレゼン・ピッチ向け）・standard（説明・報告向け）・infographic（資料全体を1枚に凝縮したポスター）・カスタム（YAML）から選ぶ。週報のような決まった形は、カスタムスタイルとカスタムレイアウトを持ち込む。台本の文字は一字一句そのまま描かせる。ロゴ・フッタ・ページ番号は絵に描かせず、PowerPoint の部品として上に載せる（あとで直せる。ロゴは html-slide と同じく右下・ふさがっていれば左下・両方だめなら置かない）。中身を変えたページだけ描き直す。描いた1枚は指示で直せる（微修正＝今の絵を土台に・作り直し＝テイストを変える）。同じ台本を html-slide に渡せば編集できる資料、image-slide に渡せば絵の資料になる（quick-slide のピッチモードにあたる）。「画像のスライド」「絵で描く資料」「ピッチ資料を画像で」「image-slide」「台本を絵のスライドにして」「ワクワクする資料」「この1枚を微修正して」「作り直して」「サムネイルで見たい」等で発動。※編集できて動く PPTX は html-slide。
---

# image-slide — 画像生成で1枚丸ごと描く資料

台本（html-slide と同じ JSON）→ コンテンツ（何を載せるか）＋作風 → 1枚ずつ描く指示（構図は Codex 任せ） → Codex で並列に描く → 16:9 にそろえる・帯の検査 → PPTX・PDF。
**html-slide と同じ段のエンジン**で、受け口（台本・テーマ・素材のパス）も出力の形も html-slide にそろえてある。
違うのは「1枚をどう描くか」だけ（html-slide は型で HTML を組み、image-slide は絵として丸ごと描く）。

> **ドキュメント**: 設計判断は [`docs/adr/`](docs/adr/)（0001 受け口を html-slide と同じに・0002 認証は1度だけ・並列・0003 帯の上の部品・0004 レイアウトの層（0005 で置き換え）・0005 構図は Codex に任せる・0006 レイアウトは持ち込める・0007 スタイルは standard・visual・カスタム・0008 ロゴは html-slide と同じ規則・0009 visual は全面に描く・0010 透かし・0011 キャラはページごとに `_characters`・参照画像は5枚まで・0012 revise＝微修正と作り直し・0013 出力は PPTX・PDF・ページの画像・サムネイル・0014 API で描く道は見送り（同じ gpt-image-2 でも呼び方で結果が変わる）・0015 描いた絵はキャッシュに置く・0016 置き場の根っこは ~/.quick-slide）、台本の読み方と描く指示の組み方は [`references/intake.md`](references/intake.md)、Codex の認証・並列・枠は [`references/codex.md`](references/codex.md)、機体に置く手順は [`references/deploy.md`](references/deploy.md)。
> **過去に受けた指摘** → [`FEEDBACK.md`](FEEDBACK.md)。**着手前に読む。**
> **テスト**: `bash tests/run.sh`（本物の Codex は呼ばない）
> **出荷検品**: [`INSPECTION.md`](INSPECTION.md)。検品役が読むのは `<出力先>/inspect.md` と `slides/slide-NN.png`。

## いつ使うか

| 状況 | 入口 |
|---|---|
| 台本を書いた・描く指示を見たい（Codex は呼ばない） | `check`（`--prompts <フォルダ>` で書き出す） |
| Codex にログインできているかだけ見る | `auth` |
| 資料を作る（全ページを描いて PPTX・PDF・ページの画像・サムネイルまで） | `deck` |
| 週報・月報に貼る画像だけ欲しい／ざっと一覧で見たい | `deck --format png` ／ `deck --format thumbs` |
| 一部のページだけ描き直す | `deck --redo 3,5`（中身を変えたページは言わなくても描き直す） |
| 区画に置いた前の絵を、色・スタイルを替えた後でも描き直さずに使う（呼ぶ側が前の版の絵を置いて組み直すとき） | `deck --keep 3,5`（[ADR 0017](docs/adr/0017-keep-placed-pictures.md)。`--redo` が勝つ） |
| 描いた1枚を指示で直す（「Hana を右へ」「黒板の手描きに」） | `revise --page 4 --note "…"`（既定は微修正＝今の絵を土台に渡す。テイストを変えるなら `--remake`＝渡さない。参考画像は `--attach`・ADR 0012） |

すべて `python3 <このスキルのフォルダ>/scripts/image_slide.py <入口> …`（引数は `--help`）。
終了コード: 0=成功 / 1=実行時エラー（描けなかったページがある）/ 2=台本・引数の誤り / 4=依存不足・認証。

## Workflow: deck（資料を作る）

1. **台本を書く。** 書き方は html-slide の [`deck-format.md`](../html-slide/references/deck-format.md) と同じ（型・キー・`talk`・画像の枠）。ストーリー（何をどの順で言うか・枚数）は台本を書く側が決める（html-slide の ADR 0004）。見本は [`examples/sample-pitch.json`](examples/sample-pitch.json)（架空の題材・5枚）
   - 絵で描くので、**文字の長さ・talk とクリックの数では止まらない**。それでも見出しは短く（絵の中の文字は直せない）
   - **スタイル**は `--style visual`（既定・ビジュアル寄り。プレゼン・ピッチ向け。**本文のページも全面に描き**、フッタ・ページ番号・ロゴは絵の上に重ねる・ADR 0009）か `--style standard`（説明・報告・社内資料向け）か `--style infographic`（**台本の全ページを1枚に凝縮**したポスター。カスタムレイアウトは1枚目の `_layout` が効く）。カスタムスタイルは YAML で `--style <ファイル>`。一言足すなら `--style-note`
   - **構図は指定しない**（Codex が中身から決める・ADR 0005）
   - **見せ方が決まっている資料**（週報・月報など）は、カスタムスタイル（`--style`）とカスタムレイアウト（`--layout` とページの `_layout`）を持ち込む。雛形は `templates/custom-style.example.yaml`・`templates/custom-layout.example.yaml`（ADR 0006・0007）
   - そのページだけの描き方の注文は `_visual`（例: `"右上に、輪になって話す人のイラスト"`）
   - そのページに出すキャラは `_characters`（例: `[{"name": "Hana", "image": "<パス>", "pose": "考える"}]`）。絵は呼ぶ側が用意してパスを書く。番号と名前を結んで描かせ、透過は白に合成して渡す（ADR 0011）
   - `_` で始まるキーは html-slide が読み飛ばすので、同じ台本のまま両方のエンジンに渡せる
2. `python3 …/image_slide.py check --script <台本.json> [--prompts <フォルダ>]` — rc=2 なら `NG:` を読んで直す
3. `python3 …/image_slide.py deck --script <台本.json> [--out <フォルダ>] [--theme default|forest|warm|<YAML>] [--logo <png> [--logo-white <png>] [--logo-position bottom-right|bottom-left|top-right]] [--watermark <文字>] [--ref <png>]… [--style <文>] [--parallel N] [--format pptx,pdf,png,thumbs]`
   - **Codex に ChatGPT でログイン済みであること**（`codex login` を1度だけ）。認証は実行の最初に1度だけ確かめる
   - `--logo` はロゴ（PNG・JPEG）。329×44 の枠に縮めて右下に置き、写真や絵で右下がふさがったページは左下、両方ふさがれば置かない（html-slide と同じ規則・ADR 0008）。`--logo-position bottom-left|top-right` で角を変えられる（左下 → 右下・右上 → 右下）。**ロゴは加工しない**（色・縦横比を変えない）。visual の本文のページでは、空いた角が無くても指定の角に置き、ロゴが沈む所ではロゴの周りに白いステッカーを敷く（組織の公式の白いロゴがあれば `--logo-white`）。ページ番号は既定でオフ（台本の `page_numbers: true` で中央下に出る）。どこに置いたかは `build.json` の `logo.pages`。`--watermark example.com` のように渡すと、全ページの中央下の端に薄いグレーの透かしを**絵に焼く**（PPTX の絵にも焼くので消しにくい・ADR 0010）。フッタかページ番号があるページは、下の行（ロゴも）を帯の上寄りに置き、下の端を透かしに空ける。`--ref` は全ページに見せる参照画像（画風の手本など。キャラをページごとに変えるなら `_characters`）。1枚に添えられるのは画像の枠・構図の見本・`_characters`・`--ref` を合わせて**5枚まで**（Codex の画像ツールの上限・超えたら rc=2）、`--style` は作風の追加の注文
   - 5枚・並列5で約1分半（1枚あたり 40〜90 秒）
4. 出力（`<出力先>/<台本の名前>/`・[ADR 0013](docs/adr/0013-outputs.md)）。`--format` で選び、省けば4つとも（html-slide と同じ口）:

   | `--format` | ファイル | 中身 |
   |---|---|---|
   | `pptx` | `deck.pptx` | 全面の絵＋帯の部品（ロゴ・フッタ・ページ番号）＋ノート |
   | `pdf` | `deck.pdf` | ページの画像を並べたもの（文字は絵の中なので選べない） |
   | `png` | `slides/slide-NN.png` | ページの画像（1920×1080・ロゴ・フッタ・透かし込み）。そのまま渡せる。**PDF と検品の元なので、どの指定でも残る** |
   | `thumbs` | `thumbs.png`（17ページ以上は `thumbs-01.png` …） | 全ページを縮めて「番号 見出し」を添えた一覧（4列） |

   - いつも出るもの: `prompts/`・`build.json`（`outputs` に作ったものの一覧）・`inspect.md`・`logs/`
   - Codex が描いた絵と鍵は出力フォルダではなく**キャッシュの区画**（`~/Library/Caches/quick-slide/image-slide/<フォルダ名>-<ハッシュ>/`・場所は `build.json` の `cache`）。合計が `cache_max_mb`（既定 1024 MB）を超えると、古く使った区画から消える（消えたページは次の deck で描き直す）。PPTX に敷くそろえた絵は実行中だけの tmp（[ADR 0015](docs/adr/0015-cache.md)）
   - 描くのに時間の大半がかかるので、`--format png` でも速さはほぼ変わらない
   - クリップボードには入れない（html-slide の ADR 0012。呼ぶ側が `slides/slide-NN.png` を `osascript` で入れる）
5. **ページの画像を1枚ずつ見る**（ざっと見るならサムネイル）。 絵の中の文字が台本と一字一句合っているかは機械では見ていない。合っていなければ台本を直すか `_visual` で注文して、`deck --redo <ページ>` で描き直す
   - 絵の見た目を直したいときは `revise`: deck と同じ引数（`--script`・`--out`・`--style`・`--theme`・`--logo` …）に `--page N --note "<直したいこと>"` を足す。
     **微修正（既定）**は今の絵（ロゴ・透かしの前）を土台に渡し、指示だけ反映して残りを保つ。**テイストを変えたい・いくら言っても変わらない**ときは
     `--remake`（今の絵を渡さずに作り直す。キャラなどの素材はそのまま渡す）。描くのはそのページだけで、PPTX・PDF・サムネイルを組み直す（`--format` も deck と同じ）。
     前の絵はキャッシュの区画の `history/` に残る。直した絵は、台本を変えずに deck を流し直しても使い回される（台本を変えたページ・`--redo` は台本から描き直す）
6. 人に渡す資料なら、検品する側に出力先の `inspect.md` と `slides/` の画像を渡して確かめてもらう（基準はこのスキルの [`INSPECTION.md`](INSPECTION.md)。落ちたら直して出し直す・最大3ラウンド）

## 構成

| パス | 中身 |
|---|---|
| `scripts/image_slide.py` | 入口（check / auth / deck / revise）。使い回しの判定・描き直し・合成・書き出しの流れ |
| `scripts/script_io.py` | 台本を読む（html-slide と同じ型・キー）。画像の枠・ノート・見出し |
| `scripts/prompt.py` | 1枚の描く指示（製品の譲れない条項・作風と色・資料全体の文脈・型＝コンテンツ・構図は任せる・描く文字） |
| `scripts/codex_run.py` | Codex の認証（1度だけ）・隔離ホームで1枚描く・並列（枠切れの待ち・失効で止める） |
| `scripts/styles.py` | スタイル（standard・visual・カスタムの YAML） |
| `templates/` | カスタムスタイル・カスタムレイアウトの雛形（週報の例） |
| `scripts/layouts.py` | 持ち込みのレイアウト（`--layout`）を読む・ページの `_layout` を引く |
| `scripts/compose.py`・`scripts/layout.py` | 16:9 にそろえる・帯の検査・確認用の合成 ／ 帯と部品の寸法 |
| `scripts/pptx_out.py` | PPTX（全面の絵＋帯の部品＋ノート）と PDF |
| `scripts/outputs.py` | `--format` の読み方・サムネイル（html-slide と同じファイル） |
| `themes/` | 色（html-slide と同じ3つ。値がずれていないかはテストが見る） |
| `tests/` | `run.sh`・`test_logic.py`・偽物の codex（`fake_codex.py`） |

## 制約・落とし穴

- **絵の中の文字は直せない。** 直すときは台本を直して描き直す。誤字は生成のたびに変わるので、見てから出す
- **ページごとに別々に描くので、絵柄は少しずつ揺れる。** 色はテーマで、調子は資料全体の見出しの並びを毎回渡してそろえている。そろわなければ `--style` で画風を言い切る
- **OPENAI_API_KEY は codex に渡さない**（残っていると API の従量課金に切り替わる）。`auth.json` が API キーのログインなら断る
- 認証が失効したら（`token_revoked` など）残りを止めて rc=4。`codex login` で入り直す。描けたページは覚えているので、続きから描ける
- PowerPoint の GUI を自動操作しない（html-slide と同じ）
