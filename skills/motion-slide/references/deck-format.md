# 場面ごとの台本（JSON）の書き方

台本は JSON 1枚。**場面ごとの部品**（場面の HTML・見た目・拍ごとの動き・話す台本）と、デッキ全体の設定を持つ。
`scripts/motion_slide.py` が場面を並べて [`../templates/deck.html`](../templates/deck.html) の形の台本 HTML にし、`build.py` → `check.py` に通す。
**型は持たない。** 場面の中身は HTML・CSS・JS で自由に書く（図と動きは場面ごとに違う）。HTML の決まり（`data-in`・`.x`・`MS.hook` の `x.` など）は [`authoring.md`](authoring.md) がそのまま効く。
手本は `scripts/split.py` が見本の台本 HTML から作った JSON（`python3 -I scripts/split.py <deck.html> --out <script.json>`）。

## 書く前に（台本係が読む）

1. **場面は1つの主張、拍は1つの変化。** 見出しは主張で書き、拍ごとに1つだけ出す・照らす・消す・線を引く（authoring.md の「作り方の原則」）
2. **場面の中で閉じる。** `html`・`style`・`script`・`hook` は、自分の場面の要素と id だけを使う。別の場面の `#id`・要素・`MS.go(別の番号)` を引かない（検査が NG にする）。場面をまたぐもの（額縁・主役・共通の定数）だけ、一番外の `stage`・`script`・`style` に置く
3. **`id` は英小文字で始まる英小文字・数字・ハイフン**（`s1`・`cover`・`why-now`）。`MS.hook` と `<section>` の id になる。要素の `id` は台本全体で重ねない（場面ごとに `grid5`・`list8` のように変える）
4. **`beats` と数をそろえる。** `dur`（拍ごとの ms）と `talk`（話す台本）は `beats` と同じ数。`data-in`・`data-hl`・`data-dim`・`data-st` は 0 〜 `beats`-1（0 始まり）
5. **`style` の規則は全部 `#<id> ` で始める**（`#s3 .card{…}`）。始めないと他の場面にも効く。`#<id>` の直後に `~`・`+`・`:has()` を付けて場面の外へ出さない（`#s3 ~ .scene{…}` は NG）。`@import` は書けない。色はトークン（`var(--pri)` など）だけ使う
6. **`hook` は `(b, x) => { … }` の本体だけ。**本体は単独で文法が通ること（`}` で閉じて続きを書く形は落ちる）。 `MS.hook(…)` は書かない（組み立てが付ける）。`b` は拍（0 始まり）、`x` は拍の道具。**何度呼ばれても同じ見た目にする**（冪等。「進んだときだけ」の演出は `x.step` / `x.fwd` で分ける）
7. **元の文章に無い数・断定を足さない**（SKILL.md の Workflow: make と同じ）。字を小さくして収めない（短くする）

## 一番外

| キー | 必須 | 中身 |
|---|---|---|
| `title` | | 題（HTML の title・一覧・`inspect.md`・thumbs の見出し）。無ければ台本のファイル名 |
| `theme` | | html-slide のテーマの名前（`default`・`forest`・`warm`）か YAML のパス（台本からの相対でよい）。`--theme` が優先 |
| `config` | | `ms-config` のキー（下） |
| `style` | | 共通の CSS（額縁・`:root`・エンジンの上書きなど。場面の CSS はここに書かない） |
| `stage` | | `<div id="stage">` の直下で、場面の**後**に置く HTML（額縁・場面をまたぐ主役など）。`<section>`・`<script>` は書けない |
| `script` | | 共通の JS（場面をまたぐ定数・関数・`MS.ready`・`IDS.forEach(… MS.hook(id, …))` のような場面を束ねる動き） |
| `scenes` | ○ | 場面の配列（上から順） |

`config`: `crumb`（パンくずの頭）・`chapters`（章の名前の配列。空なら章のレールを出さない）・`dur`（`dur` の無い拍の ms・既定 4200）・`terms`（残す言葉の数・既定 6）・`ambient`・`sfx`・`bgmVolume`。意味は authoring.md の表と同じ。

`html`・`style`・`script`・`stage`・`hook` は**文字列か、行の配列**（配列は改行でつなぐ。長いものは配列のほうが直しやすい）。

## 場面（`scenes` の1つ）

| キー | 必須 | `<section>` に出るもの |
|---|---|---|
| `id` | ○ | `id`。`MS.hook` の場面 id |
| `title` | ○ | `data-title`（パンくず・章のレール・一覧。**章のレールは 224px・18px で1行**） |
| `beats` | ○ | `data-beats`（1 以上の整数） |
| `ch` | | `data-ch`（章の番号・0 始まり。`config.chapters` の範囲） |
| `norail` | | `data-norail`（true で章のレールを消す。表紙・まとめ） |
| `term` | | `data-term`（この場面で覚える言葉） |
| `dur` | | `data-dur`（拍ごとの ms の配列。`beats` と同じ数） |
| `thumb` | | `data-thumb`（一覧・`slides/slide-NN.png` に撮る拍・0 始まり。既定は最後の拍） |
| `class` | | `scene` に足すクラス名 |
| `attrs` | | そのほかの属性（`data-*` と `style` の辞書。値は文字列か true） |
| `html` | ○ | `<section>` の中身（外枠はエンジンが付ける）。`<section>`・`<script>`・`<style>`・`<iframe>`・`<object>`・`<embed>`・`<base>`・`<meta>`・`<link>`・`<title>`・`<textarea>`・`<template>`・`<aside class="notes">` は書けない（大文字小文字は区別しない）。閉じ忘れ・閉じすぎ・閉じていない `<!--` は NG |
| `style` | | この場面の CSS。規則は全部 `#<id>` で始める |
| `script` | | この場面だけの定数・部品の組み立て・`MS.ready`。場面ごとに1つの関数に包まれる（名前は他の場面と重ならない）。`MS.hook` は書かない |
| `hook` | | 拍ごとの動き。`(b, x) => { … }` の本体。同じ `script` の定数が見える |
| `talk` | | 話す台本。拍ごとの文字の配列（`beats` と同じ数・文字のみ）。`<aside class="notes">` になる（N キー・`inspect.md`） |

