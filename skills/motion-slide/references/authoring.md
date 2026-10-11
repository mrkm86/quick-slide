# 台本の書き方

> 台本 = `<出力先>/deck.html`。雛形は [`../templates/deck.html`](../templates/deck.html)、出来上がりの見本も同じ雛形（出す・照らす・線を引く・飛ぶ・表・LIVE を一通り）。

## 作り方の原則（参考にした Web プレゼンの分析から）

参考にした Web プレゼン（2026-10-06 に録画を分析）と見本2本で効いたもの。

| 原則 | 書き方 |
|---|---|
| **場面は1つの主張、拍は1つの変化** | クリックごとに「1つ出る・1つ照らす・1つ消える・線が1本引かれる」のどれか。2つ同時に変えない |
| **見出しが拍で変わる** | 場面の中で主張が進むなら、見出しを拍ごとに差し替える（`data-in` / `data-out` を付けた `.h2` を重ねる）。見出しだけ読めば話が追える |
| **見出しは主張で書く** | 「比較」ではなく「違いは5つに分けて見る」。強調は `<em>` を1か所 |
| **図で言う** | 文を並べる代わりに、仕組み（城の壁と部屋・ネットワーク・時間の帯・枠の数）で見せる。文は図の横に1〜2行 |
| **数は動かして見せる** | 26 → 43 は、枠が埋まっていく・数え上げる（`data-count` / `x.countUp`） |
| **否定は線と判子** | 誤解は一度出してから `data-st`（取り消し線）や `.stamp.cross`（ブザー音）で消す |
| **その場で触れる部品を1つ** | チェックリスト・入力欄・つまみ（`.live-zone` の中はクリックで進まない）。見る人が自分ごとにする場所 |
| **道しるべは常に出す** | パンくず・拍の点・章のレール・ここまでの言葉はエンジンが出す。台本は `data-title` と `data-term` を書くだけ |
| **最後は出典と時点** | 制度・数字の話は「いつ時点の、どの情報から」を最後の場面に書く |

## ファイルの形

```html
<head>
  <title>題（一覧と inspect.md の題になる）</title>
  <script type="application/json" id="ms-config">{"crumb":"コラム","chapters":["違いを知る","選び方"],"dur":4200,"terms":6}</script>
  <style>/* 場面ごとの見た目。#s3 .castle{…} のように場面 id で始める */</style>
</head>
<body><div id="stage">
  <section class="scene" id="s1" data-beats="2" data-title="表紙" data-norail data-dur="3800,5200">…</section>
  …
</div>
<script>/* MS.hook / MS.ready */</script></body>
```

| `ms-config` のキー | |
|---|---|
| `crumb` | パンくずの頭（題材の短い名前） |
| `chapters` | 章の名前。場面の `data-ch`（0始まり）で引く。空なら章のレールを出さない |
| `dur` | `data-dur` の無い拍の自動再生の長さ（ms・既定 4200） |
| `terms` | 下に残す「ここまでの言葉」の数（既定 6。超えた分は「+N」） |
| `ambient` | 背景の流れる線（既定 true） |
| `sfx` | 開いたときに音を出すか（既定 true。M で切り替え） |
| `bgmVolume` | BGM の音量（既定 0.55） |

## 場面（`section.scene`）の属性

| 属性 | 必須 | |
|---|---|---|
| `id` | ○ | `s1`, `s2` … `MS.hook` で引く |
| `data-beats` | ○ | 拍の数 |
| `data-title` | ○ | パンくず・章のレール・一覧に出る（**章のレールは 224px・18px で1行**。長いと check が NG） |
| `data-ch` | | 章の番号（0始まり） |
| `data-norail` | | 表紙・まとめ。章のレールを消し、本文を左に寄せる |
| `data-term` | | この場面で覚える言葉。下の「ここまでの言葉」に積もる |
| `data-dur` | | 拍ごとの自動再生の長さ（ms をカンマで。**数は `data-beats` と同じ**。違えば build が NG） |
| `data-thumb` | | 一覧のサムネイルに撮る拍（0始まり。既定は最後の拍）。最後の拍がまとめの文だけの場面は、図が出そろった拍を指す |

話す台本は場面の直下に `<aside class="notes"><p>拍1</p><p>拍2</p>…</aside>`（N で出る・`inspect.md` に入る）。

## 拍で付け外しする属性（エンジンが毎拍当てる）

拍は **0 始まり**。「`b` 以上 `out` 未満」で効く。

| 属性 | 付くクラス | 見た目 |
|---|---|---|
| `class="x" data-in="1" [data-out="3"]` | `.on` | 出る・消える。`.x` は下から、`.x.f` はその場で、`.x.p` は弾んで、`.x.l` / `.x.r` は横から |
| `data-hl="1" [data-hlout="2"]` | `.hl` | 照らす（`.card` は枠が主色に。ほかは場面の CSS で書く） |
| `data-dim="2" [data-dimout]` | `.dim` | 薄くぼかす |
| `class="strike" data-st="1"` | `.st` | 赤い取り消し線が引かれる |
| `data-count="43" [data-cin="1"] [data-cms] [data-tick]` | — | その拍で 0 から数え上げる（進んだときだけ。戻ったら最初から数が入っている） |

