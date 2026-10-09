# 0020 基礎をデジタル庁デザインシステム（DADS）のスライド用ローカルデザインシステムに替える

- 日付: 2026-10-10
- 状態: 採用

## 決めたこと

仕様の正本は `skills/dads/references/slide-local.md`、値の正本は `skills/dads/tokens/slide.json`。エンジンの定数
（`deck_types.py` の `SCALE`・`LINE_HEIGHTS`・`SPACE`・`COL`・`GUTTER`・`RADII`・`ELEVATION_2`、`deck.py` の `INK` ほか）は同じ値で、試験（`dads_tokens`）が見比べる。
型の構造（新しい型・札の追加・線の形・DOM の順など）はこの ADR では変えない。

1. **色**: `deck.py` の `palette()` を作り直した。テーマの `primary`・`accent` それぞれを、DADS のキーカラーの作り方
   （`skills/dads/scripts/keycolors.py`）で役に分ける。パスは `Path(__file__).resolve()` から辿る（エンジンは symlink 経由でも呼ばれる）
   - テーマの色が DADS のプリミティブの段そのまま（`#006f83` = cyan-900 など）なら、その色相の段（`ramp_key_colors`）。そうでなければ、同じ色相のまま明度だけを動かす（`brand_key_colors`）
   - 役（前 → 後）:

     | 前 | 後 |
     |---|---|
     | `primary`（面にそのまま使う生色） | `primary`＝`primary-text`＝文字にも使う色（地・surface・淡い地の全部の上で 4.5:1） |
     | `primary`（棒・矢印・印・線・stmt-bar・sum-bar・card-bar・li-mark） | `primary-mark`（地・surface・淡い地の上で 3:1） |
     | `primary`（上の帯・表紙の飾り・題の下線） | `primary-decor`（セカンダリー。純粋な飾りの面だけ） |
     | `primary-strong` | 同じ名前（白い文字を載せる面。4.5:1 を満たす `key`、足りなければターシャリー）。アイコンの色は `primary-text`（4.5:1） |
     | `tint` / `primary-soft` | 同じ値（その色相の 50 の段） |
     | `line`（`ink` の 14%・1.27:1） | `rule`（Gray-536 `#767676`・罫・枠・軸。どの地の上でも 3:1） |
     | （無し） | `rule-weak`（Gray-420 `#949494`。**白の地の上だけ** 3:1。白でなければ `rule` と同じ）。グラフの目盛り線に使う |
     | `bar`（`ink` の 24%・1.53:1） | `bar`＝`rule-weak`（非強調の棒・3:1） |
     | `muted`（`ink` の 72%） | Gray-600 `#666666`（淡い地の上で足りなければ濃くする） |
     | 透かし `#9ca3af`（2.54:1） | `faint`＝Gray-536（白の地で 4.54:1。白でなければ濃くする） |
     | `shadow` | 消した（影の色の役は無い） |
     | （無し） | 意味の色 `success-1/2`・`error-1/2`・`warning-yellow-1/2`・`warning-orange-1/2`（DADS の値。白い面の上だけ） |

   - `accent` も同じ規則（`accent-mark`・`accent-decor`・`accent-strong`・`accent-soft`）。書かなければ `primary` と同じ
   - **テーマ**: `default` はシアン（`#006f83`）、`forest` は緑（green-800 `#197a4b`）、`warm` は橙（orange-900 `#ac3e00`・生成りの地）。
     `ink` は Gray-800 `#333333`・`muted` は Gray-600 `#666666`。YAML のキー（`background`・`ink`・`primary`・`accent`・`muted`・`surface`・`font`・`font_files`）は変えない。
     ブランドの色は、同じ色相で明度だけが動く（Quick Slide が渡す `#0db4f2` は `#0077a1` になる）。image-slide のテーマ YAML は、試験が見比べるので同じ値にした
2. **検査の閾値**: `contrast_problems()` は、文字（`ink`・`muted`・`primary-text`・`accent-text`）を 地・surface・淡い地・強調の淡い地 の全部で **4.5**
   （DADS は大きな文字の緩和を使わない）。罫・枠 `rule` を 3.0（全部の地）・`rule-weak`・`bar` を白の地で 3.0・`primary-mark`・`accent-mark` を 3.0・
   文字を載せる面と地を 3.0。`faint` は地で 4.5。`overlay_problems()` は、写真の上の白い文字と強調の色を **4.5**（前は強調を 3.0）。
   幕の濃さは `SCRIM_ALPHA` 0.80 → 0.86・`QUOTE_ALPHA` 0.72 → 0.86・`HERO_VEIL` 0.88 → 0.94（3つのテーマで 4.5 を満たす最小に近い値）
