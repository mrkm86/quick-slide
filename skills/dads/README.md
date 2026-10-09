# dads — デジタル庁デザインシステムの参照の置き場

デジタル庁デザインシステム（DADS）に沿って資料と画面を作るための、**再利用できる参照**の置き場。どのエンジン（html-slide・motion-slide・Quick Slide の画面）からも同じものを読む。

- 基本デザイン・コンポーネント・アクセシビリティの**要約**（`references/`）
- 公式のデザイントークン（MIT）と、**スライド用ローカルデザインシステム**のトークン（`tokens/`）
- Material Symbols のアイコンの部分集合（`icons/`）
- ブランドの色から DADS のキーカラーを作る道具（`scripts/keycolors.py`）

> **出典**: デジタル庁デザインシステムウェブサイト https://design.digital.go.jp/dads/ のコンテンツを加工して作成。
> この置き場は、デジタル庁が作成・公開したものではない。`references/` は 2026-10-08 版の Markdown 一括版をもとにした**要約・再構成**で、公式の文書そのものではない。正確な文言・最新の内容は一次情報（各ファイルの URL）を見ること。

## 入っているもの

```
skills/dads/
  README.md                        これ
  LICENSE-dads-tokens.txt          公式トークンのライセンス（MIT）
  references/
    foundations.md                 基本デザインの要約（色・文字・余白・角・影・アイコン・レイアウト・リンク）。数値は表で
    components.md                  49 コンポーネントの要約と、スライド・画面での対応表。スライドに転用するものは詳しく
    accessibility.md               アクセシビリティの要点（スライドと画面）と早見のチェックリスト
    slide-local.md                 スライド用ローカルデザインシステムの仕様（実装する人向け）
  tokens/
    dads.css                       公式トークンの CSS 変数（MIT・先頭に出典のコメントを足しただけ）
    dads.tokens.json               公式トークンの JSON（MIT・そのまま。JSON はコメントを書けないのでここに書く）
    slide.json                     スライド用トークンの正本（文字の段・役・余白・格子・角・影・色・コントラストの条件）
    slide.css                      slide.json から作った CSS 変数（--slide-*）。直接編集しない
  icons/
    material-symbols.json          名前 → {path, viewBox, symbol}（100 種）
    README.md                      対応表（既存 45 種 → Material Symbols と追加）・座標系・版
    LICENSE-material-symbols.txt   Apache License 2.0
  scripts/
    keycolors.py                   ブランドの色 → キーカラー。コントラスト比・プリミティブの色相表（標準ライブラリだけ）
    build_slide_css.py             slide.json → slide.css
    build_icons.py                 Material Symbols の固定の版から icons/material-symbols.json を作り直す
  tests/                           unittest（標準ライブラリだけ）
```

## 使い方

まず `references/slide-local.md`（スライド）か `references/components.md`（画面）から入る。迷ったら DADS の要約（`foundations.md`・`accessibility.md`）に戻る。

| 使うところ | 読むもの | 使うもの |
|---|---|---|
| **html-slide**（台本から PPTX） | `references/slide-local.md` の 1〜8（決まり）と 9-2（html-slide では） | `tokens/slide.json`・`tokens/slide.css`・`icons/material-symbols.json`・`scripts/keycolors.py` |
| **motion-slide**（HTML で場面を描く） | `references/slide-local.md` の 9-3（motion-slide では） | `tokens/slide.css`（役のクラス `.slide-t-<役>`・色・余白・格子）・`icons/` |
| **Quick Slide の画面**（ウェブアプリ） | `references/components.md` の 3・`references/accessibility.md` の 6・7・11・`foundations.md` | `tokens/dads.css`（公式トークンをそのまま） |
| 色のテーマを作る | `slide-local.md` の 4-2 | `python3 skills/dads/scripts/keycolors.py '#0db4f2'` |
| コントラストを測る | `accessibility.md` の 1 | `python3 skills/dads/scripts/keycolors.py --contrast '#333333' '#ffffff'` |

- スライドは**スライド用のローカルデザインシステム**（`slide.json`）に従う。DADS の値をそのまま使う所と、拡張した所（80 以上の文字・本文 24 の下限など）が `slide-local.md` で区別してある
- 画面は DADS にそのまま従う（スライド用の拡張は使わない）
- 公式トークンの CSS 変数名（`--color-*`・`--font-*`・`--elevation-*` など）と、スライド用（`--slide-*`）は衝突しない。画面で `dads.css` とスライド用の `slide.css` を同じページに読んでもよい