場面の要素には、今の拍が `data-beat` で付いている。拍ごとに CSS を変えるなら `#s3[data-beat="1"] .room{…}`。

**効果音は自動で鳴る**（進んだときだけ・戻るときは静か）: 場面が変わる whoosh ／ 拍 tap ／ `.cross` が出る buzz ／ `.stamp` と `.p` が出る pop ／ `.hl` が付く ping。

## 共通の部品（engine.css）

| クラス | |
|---|---|
| `.h2`（`<small>` で副題） | 見出し。左 340px・上 130px。2行まで |
| `.body` | 本文の箱（左 340・右 140・上 330・下 130 → 幅 1440 × 高さ 620）。中は `position:absolute` で置く |
| `.card` `.badge` `.lab` | 白い札・丸い番号・小さな英字の見出し |
| `.stamp`（`.cross` で赤・`.ok` で主色） | 判子。`.x.p` と組むと押される |
| `.live` | 点滅する「LIVE」の印 |
| `.arrow` | 横の矢印 |
| `svg.wires` | 線と手書きの丸を描く板（場面に1枚、`id` を付ける） |
| `.live-zone` | この中のクリックでは拍が進まない（入力欄・ボタン・チェックリスト） |
| `data-overlap-ok` | check の重なりの検査から外す（判子を図に重ねるときなど） |

**色はトークンだけ使う**: `--pri`（主色）`--pri-d`（濃い）`--pri-m`（中）`--pri-l`（淡い地）`--ink`（文字）`--neg`（赤）`--g-mid` `--g-l` `--g-weak` `--g-bg`（灰）。
値は build が html-slide のテーマから作る（既定 = html-slide の `default` テーマの primary）。字体は `--head`（見出し・明朝）`--gothic` `--mono`。

## 拍ごとの仕掛け（`MS.hook`）

```js
MS.hook("s4", (b, x) => { … });   // 場面 s4 の拍 b（0始まり）の見た目を丸ごと決める
MS.ready(() => { … });            // 1回だけ（部品を組む・入力欄のイベント）
```

**フックは何度呼ばれても同じ見た目にする（冪等）。** 一覧から飛んだとき・戻ったときにも呼ばれる。
拍が変わるたびに、エンジンは `x.later` のタイマー・`svg.wires` の中身・飛んでいる途中の文字を消してから呼ぶ。

| `x.` | |
|---|---|
| `step` / `fwd` / `prev` | 前の拍から1つ進んで来たか ／ 前へ進んだか ／ 前の `{S, B}`。**「進んだときだけ」の演出はこれで分ける** |
| `later(fn, ms)` `every(fn, ms)` | 拍が変わると消えるタイマー（素の setTimeout を使わない） |
| `sfx(name, gap)` | pop / ping / whoosh / swish / pen / buzz / tick / tap |
| `table(id, spec)` | キー付きの表。`{at:[x,y], cols:[{k,label,w}], rows:[{k, 列:値}], rh, hh, hlCol, hlRow, headHl:[列], mark:{"行:列":"ok"|"bad"|"mute"}}`。同じ鍵のマスは動いて変わり、増えたマスは出てきて、消えたマスは消える |
| `cell(id, "行:列")` | 表のマスの要素（飛ばす先・線の端） |
| `fly(src, dst, opt)` | src の位置から dst へ文字を飛ばす。`{text, html, fs, dx, dy, box, cls, onLand}`。着いたら pop |
| `wire(svgId, a, b, opt)` | a の右から b の左へ曲線。`{fromLeft, toRight, dash, color}` |
| `hand(svgId, el, color)` | el を手書きの丸で囲む（ペンの音） |
| `countUp(el, to, ms, {from, fmt, tick})` | 数え上げ |
| `rel(el)` | 舞台の座標（1920×1080）での位置 |

外からは `MS.go(s, b)` `MS.next()` `MS.state`（`{S, B, scenes, sfx}`）。入力欄で値を変えたら `MS.go(MS.state.S, MS.state.B)` で同じ拍を当て直す。

### 「飛ぶ」の型（見本: 雛形の s3）

```js
const fly = b === 1 && x.step;          // 拍1→2 と進んだときだけ飛ぶ
T(x, b >= 1 && !fly ? v : "");          // 飛ばずに来たら、最初から入っている
if (fly) x.later(() => x.fly(src, x.cell("tb", "a:v"), { fs: "21px", onLand: () => T(x, v) }), 500);
```

## 置き方の寸法

- 舞台は 1920×1080 固定（エンジンが画面に合わせて縮める）。**道しるべの場所に置かない**: 上 0〜120（パンくず・拍の点・操作）、左 96〜320 の 300px から下（章のレール）、下 990〜（言葉・ロゴ）
- 本文の文字は 22px 以上。見出しは 64px で 22 字まで（1行）
- `check.py` の NG（はみ出し・切れ・JS のエラー）と警告（重なり・道しるべとの重なり・泣き別れ・パッと出る）は **0 にしてから見せる**
- **`.x` を付けた要素に `transition` を書くときは `opacity` を入れる。** 書いた `transition` が `.x` の指定を丸ごと上書きして、ふわっと出ずにパッと出る（`.card` で踏んだ。check の「パッと出る」はこれ）
