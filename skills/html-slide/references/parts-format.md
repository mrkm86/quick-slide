# 部品 JSON（extract.mjs → pptx_writer.py の間の形）

`scripts/extract.mjs` が HTML から書き出し、`scripts/from_video.py` が段を足し、`scripts/pptx_writer.py` が読む。
座標はすべて **スライドの左上が原点の CSS px**（1920×1080）。色は `{"hex": "2f63b8", "a": 1}`（a は 0〜1）。

```json
{
  "version": 1, "mode": "contract | hyperframes", "source": "/abs/path/index.html",
  "size": {"w": 1920, "h": 1080}, "title": "…",
  "slides": [{
    "id": "s1", "transition": "fade | none", "notes": "…［クリック］…",
    "background": {"type": "solid", "color": {…}} | {"type": "grad", "angle": 160, "stops": [{"color": {…}, "pos": 0}, …]},
    "prims": [ … 部品（重なり順・奥から手前） … ],
    "units": {"u1": {"step": 1, "fx": "pop", "delay": 0, "keep": null, "name": "card-a"}},
    "steps": 2, "notes_clicks": 2
  }],
  "violations": [{"slide": 2, "el": "div#tilt.tilt 「…」", "rule": "transform", "detail": "…", "level": "error | warn"}]
}
```

## 単位（units）

部品の `unit` が指す。**1つの単位 = PowerPoint の1つのグループ**（重なり順で続く区間ごと）。

| キー | 中身 |
|---|---|
| `step` | `null` = 最初から出ている / `0` = 画面に入ったら自動 / `N` = N 段目のクリック |
| `fx` | `rise` / `fade` / `pop` / `wheel` / `wipe-right` / `wipe-left` / `wipe-up` / `wipe-down`（wipe の向きは「描く向き」） |
| `delay` | 同じ段の中の遅れ（ms） |
| `keep` | 図形名を `!!keep` にする（画面をまたいで同じ部品だと分かる名前） |
| `name` | 図形名（keep が無いとき） |

contract では `data-step` を持つ一番外側の要素、無ければ `data-keep`・`data-name` を持つ一番近い要素が単位になる。
hyperframes では GSAP で出る要素の id が単位で、`t`（出る時刻・秒）と `kind`（`pop`・`rise`・`slide`・`grow`・`fade`・`mark`）、`parent`（外側で出る単位）が付く。トップに `canvas{w,h}`（動画の大きさ。1920×1080 以外は from_video が rc=2）。
`step`・`fx`・`delay` は `from_video.convert` が台本から入れる（印は `mark` に台本の種類も残す）。

## 部品（prims）

共通: `type`・`unit`（無ければ `null`）・`name`（要素の `data-name` か `id`。無ければ `null`）。

| type | キー |
|---|---|
| `rect` | `rect{x,y,w,h}`・`rx`（角丸 px）・`fill`・`grad{angle, stops}`・`stroke`・`sw`（線の太さ px）・`dash`（`dash`/`dot`）・`inset`（true = CSS の枠線で、線を箱の内側に描く）・`shadow{dx,dy,blur,color}` |
| `ellipse` | `rect`・`fill`・`stroke`・`sw`・`dash` |
| `path` | `subs[{pts:[[x,y],…], closed}]`・`fill`（閉じた部分だけ塗る）・`stroke`・`sw`・`cap`・`join`・`dash` |
| `text` | `rect`・`runs`・`lines`（行数）・`align`（`l`/`ctr`/`r`/`just`）・`lh`（行送り px）・`rot`（度）。複数行は `rect` = 中身の箱の幅 × 行送り×行数、`ca`（1行目の文字の高さ）・`box`（中身の箱）も付く。`pin: true`（`word-break: auto-phrase`・`keep-all`・`text-wrap: balance`・`pretty`）はブラウザの改行の位置を a:br で固定する |
| `img` | `rect`（見えている範囲）・`src`（絶対パスか data URI）・`crop{l,t,r,b}`（0〜1・小数4桁。2桁では全面の絵が約1%伸びる）・`alt`（PPTX の代替テキスト `descr`）・`radius`・`shadow`・`op` |

`runs` の1つ: `{text, color, size（px）, bold, italic, underline, strike, font（字体名）, spc（字間 px）, line（ブラウザで何行目か）}`。強制改行は `{"br": true}`。
不透明度（祖先の `opacity` も）は色の `a` に掛けてある（画像だけは `op`）。透明な文字（`color: transparent`・`visibility: hidden`）は run に出さない。

## 決まり違反（violations）

`level: "error"` が1つでもあれば `check` は rc=2。`slide` は 1 から（0 は HTML 全体の話）。section 自身も検査する（`el` は `section#id`）。
部品の重なり順は、兄弟の中で z-index が負 → 位置指定なし → 位置指定あり（auto・0）→ z-index が正 の順（祖先をまたぐ重ね順は再現しない）。

| rule | 中身 |
|---|---|
| `transform`・`filter`・`backdrop-filter`・`clip-path`・`mask`・`background-image`・`pseudo-content`・`writing-mode` | 許可リストの外の CSS |
| `text-overflow` | 文字が箱からはみ出す（いちばん内側の箱だけ） |
| `outside-slide` | section の外にはみ出す（祖先がもう出ていれば出さない） |
| `notes-clicks` | 段の数とノートの［クリック］の数が合わない |
| `bad-step`・`bad-fx`・`bad-delay`・`bad-transition`・`slide-size`・`no-slide`・`inline-step` | 属性・大きさの誤り（`inline-step` は文字の中の inline 要素の data-step） |
| `order` | flex・grid の `order`（重なり順が HTML の順とずれる） |
| `z-index-context` | 重ね順の文脈（stacking context）を作らない親の中の `z-index`（祖先をまたぐ重ね順は写せない） |
| `unsupported-element`・`unsupported-svg`・`external-image`・`image-load`・`image-format` | 写せない要素・読めない画像 |
| 警告: `nested-step`・`empty-step`・`fx-without-step`・`svg-paint`・`font-load`・`notes-ascii-click` | 止めないが、見落とすと崩れるもの |