3. **文字**:
   - 字体の既定は **Noto Sans JP**（`themes/*.yaml` の `font`・`deck.py` の `DEFAULT_FONT`）。設定の `fonts["Noto Sans JP"]` に regular・bold のパスを書く
     （`config.example.yaml` に書き方の例）。**ファイルが分からなければ（設定に無い・ファイルが無い）游ゴシックに落ち**、`deck-info.json` の `font_note` に理由を残し、
     入口（`html_slide.py`）が「注:」と出す。**警告にはしない**（字体ファイルの無い環境でも試験が通り、警告 0 の見本が崩れないため）。ほかの字体はファイルが分からなければ rc=2（従来どおり）
   - 大きさは DADS の段だけ: 64・57・48・45・36・32・28・26・24・22・20・16。**拡張**として 80・96・120・160・200（題・数字・主張だけ）
   - `fit()` は連続値（2px 刻み）で縮めず、**段を1つずつ下りる**（下限は `min_size` と `MIN_PX`＝22 の大きいほう。入らなければ rc=2）。`tidy` も次の段（14% 以内）で見る。
     grow（ADR 0018）は倍率（1.25・1.15）でなく**段を上る**（`GROW_STEPS = (2, 1)`・役ごとの上限は `Slide.g(v, hi)`・間隔は `Slide.gap()` で 8 の倍数のまま）。
     並ぶ項目の題と本文は、本文が題より大きくならない（DADS: 箱の題は常に本文以上）。`kpi`・`annotate` の「全部を同じ割合で縮める」は、同じだけ段を下りる形にした
   - 役ごとの既定（前 → 後）: 見出し 60/1.35 → 64B-140（下限 45）・図解の大見出し 84 → 80（下限 64）・キッカー 28/30 → 28B-150・副題 30 → 32N-150・
     表紙の題 88/1.3 → 96B-140（下限 64）・章題 76/74 → 80（写真つきは 64）・主張 76 → 96（下限 64）・大きな数字 240 → 200（下限 96・行高 1.0）・章の番号 200（行高 1.0）・
     箱の題 44/38 → 45/36・箱の本文 32/28・注記・出典 22・表のセル 28N-150（下限 24）・札 28B・体制の頭文字 67 → 64（丸 160 の 0.4）ほか（型ごとの値は `deck_types.py`）
   - 行高は 1.0・1.2・1.3・1.4・1.5・1.6・1.7・1.75 の段だけ（`fit()` が段でない行高を assert で止める）。48 以上は 1.4・本文は 1.5〜1.6・数字は 1.0
   - 文字間隔（`letter-spacing`）は付けない（PPTX に写せないため。幅は PIL で測る）
   - **字体の上下の幅**: Noto Sans JP は ascent＋descent が 1.448em あり、行高 1.0〜1.4 の箱では文字の領域が箱からはみ出して見え（`scrollHeight`）、
     描き出し係の「文字のはみ出し」の検査が止まる。`@font-face` の `ascent-override`・`descent-override` で領域を 1em に縮め、**ベースラインは動かさない**
     （`(ascent − descent) / 2` を保つ）。見た目は変わらない
   - 下の文字（footer）は 22px・1 行。入らなければ 20px（DADS の段）まで下りる。それでも入らなければ、透かしがあるとき rc=2（透かしの帯 1048px〜にかかるため）・無ければ警告。
     ページ番号を出すと幅が 792px になる（中央下の番号の手前まで）
