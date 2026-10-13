# HTML の決まり（描き出し係と型の間の約束）

html-slide の描き出し係は、**この決まりに沿った HTML だけ**を PPTX にする。型（資料の型・動画の型）は
この決まりに沿った HTML を出すだけで、PPTX のことは知らない。決めた理由は [`../docs/adr/0001-html-contract.md`](../docs/adr/0001-html-contract.md)。

## 1. 画面

| 決まり | 中身 |
|---|---|
| 1枚 | `<section data-slide>`。大きさは **1920×1080（CSS px）**。1つの HTML に何枚並べてもよい（上から順にスライドになる） |
| 座標 | section の左上が原点。1px = 6350 EMU（16:9 の 13.333in に写る）。文字の大きさは 1px = 0.5pt |
| 背景 | section の `background-color`（ベタ）。`linear-gradient` は2色までならグラデーションに写す |
| 切り替え | section の `data-transition="fade|none"`（既定 `fade`）。`morph` は退役した（ブラウザで再現できない）。書いてあれば fade として読み、警告する（ADR 0023） |
| ノート | section の中の `<aside class="notes">`（画面には出さない。`display:none` でよい）。中の文がそのまま PPTX のノートになる |

## 2. 動き（属性）

| 属性 | 付ける場所 | 意味 |
|---|---|---|
| `data-step="N"` | 出したい要素 | **N クリック目で出る**（N は 1 から）。`0` は「画面に入ったら自動で出る」。無ければ最初から出ている |
| `data-fx` | 同上 | 出方: `rise`（浮上・既定）／`fade`／`pop`（ズームして少し行き過ぎる）／`wipe-right`／`wipe-left`／`wipe-up`／`wipe-down`／`wheel`（時計回りに描く・丸の印向け） |
| `data-delay` | 同上 | 同じ段の中での遅れ（ms・既定 0）。同じ段は `data-delay` の小さい順に出る |
| `data-keep="名前"` | 画面をまたいで残したい要素 | 図形名を `!!名前` にする（画面をまたいで同じ部品だと分かる名前。動きは付かない） |
| `data-name="名前"` | 任意の要素 | 図形名（編集するときに選択ウィンドウで見える名前）。無ければ要素の `id` |

- **出す単位は、`data-step` を持つ要素とその中身全部。** 中の図形・文字・画像は1つのグループにまとめて動かす
- `data-step` の中に `data-step` を入れ子にしない（外側が優先され、内側は無視して警告）
- `data-step` はブロック要素に付ける（文字の中の `span` などの inline 要素に付けると、文字と一緒に出て別に出せないので rc=2）
- **段の数とノートの［クリック］の数を一致させる。** 段の数 = `data-step` に現れる 1 以上の値の種類の数。ノートの［クリック］がそれと違えば rc=2（話す台本と動きがずれるため）

**この効果と切り替えは、ブラウザの再生（Quick Slide の発表の画面）でも同じに出せるものだけ。** HTML で再現できない動き（変形・モーフなど）は使わない（ADR 0023）。

## 3. 見た目（許可リスト）

| 写すもの | 写し方 |
|---|---|
| 箱（`div` など） | `background-color` のベタ塗り（透明度あり）、一様な枠（`solid` / `dashed`・4辺同じ太さと色）、一様な角丸、`box-shadow`（外側の影1つに近似） |
| 文字 | 文字を直に持つ要素ごとにテキストボックス1つ。色・大きさ・太さ（600 以上は太字）・`text-align`・`line-height`。中の `em` / `strong` / `span` / `b` は色と太さを変えた run になる |
| 画像 | `img`（png / jpg / webp）。`object-fit: cover` は切り抜き、`contain` は縮小。角丸は図形の角丸にする |
| SVG | インラインの `svg` の中の `rect` / `circle` / `ellipse` / `line` / `polyline` / `polygon` / `path` / `text` / `tspan` / `g`。塗り・線・線の太さ・透明度。`path` の曲線は折れ線に近似する。1 つの `path` の中の複数の部分パス（`M`…`Z`）は 1 つの図形のまま写り、入れ子の部分パスは穴になる（viewBox の原点が負でもよい・アイコンの塗りの図形が使う。穴の向きは問わない＝塗りは even-odd） |
| 字体 | `font-family` の先頭の名前を、そのまま PPTX の字体名にする（`latin` と `ea` の両方）。計測に使う字体は `@font-face` で読ませる |

**止めるもの（`check` が rc=2）**: `transform`（`none` 以外）、`filter`、`backdrop-filter`、`clip-path`、`mask`、
背景画像（`url(...)`）、`::before` / `::after` の `content`、`writing-mode` の縦書き、
文字のはみ出し（要素の `scrollWidth` / `scrollHeight` が箱より大きい）、section の外にはみ出す要素、
flex・grid の `order`（0 以外。重なり順が HTML の順とずれる）、重ね順の文脈（stacking context）を作らない親の中の `z-index`
（`auto` 以外。`z-index` は section の直下か、`position` と `z-index` を持つ親の直接の子どうしでだけ使う）。section 自身も同じく検査する。

**取り込んだページ**（section に `data-imported`。`import` が書く・[ADR 0025](../docs/adr/0025-import-pptx.md)）は、文字のはみ出し・section の外へのはみ出しを警告にし、
ノートの［クリック］の数を照合しない。回すだけの `transform: rotate()` を通す（PowerPoint の文字の箱の回転に写す）。

**並べ方は自由。** flex・grid・絶対配置のどれでもよい。変換器はブラウザが計算した位置を読む。

## 4. 例

```html
<section data-slide data-transition="fade">
  <div class="kicker" data-keep="kicker">課題</div>
  <h1 data-step="0" data-fx="rise">再配達が<em>1割</em>を超えている</h1>
  <div class="card" data-step="1" data-fx="pop">…</div>
  <div class="card" data-step="2" data-fx="pop">…</div>
  <aside class="notes">最初に結論です。［クリック］1つめは…［クリック］2つめは…</aside>
</section>
```
