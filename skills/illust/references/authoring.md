# 挿絵の書き方（SVG）

挿絵は、中身を表す**動かない絵**。空気・場面・たとえ・気持ちを伝える。構造・手順・比較・数字は挿絵にせず図解にする。
画像生成（Codex）は要らない。SVG を 1 枚書けば、`illust.py` が確かめて描いてよい形に絞り、PNG にする（HTML ならそのまま置ける）。

## 書き方は 2 つ（場面に合わせて選ぶ）

- **A. アイコンを組み合わせる**: 大きなアイコン 1〜3 個を主役にし、丸・角丸の面・矢印・小さな札と組み合わせる。崩れにくい。
  「人が集まる」「書類を送る」「時間を区切る」のように、物と動きで言えるとき
- **B. 単純な形で描く**: 人・机・時計・建物などを、丸・角丸の長方形・太い線だけで描く。場面の空気が出る。
  「会議が長引いて疲れている」「手を挙げて喜ぶ」のように、人の様子で言えるとき

アイコンは `<icon name="名前" x="左" y="上" size="一辺" fill="#色"/>` と書く（形は `illust.py` が展開する）。
使える名前（Material Symbols の部分集合）:

```
add alert analytics arrow-down arrow-left arrow-right arrow-up article assignment award bolt book brain building bulb
calendar captions chart chat check checklist chevron-down chevron-left chevron-right chevron-up clock close cloud code
compare copy cross database delete doc done download edit error event external-link eye fact-check filter flag folder
gear globe handshake help history home hourglass hub image info key laptop layers link list lock mail menu mic more-vert
notice phone pie pin puzzle refresh remove robot rocket school scissors search shield slideshow sort sparkle speaker
speed star table target terminal timer tool translate tree trend trend-down upload user users video work yen
```

## 形と色

- `<svg viewBox="0 0 幅 高さ">` で始める。**縦横の比は、置く枠の比に合わせる**（呼ぶ側が枠の大きさを言う）。数は viewBox の座標で書く
- 色は呼ぶ側が渡す**配色の色だけ**を使う（主役の色・その淡い段と濃い段・灰色・白・人の肌と髪）。ほかの色を足さない
- 地: 枠いっぱいに淡い色の面（主役の色の一番淡い段など）を敷くか、何も敷かずに透明にする（ページの地が見える）
- 主役を 1 つに決め、大きく置く。細かい飾りを散らさない。形の数はおよそ 60 個まで
- 人（B）: 頭は丸（肌の色）、髪は頭の上の半円（髪の色）、体は角丸の長方形、腕は太い線（`stroke-linecap="round"`）。
  **目・鼻・口は描かない**（表情は体の傾き・腕の向きで出す）
- 線は太めに（viewBox の幅 1000 に対して 6〜14）。角は丸める（`rx`・`stroke-linejoin="round"`）

## 書かないもの

- 文字（入れるなら短い語を 1〜2 個・12 字まで。文字は資料の側が持つ）
- 実在の人物・会社・商品・ロゴ・地図・国旗
- `<script>`・`<image>`・`<use>`・`<foreignObject>`・`<style>`・アニメーション・外の URL・`on…` の属性（落とされる）
- DOCTYPE・ENTITY（受け付けない）

## 例（A・960×1080 の縦長の枠）

```xml
<svg viewBox="0 0 960 1080">
  <circle cx="480" cy="540" r="330" fill="#e6f1f3"/>
  <icon name="users" x="250" y="330" size="460" fill="#006f83"/>
  <rect x="650" y="230" width="190" height="130" rx="40" fill="#ffffff" stroke="#73b0bb" stroke-width="8"/>
  <icon name="help" x="700" y="250" size="90" fill="#73b0bb"/>
</svg>
```