### 試験

```
python3 -m unittest discover -s skills/dads/tests -v
python3 skills/dads/scripts/build_slide_css.py --check    # slide.css が slide.json と同じか
python3 skills/dads/scripts/build_icons.py --check        # アイコンの JSON が対応表と同じか
```

試験が確かめること: `keycolors.py` の計算（コントラスト比・色相を保った明度の調整・プリミティブの表が公式トークンと同じ）／`slide.json` の整合（文字の段・役・8 の倍数・格子の足し算・角と影が DADS の値・コントラストの条件）／`slide.css` が `slide.json` から作れる中身と同じ／アイコンの数と形。

## 出典・加工の表記とライセンス

DADS の「利用上の注意事項」（https://design.digital.go.jp/dads/introduction/notices/）に従う。

- **DADS 本体**（サイトの文書・ガイドライン。`references/` はこの要約）: 利用するときは出典を記載する。編集・加工して使うときは、加工した旨も記載する。**デジタル庁が作成したかのように見せてはいけない**。上記のほかはデジタル庁ウェブサイトのコピーライトポリシー（https://www.digital.go.jp/copyright-policy ）に従う。
  - 出典（1-1）: 出典：デジタル庁デザインシステムウェブサイト https://design.digital.go.jp/dads/
  - 加工（1-2・記載例 1）: デジタル庁デザインシステムウェブサイト https://design.digital.go.jp/dads/ のコンテンツを加工して作成
  - この置き場の `references/` を外に出す（配る・載せる）ときは、上の加工の表記を付ける。この README が、その表記の置き場
- **この仕組みで作った資料・画面には出典を出さない**。DADS の文書そのものを載せていないため。DADS が画面パーツ（Figma・コードスニペットを編集・加工したもの）への出典表記を求めていないことにも合う。ただし、資料や画面が DADS の文章を引用・転載するなら、出典を付ける

| もの | 場所 | ライセンス | 求められること |
|---|---|---|---|
| DADS 本体の文書（の要約） | `references/` | デジタル庁ウェブサイトのコピーライトポリシー（出典の記載が必要） | 出典と加工の表記（上）。デジタル庁が作ったように見せない |
| デザイントークン | `tokens/dads.css`・`tokens/dads.tokens.json` | **MIT**（Copyright (c) 2023 デジタル庁） | 著作権表示とライセンス全文を残す（`LICENSE-dads-tokens.txt`） |
| Material Symbols | `icons/` | **Apache License 2.0** | ライセンス全文を残す（`icons/LICENSE-material-symbols.txt`）。加工（`d` と `viewBox` の取り出し）を `icons/README.md` に書いた |
| Noto Sans JP | （この置き場には含めない。エンジンが用意する） | **SIL Open Font License 1.1**（Copyright 2022 The Noto Project Authors） | 字体ファイルを配るときは、著作権表示と OFL の全文を同梱する。字体ファイル単体で売らない |

- `tokens/dads.tokens.json` は公式パッケージ `@digital-go-jp/design-tokens`（2.0.1）の `figma/tokens.json` を**そのまま**写したもの（JSON はコメントを書けないので、出典・ライセンスはこの README と `LICENSE-dads-tokens.txt` に書く）。`tokens/dads.css` は同 `examples/tokens.css` の先頭に出典のコメントを足しただけで、ほかは同じ
- `tokens/slide.json`・`tokens/slide.css` は、この置き場が DADS の値をもとに定めたスライド用の値（拡張を含む）。DADS の公式の値ではない
- DADS のデザインデータ（Figma）・コードスニペットは、この置き場に含めない（Figma のデータは CC BY 4.0、コードスニペットは MIT。使うときは各々の条件に従う）

## 取り込んだ版

| もの | 版 |
|---|---|
| DADS サイトの Markdown 一括版 | 2026-10-08 版（145 ファイル） |
| デザイントークン `@digital-go-jp/design-tokens` | 2.0.1 |
| Material Symbols `@material-symbols/svg-400` | 0.47.6（Outlined・weight 400・fill 0・grade 0・opsz 48） |

DADS の更新を取り込むときは、(1) 新しい Markdown 一括版で `references/` の該当箇所を直す、(2) トークンを取り直して `dads.css`・`dads.tokens.json` を差し替え、`tests/` で `slide.json` との整合（段・色・影・角）を確かめる、(3) この表の版を更新する。