4. **余白と格子**: 余白は 8 の倍数（雛形の 4・6・10・12・14・18・19・20・22・26・28・34・36・44・52・68 を直した。試験が雛形の余白を見る）。段は 8・16・24・40・64、見出しと中身の間 `BODY_GAP` 56 → 40、
   中身の下端 `BODY_BOTTOM` 950 → 952、下の文字 1006 → 1008。格子 12 列（列 96・溝 48）を `COLS`・`COL`・`GUTTER`・`span(n)`・`col_x(i)` に持ち、
   カード・指標・要約・比較・ギャラリー・体制・コラージュ・写真つきの箇条書き・グラフの右の要点（4 列 528）・注釈の写真（8 列 1104）・締めの連絡先で使った。
   溝 48 は格子専用（箱と箱の間）。文字の下の余白の段には 48 を使わない。矢羽根・比較の矢印の列・年表など、格子に載せない所は 8 の倍数だけ守る
5. **角・影**: 角は 0・8・12・16・24・32・full。カード 16・写真 8・箱の中の札 8・番号と丸は full（`9999px`）。`take`（44）・`mk-dot`（28）・`li-no`（34）・`tl-dot`・`mx-dot`・`cmp-dot`・
   アイコンの丸・顔の丸・放射の中心・凡例の見本（`{d/2}px`）を full に替えた。PPTX には `roundRect` の調整値が 0.5 に頭打ちされて写る（描き出しで確かめた）。
   影は `elevation-2` の値だけ（`.mk-dot`）。**箱（カード・比較の列・指標・要点・放射の中心・対）は外周の枠を持つ**（`rule` の 2px・強める箱は差し色の 4px）。
   地と面の色の差（1.12:1）だけで境界を作らない
6. **退役した look**: `look.surface: white`（白に影 `0 8px 28px`）は、受け付けるが **`surface: line`（白に枠線）と同じに描く**。影は出さない。`styles/visual.yaml` は `line` に替えた。
   `look.radius: square` は 4・`round` は 32 のまま（写真は 8。`square` のときだけ 4）
7. **表紙・章扉の飾り**（`deck_art.py`）の色は `primary-decor`・`accent-decor`（飾りの面）。図形は飾りで、情報を持たせない

## 理由

- DADS はコントラストを「大きな文字でも 4.5」「図形・罫は 3」と決めている。今のテーマは面にそのまま使う色（`#0db4f2` が白に 2.38:1）・薄い罫（1.27:1）・
  薄い棒（1.53:1）・暗幕上の文字を 3.0 で通していて、点検（2026-10-10）で 20 近い型が基準を割った
- 文字の大きさが 2px 刻みで縮み、段の外の大きさ（60・44・38・34・30・42 …）が混ざり、同じ資料の中で隣り合う項目の大きさが揃わなかった。DADS は段だけを使う
- 見た目の根拠を1か所（`slide.json`）にそろえ、html-slide・motion-slide・画面が同じ値を読めるようにする（計画 `docs/DADS-PLAN.md`）

## 見送ったこと

- 文字間隔（`letter-spacing`）: PPTX に写らない。DADS の値（36 で 1%・26 以下で 2%）は付けない
- 暗い地のテーマ: DADS に作例が無い。`keycolors.py --ground` は作れるが、影・暗幕の決まりが未定
- `look` の新しい値（影つきなど）: 影は elevation-2 の番号の丸だけにした
- 淡い地の上の意味の色: 黄・橙の `-1`・`-2` が淡い地の上で 3:1・4.5:1 を割る（slide-local.md の 4-3）。意味の色の箱は白い面の上にだけ置く（型は次の担当が足す）
- 色だけで区別している型（折れ線の線・matrix の強調・timeline のいま・表の強調列・cards/kpi/compare の強調）の**形・札・語**での補い: 型の構造の変更なので次の担当
- 巻き込んだ別の場所: `src/quick_slide/render/look.py` の既定の字体（html-slide は Noto Sans JP・image-slide は游ゴシックのまま）、`skills/image-slide` のテーマの値（試験が見比べる）と
  `layout.py` の透かしの色（`#767676`。html-slide の `WATERMARK_COLOR` と試験が見比べる）

## 残る懸念

- `skills/dads/scripts/keycolors.py` を実行時に import する。`skills/html-slide` だけを配ると動かない（`skills/dads` も同じ階層に置く。配布の手順は `references/deploy.md`）
- 見本の台本（`examples/image-deck.json`）の footer は 22px では 1 行に入らなかったので短くした（下の文字の幅が 792px のため）
- Noto Sans JP のファイルが無い機体では游ゴシックで測る。幅が少し違い、描き出しの Chrome の検査が最終の判定
