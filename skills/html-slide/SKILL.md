---
name: html-slide
description: 台本（資料用の JSON）や HTML から、編集できて PowerPoint の本物のアニメーションで動く PPTX を作るスキル。台本を書けば、画面の型29種（表紙・目次・章扉・結論・主張・大きな数字・KPI・カード・矢羽根・比較・2×2・棒グラフ・折れ線・表・箇条書き・年表・体制・写真・ギャラリー・図・注釈つきの画像・引用・締め・式・放射・NG→OK の対・お知らせの枠・用語と説明・手順の案内）で HTML を組み、全面のヒーロー画像・左右の画像（1/2・2/5・1/3）・横長の帯・コラージュ・ビフォーアフター・画像つきカードなど画像をはめ込む置き方と、画像なしの表紙・章扉をテーマの色の図形で飾る置き方（graphic）と、項目に添える線のアイコン・結論の1行の帯（takeaway）も持つ（一覧画像を catalog で出せる）。画像の枠に「何を描くか」だけを書けば、Codex（ChatGPT のサブスク枠の gpt-image-2）で描いて埋める（写真風・イラスト・手描きを --image-look で資料全体に・ページのキャラ _characters も描き込む）。手持ちの写真は path で差し込み、描いた絵は1枚ずつ微修正・作り直しできる。クリックで要素が順に出る・モーフで画面がつながる・ノートに話す台本が入った PPTX にする。文字も図形も PowerPoint の部品なので、あとから直せる。PPTX と一緒に PDF（文字を選べる）・ページごとの画像（PNG）・全ページのサムネイル一覧も出し、--format で画像だけにもできる（週報・月報に貼る用）。スタイルは standard（説明・報告向け）と visual（ビジュアル寄り・ピッチ向け）から選べ、カスタムの YAML も渡せる。決まりに沿った HTML なら何でも PPTX にでき、html-video が焼いた解説動画の HTML も PPTX にできる。「スライドを作って」「パワポにして」「ヒーロー画像の表紙」「資料を作って」「稟議の資料」「提案書」「動くパワポ」「編集できるスライド」「提案資料」「html-slide」「html-pptx（旧名）」「動画をパワポにして」「叩き台を作って」「この写真に差し替えて」「この絵を直して」「スライドを画像で」「PDF も」「サムネイルで見たい」等で発動。※同じ台本を画像生成で1枚丸ごと描くなら image-slide、解説動画は html-video の領分。
---

# html-slide — 編集できて動く PPTX

台本（資料用の JSON）→ 型が HTML を組む → Chrome で位置を読む → python-pptx で PPTX。
**文字・図形は PowerPoint の部品**（画像に焼かない）で、アニメーションは PowerPoint の `<p:timing>` を直接書く。

> **ドキュメント**: 設計判断は [`docs/adr/`](docs/adr/)（0001 HTML の決まり・0002 字体・0003 名前・0004 ストーリーテンプレートを持たない・0005 スタイル・0006 テーマの色・0007 ロゴ・0008 ロゴの位置・0009 透かし・0010 handout は動きなし・0011 画像の枠を Codex で埋める・0012 出力は PPTX・PDF・ページの画像・サムネイル・0013 API で描く道は見送り・0014 描いた絵はキャッシュに置く・0015 置き場の根っこは ~/.quick-slide・0016 Node のモジュールと Chrome の在りか・0017 図形の表紙・0018 少ない中身は大きく・0019 図解の型とアイコンと締めの帯・0020 DADS の基礎・0021 型の構造の直しと3つの型・0022 余白のあるページは大きく組む・0023 モーフをやめる）、HTML の決まりは [`references/contract.md`](references/contract.md)、台本の仕様と画面の型は [`references/deck-format.md`](references/deck-format.md)、画像の枠の埋め方は [`references/images.md`](references/images.md)、部品 JSON は [`references/parts-format.md`](references/parts-format.md)、機体に置く手順は [`references/deploy.md`](references/deploy.md)。
> **過去に受けた指摘** → [`FEEDBACK.md`](FEEDBACK.md)。**着手前に読む。**
> **テスト**: `bash tests/run.sh`
> **出荷検品**: [`INSPECTION.md`](INSPECTION.md)（画面型）。検品役が読むのは `<出力先>/inspect.md`。

