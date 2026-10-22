# 受け口 — 台本の読み方と、描く指示の組み方

## 台本は html-slide と同じ

台本の書き方は html-slide の [`deck-format.md`](../../html-slide/references/deck-format.md) が正本。image-slide は**同じ JSON をそのまま読む**（[ADR 0001](../docs/adr/0001-same-intake-as-html-slide.md)）。

| 台本のキー | image-slide での扱い |
|---|---|
| `title` | 資料の名前。描く指示の「資料全体」と PPTX の題 |
| `theme` | 色（`--theme` が優先）。テーマの `background`・`ink`・`primary`（主役の色）と、あれば `accent`（差し色。面積を絞って要所だけと添える）・`muted`・`surface` を描く指示に入れる |
| `footer` | 下端の帯の左に PowerPoint の文字として置く（帯のある型だけ） |
| `page_numbers` | 下端の帯の中央にスライド番号のフィールドとして置く（帯のある型だけ・既定 false。html-slide と同じ） |
| `mode` | 読むが使わない（絵にはクリックが無い） |
| `outline`・`slides[].role` | 読み飛ばす（ストーリーテンプレートはやめた・html-slide の ADR 0004） |
| `slides[].type` | 型の描き方の言葉（`scripts/prompt.py` の `PATTERNS`）を選ぶ |
| `slides[].talk` | ノート。全部を改行でつなぐ（クリックが無いので、数は照合しない） |
| `slides[].transition`・`steps`・`fx`・`id` | 読むが使わない |
| `slides[]` のそのほかのキー | 「描く文字」として、そのまま描く指示に入れる |
| `slides[]` の配置の指定（型ごと: `layout`・`split`・`side`・`text_side`・`tone`・`fit`・`widths`・`align`・`min`・`max`・`value_labels`・`arrow`） | 「描く文字」から外し、**配置の指定**として別に渡す（鍵にも入る）。外すのは一番外だけで、並びの中の `role`（体制の肩書き）や `big-number` の `side`（脇の数字）は中身として描く（`script_io.LAYOUT_KEYS`） |
| `photo` の `hide_text: true`（`layout: full` だけ） | 画像だけの扉絵。`kicker`・`headline`・`body` を「描く文字」から外し、文字・幕を描かせない（`caption` だけ出典として右下に小さく）。`headline` はページの名前として文脈の一覧と代替テキストに使う（[ADR 0018](../docs/adr/0018-image-only-photo.md)） |
| `slides[]._layout` | image-slide だけが読む、持ち込みのレイアウトの名前（`--layout` で渡したもの）。`{name, parts}` で部品の文字も渡せる（[ADR 0006](../docs/adr/0006-bring-your-own-layout.md)） |
| `slides[]._visual` | image-slide だけが読む、そのページの描き方の注文。html-slide は `_` で始まるキーを読み飛ばす |
| `slides[]._characters` | image-slide だけが読む、そのページに出すキャラ `[{name, image, pose}]`。並べた順が参照画像の順。`name`・`image` は必須で、絵が無い・知らないキーは rc=2（[ADR 0011](../docs/adr/0011-characters-per-page.md)） |

- **止めるのは「何を描くかが分からない」ときだけ**: 知らない型・知らないキー・必須のキー（`headline` など）が無い・画像の枠にファイルも `prompt` も無い。どれも rc=2
- html-slide で止まる文字の長さ・talk の数・画像が無いことでは止めない。ただし**知らないキーは止める**（片方のエンジンだけで通る台本を作らないため）
- 一番外にエンジン固有のキー（ロゴなど）を書くと、html-slide で止まる。だから引数で渡す（`--logo`・`--ref`・`--style`）。ページごとのキャラは `_characters`（`_` のキーは html-slide が読み飛ばす）

### 帯のある型・無い型

html-slide の型の定義と同じ: `cover`・`section`・`photo` と、`quote` の `layout: full` は帯（フッタ・ページ番号）を出さない。
帯を空けさせるのは、帯のある型で、ロゴ・フッタ・ページ番号のどれかを載せるページだけ。帯の無い型には、ロゴを渡しても帯を頼まない。
全面に描かせるスタイル（visual・`bleed: true`）では、本文のページにも帯を頼まず、下端 7% に文字や図だけ置かせない（[ADR 0009](../docs/adr/0009-visual-full-bleed.md)）。
描いた後に、`--logo-position` の順（既定は右下 → 左下）に周りが1色の所を探してロゴを置き、どこもだめなら置かない（[ADR 0008](../docs/adr/0008-logo-same-rules-as-html-slide.md)）。

### 画像の枠

| 枠の状態 | 描き方 |
|---|---|
| ファイルがある（`path`、または `<台本のフォルダ>/images/s{NN}-{枠の名前}.png`） | **参照画像として Codex に添える**。描く文字の中では「添付の参照画像1の内容をこの位置に描く」 |
| ファイルが無く `prompt` がある | 描く文字の中で「この位置に描く絵: {prompt}」 |
| どちらも無い | rc=2 |

置き場の規則は html-slide と同じ（`html_slide.py images` の一覧がそのまま使える）。
参照画像は**描き直しの手がかり**であって、貼り込むのではない（写真そのものを見せたいなら html-slide）。

## 描く指示の組み方（`scripts/prompt.py`）

上から順に:

1. **製品の譲れない条項** — 1枚だけ描く・文字は一字一句そのまま・原稿に無い数字や主張を足さない・数値は計算し直さない・端から 6% 内側に収める・ページ番号やロゴは描かない・指示文そのものを描かない（quick-slide の `MANDATORY_RECIPE` の考え方）
2. 最終のキャンバス（16:9）
3. **帯の条項**（帯を使うページだけ）— 下端 7% を1色の無地で空ける（[ADR 0003](../docs/adr/0003-band-parts-on-top.md)）
4. スタイル（`--style` の standard・visual・カスタムの文章＋`--style-note`）とテーマの色
5. **資料全体の文脈** — 題・全何枚・このページは何枚目・全ページの見出し。全ページで調子をそろえるため
6. このページの型（中身の形・`prompt.TYPE_NOTES`）
7. **構図** — `_layout` のあるページは持ち込みのレイアウト（構図の文章・部品の文字・作風・構図の見本の番号）、無いページは指定しない。「このページのコンテンツが一番印象に残り、一番よく伝わる構図を自由に決める」とだけ言う（`prompt.COMPOSITION`・[ADR 0005](../docs/adr/0005-no-layout-layer.md)）
8. 描く文字（台本の中身を JSON で）とキーの意味
9. 配置の指定（あれば）
10. `_visual`（あれば）・参照画像の役割（あれば）。参照画像の並びは 画像の枠 → 構図の見本 → `_characters`（「参照画像N は Hana（ポーズ・表情: 考える）」と番号と名前を結ぶ）→ `--ref`。合わせて5枚まで。透過のある絵は白に合成して渡す（`codex_run._flat_refs`）

### 描き直すかどうか（使い回しの鍵）

鍵は**5. を除いた指示**と参照画像の中身のハッシュ（`prompt.page_key`）。キャッシュの区画の `slide-NN.json` の鍵と同じなら描き直さない（区画が上限で消えていれば描き直す・ADR 0015）。

- 5. を外すのは、**ほかのページを直した・1枚足しただけで全ページを描き直さない**ため（実際に、見出しを1つ直しただけで5枚とも描き直しになった）
- 描き方の言葉や条項を変えたら `PROMPT_VERSION` を上げる（全部描き直しになる）
- 中身は同じでも描き直したいときは `--redo`