## 例

```json
{
 "title": "雛形の台本",
 "config": { "crumb": "雛形", "chapters": ["見せ方", "仕掛け"], "dur": 4200, "terms": 6 },
 "scenes": [
  {
   "id": "s1", "title": "表紙", "beats": 2, "norail": true, "dur": [3600, 4200],
   "style": ["#s1 .cover{position:absolute;left:180px;top:320px}", "#s1 h1{font-family:var(--head);font-size:104px}"],
   "html": ["<div class=\"cover\">", "  <h1 class=\"x\" data-in=\"0\">見出しは<br><em>主張で書く</em></h1>", "  <p class=\"lead x\" data-in=\"1\">クリックごとに1つだけ増える</p>", "</div>"],
   "talk": ["拍1で話すこと。", "拍2で話すこと。"]
  },
  {
   "id": "s3", "title": "値がマスへ飛ぶ", "beats": 3, "ch": 1, "term": "表", "dur": [4200, 5600, 6000],
   "style": "#s3 #tb{position:absolute;left:560px;top:0}",
   "html": "<div class=\"card src x\" data-in=\"0\"><b class=\"v\">42</b> 件</div><div id=\"tb\" class=\"x f\" data-in=\"0\"></div><svg class=\"wires\" id=\"w3\"></svg>",
   "script": "const COLS = [{ k: \"name\", label: \"項目\", w: 200 }, { k: \"v\", label: \"値\", w: 200 }];",
   "hook": ["const fly = b === 1 && x.step;", "x.table(\"tb\", { at: [560, 0], cols: COLS, rows: [{ k: \"a\", name: \"件数\", v: b >= 1 && !fly ? \"42\" : \"\" }] });"],
   "talk": ["カードと空の表。", "値が飛んで、マスに入る。", "書き換えると表も変わる。"]
  }
 ]
}
```

## 組み立てのしくみ（場面を壊し合わせない）

場面の `hook`・`script` は HTML の `<script>` に直に埋めない。組み立てが全場面ぶんを JSON（`<script type="application/json" id="ms-scenes">`・`<` `>` `&` は `\u` で逃がす）に入れ、エンジンが場面ごとに `new Function` で作る。
本体だけで文法を検査してから `(b, x) => { 本体 }` に包むので、`}` で本体の外へ出る書き方・`</script>` を含む文字列は、その場面の中で落ちるだけで、他の場面や共通部には届かない（文法の誤りはその場面の hook・script だけが動かず、コンソールに `場面 s3 の…` が出る）。
共通の `script`・`style`・`stage` は HTML に直に入るので、`</script`・`<!--` は逃がし、`</style` は NG にする。
**ブラウザの CSP**: 発表の画面で場面の hook・script を動かすには `script-src` に `'unsafe-eval'` が要る（`new Function` を使うため）。

## 検査（`check` / `deck`）

`motion_slide.py check` は、形の検査（Chrome なし・`--quick` ならここまで）→ 組み立て → 全部の拍を Chrome で開く検査、の順。NG は `NG: N枚目（id s3）: …` で、直し方まで書く。主なもの:

| 検査 | NG の例 |
|---|---|
| 形 | 知らないキー・`title`/`beats`/`html` が無い・`id` の書式と重なり・`ch` が章の外・`dur`/`talk` の数が `beats` と違う・`data-in` が拍の外 |
| HTML | 閉じ忘れ・閉じすぎ・`html`・`stage` に禁止のタグ（`<SCRIPT>` も）・閉じていない `<!--`・要素の `id` が場面をまたいで重なる |
| 場面の中で閉じる | `hook`/`script`/`style` が別の場面の `#id`・要素の id（`getElementById`・`querySelector`・`$`・`x.table`・`x.cell`・`x.wire`・`x.hand`。空白・コメントをはさんでも見る）・`MS.go(別の番号)` を引く・`style` の規則が `#<id>` で始まらない／`~` `+` `:has()` で外へ出る |
| JS | `hook`/`script` はエンジンと同じ `new Function` の文法検査・共通 `script` は `node --check`（`node` があるとき） |
| 全拍（Chrome） | JS のエラー・舞台の外へのはみ出し・文字が切れる（NG）／重なり・道しるべとの重なり・泣き別れ・パッと出る（警告。`--strict` で NG） |

見た目の確認（`slides/slide-NN.png`・`thumbs.png`・`check/sheet-sNN.png`）は目で見る。検査は図が意味を伝えているかを見ない。