## いつ使うか

| 状況 | 入口 |
|---|---|
| どの型・置き方を使うかを見て選ぶ（一覧画像） | `catalog`（`--only image` で画像のものだけ） |
| 画像の枠の一覧を出す（出どころ: 持ち込み・置いた絵・生成） | `images` |
| まだ絵の無い生成の枠を Codex で描く（deck も既定で先にやる） | `fill` |
| 描いた絵1枚を指示で直す（「人を一人足して」「夜の景色に」） | `revise --page N [--slot 枠] --note "…"`（既定は微修正＝今の絵を土台に・`--remake` で作り直し） |
| 資料を作る（台本を書いて PPTX・PDF・ページの画像・サムネイルまで） | `deck` |
| 週報・月報に貼る画像だけ欲しい／ざっと一覧で見たい | `deck --format png` ／ `deck --format thumbs` |
| 台本を書いた・HTML を書いた（検査だけ） | `check` |
| 決まりに沿った HTML が手元にある | `render` |
| html-video が焼いた解説動画を PPTX にする | `from-video` |
| できた PPTX の、クリックとノートの照合 | `verify` |

すべて `python3 <このスキルのフォルダ>/scripts/html_slide.py <入口> …`（引数は `--help`）。
終了コード: 0=成功 / 1=実行時エラー（描けなかった画像の枠がある・deck は仮の枠で組む）/ 2=台本・HTML の誤り（決まり違反・はみ出し・照合のずれ）/ 4=依存不足・Codex の認証。

## Workflow: deck（資料を作る）

1. **ストーリー（何をどの順で言うか・枚数）は呼ぶ側が決める。** このスキルはストーリーの型を持たない（[ADR 0004](docs/adr/0004-no-story-templates.md)）。決まった流れが要る用途（社内の稟議・定例など）は、呼ぶ側のスキルが流れを決めて検品をかける
2. [`references/deck-format.md`](references/deck-format.md) の「書く前に」の10を読み、台本を書く。見本は [`examples/sample-deck.json`](examples/sample-deck.json)
   - **見出しは主張で書く**（「現状」ではなく「会議の4割は決めずに終わっている」）
   - 1枚1メッセージ。話の筋は 結論 → 課題 → 原因 → 解決 → 効果 → 次の一手 が基本
   - `talk`（話す台本）は、1つ目が画面に入ったとき、2つ目以降が各クリックのとき。数が型の段の数と合わないと rc=2
   - 配る資料・資料だけ作りたいなら `mode: handout`（`--mode handout` でも可）。動きも切り替えも付けない
   - **画像は台本を書く側が判断して置く**（[ADR 0011](docs/adr/0011-fill-images-with-codex.md)）。構造・手順・数字は図解（型）、空気・場面・気持ちは `image: {prompt: "何を描くか"}`、
     実在のもの（自社の製品・特定の人・「あの資料のあの写真」）は `path` で持ち込み。prompt に画風は書かない（`--image-look` かスタイルが足す）。
     キャラはページに `_characters`（image-slide と同じ書き方）。決まりは deck-format.md の「絵の出どころ」
3. `python3 …/html_slide.py deck --script <台本.json> [--out <フォルダ>] [--style standard|visual|<YAML>] [--theme default|forest|warm|<YAML>] [--logo <PNG> [--logo-position bottom-right|bottom-left|top-right]] [--watermark <文字>] [--image-look photo|illustration|handdrawn] [--no-fill] [--format pptx,pdf,png,thumbs]`
   - **まだ絵の無い生成の枠は、先に Codex で描いて埋める**（認証は1度・並列8本・1枚1〜2分）。描けなかった枠は仮の枠で組んで rc=1（もう一度流すとその枠だけ描く）。
     急ぐ下書きは `--no-fill`（仮の枠）。Codex の認証が切れていれば rc=4（`codex login`）
   - **画風**は `--image-look`（省けばスタイル: standard は illustration・visual は photo）。変えると生成の枠だけ描き直す。前の画風の絵は残る
   - **ロゴ**は既定で右下（高さ 44px まで・引き伸ばさない・右下が写真なら左下・両方ふさがれば置かない）。`--logo-position` で左下・右上も選べる（右上がふさがれば右下へ）。決まりは deck-format.md の「ロゴ」
   - **透かし**は `--watermark 社外秘` のように渡すと、全ページの中央下の端に薄いグレーで入る（PPTX では選べない・動かせない部品）。決まりは deck-format.md の「透かし」
   - **スタイル**は資料全体の見せ方の既定（[ADR 0005](docs/adr/0005-styles-standard-visual.md)）。`standard`（既定・説明・報告・社内資料向け）／`visual`（ビジュアル寄り・プレゼン・ピッチ向け。画像のある表紙・章扉は全面写真など）／カスタムの YAML（雛形は `templates/custom-style.example.yaml`）。台本に変化形を書いたページは台本が勝つ
   - rc=2 なら stderr の `NG:` を読んで台本を直す（文字のはみ出しは文を短く。字を小さくしない）
   - `警告:`（仮の画像の枠・キャラを描く枠が無い・visual で表紙に画像が無いなど）は rc を変えない。読んで直すかを決める
4. 出力（`<出力先>/<台本の名前>/`・[ADR 0012](docs/adr/0012-outputs.md)）。`--format` で選び、省けば4つとも（image-slide と同じ口）:

   | `--format` | ファイル | 中身 |
   |---|---|---|
   | `pptx` | `deck.pptx`（＋`build.json`・`inspect.md`） | 編集できて動く PPTX |
   | `pdf` | `deck.pdf` | `deck.html` を Chrome で印刷（文字は選べる・PPTX と同じ 13.333×7.5 インチ・写真は JPEG にして軽く） |
   | `png` | `slides/slide-NN.png` | ページの画像（1920×1080・ロゴ・フッタ・透かし込み・クリックで出るものは全部出た形）。そのまま渡せる |
   | `thumbs` | `thumbs.png`（17ページ以上は `thumbs-01.png` …） | 全ページを縮めて「番号 見出し」を添えた一覧（4列） |

   - いつも出るもの: `deck.html`・`deck-info.json`（`outputs` に作ったものの一覧）。描いた絵はキャッシュの区画（`~/Library/Caches/quick-slide/html-slide/<台本のフォルダ名>-<ハッシュ>/gen-<鍵>.png`・直す前の絵は `history/`・場所は `deck-info.json` の `cache`）。`assets/` にはハードリンクで張る。合計が `cache_max_mb`（既定 1024 MB）を超えると古く使った区画から消える。残したい絵は `images/s{NN}-{枠}.png` に写す（[ADR 0014](docs/adr/0014-cache.md)）
   - PDF と画像は HTML から作るので、PowerPoint で開いた PPTX とは改行の位置がわずかに違うことがある
   - 作れなかった出力（Chrome が動かないなど）があれば rc=1
5. ページの画像かサムネイルを見て、崩れていないかを確かめる
   - **クリップボードには入れない**（エンジンは無人でも動く・入るのは1枚だけ。ADR 0012）。会話の中で「3枚目をクリップボードに」と頼まれたら、呼ぶ側が
     `osascript -e 'set the clipboard to (read (POSIX file "<出力先>/slides/slide-03.png") as «class PNGf»)'`
6. 人に渡す資料なら、検品する側に出力先の `inspect.md`（と `slides/` の画像）を渡して確かめてもらう（基準はこのスキルの [`INSPECTION.md`](INSPECTION.md)）
7. **PowerPoint で動くかは人が見る**（自動では確かめない。下の「制約」）

## Workflow: from-video（解説動画 → PPTX）

`python3 …/html_slide.py from-video --video-dir <html-video の出力>/<preset>` — `project/index.html`・`build.json` と台本（`--script`）から、1文 = 1クリックの PPTX を作る。ノートには字幕の文が入る。html-video は改修しない（[ADR 0001](docs/adr/0001-html-contract.md) の互換の経路）。

## 構成

| パス | 中身 |
|---|---|
| `scripts/html_slide.py` | 入口（catalog / images / fill / revise / check / deck / render / from-video / verify） |
| `scripts/imagegen.py`・`scripts/codex_run.py` | 生成の枠の描く指示・鍵・画風・キャラ ／ Codex の認証と並列の生成（image-slide の写し） |
| `scripts/deck.py`・`scripts/deck_types.py` | 台本 → HTML（画面の型23種と画像の置き方）・雛形・画像の枠の一覧 |
| `scripts/deck_style.py`・`styles/` | スタイル（standard・visual・カスタム）の読み込みと検査・既定の当て方 |
| `scripts/catalog.py` | 型と置き方の一覧画像（65通り・画像を使うもの32通り） |
| `scripts/extract.mjs` | Chrome で HTML を開き、位置・見た目・動きの属性を読む。決まり違反とはみ出しを集める |
| `scripts/pptx_writer.py` | 読んだ部品 → PPTX（図形・文字・画像・SVG・アニメーション・ノート） |
| `scripts/from_video.py` | html-video の HTML（GSAP の時刻）→ 部品 |
| `scripts/verify.py` | PPTX を開き直して、クリックとノートの照合・LibreOffice での読み取りの照合 |
| `scripts/snapshot.mjs` | ページの画像を撮る・PDF に印刷する |
| `scripts/browser.py` | Node・puppeteer-core・Chrome の在りか（ADR 0016。`python3 scripts/browser.py` で JSON） |
| `scripts/outputs.py` | `--format` の読み方・サムネイル（image-slide と同じファイル） |
| `templates/`・`themes/` | 資料の CSS と字体の読み込み ／ 色 |
| `examples/` | 見本の台本（`sample-deck.json` 全型・`image-deck.json` 画像の型と置き方。どちらも架空の題材。`images/s01-image.jpg` は Codex（gpt-image-2）で描いた本物） |

## 制約・落とし穴

- **PowerPoint の GUI を自動操作しない。** AppleScript で書き出させたら、PowerPoint がダイアログで止まって応答しなくなった（2026-10-03）。動作の最終確認は人が開いて見る
- **字体は Noto Sans JP（DADS）で測る**（[ADR 0020](docs/adr/0020-dads-foundations.md)。游ゴシックから替えた経緯は [ADR 0002](docs/adr/0002-font-yu-gothic.md)）。設定の `fonts` に Noto Sans JP の regular・bold のパスを書く（`config.example.yaml`）。分からなければ PowerPoint 同梱の游ゴシックで組み、`deck-info.json` の `font_note` に残す
- 許可リストの外の CSS（`transform`・`filter`・`clip-path`・`::before` の中身・背景画像）は `check` が止める。型を足すときもこの範囲で書く
- SVG の曲線は折れ線に近似する（見た目は同じ。PowerPoint で曲線として直すのは難しい）
- Node のモジュール（puppeteer-core）と Chrome（headless shell）は `scripts/browser.py` が探す（環境変数 → 設定 → Quick Slide の置き場 `~/.quick-slide/engines/html-slide/` → 前からの置き場 `~/.cache/…`・[ADR 0016](docs/adr/0016-node-and-chrome-places.md)）。足りなければ描く前に rc=4。入れ方は [`references/deploy.md`](references/deploy.md)
