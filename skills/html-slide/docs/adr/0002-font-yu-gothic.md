# 0002. 資料の既定の字体は游ゴシック（Yu Gothic）にし、PowerPoint 同梱のファイルで測る

- **日付**: 2026-10-03
- **状態**: 採用

## 決めたこと

- 資料の型の既定の字体は `Yu Gothic`（PPTX の字体名も同じ）
- Chrome での計測には、PowerPoint for Mac に同梱されている Windows 版のファイル（`/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGothR.ttc`・`YuGothB.ttc`）を `@font-face` で読ませる。パスは設定（`config.yaml` の `fonts:`）で差し替えられる
- ファイルはスキルに写さない（参照するだけ）

## 採らなかった案と、その理由

| 案 | 却下した理由 |
|---|---|
| ヒラギノ（html-video と同じ） | Windows に無い。客先の PC で別の字体に置き換わり、文字の位置がずれる |
| Noto Sans JP | Mac にも Windows にも標準では入っていない。入れていない PC で置き換わる |
| macOS の游ゴシック体（AssetsV2 の otf） | Windows 版と字幅がわずかに違う。PowerPoint が使うのは同梱の Windows 版なので、それで測る方が一致する |

実測（2026-10-03）: Chrome で `YuGothR.ttc` を読ませた字幅 1401.03px と、PIL で同じファイルを測った値 1401.03px が一致した（100px・「游ゴシックで測るテキストABC」）。

## 効いてくる制約

- PowerPoint が入っていない機体では、既定のパスに字体が無い。設定でパスを渡さないと、Chrome は別の字体で測り、はみ出しの判定がずれる（`check` が字体の読み込み失敗を警告する）

## 見直す条件

- 社のブランドで字体が決まっている資料（`--import` のプロファイル）を作るとき → プロファイルの字体を優先する

## 追記（2026-10-10）既定の字体を Noto Sans JP に替えた

DADS（デジタル庁デザインシステム）の字体に合わせ、テーマの既定の `font` を Noto Sans JP にした（[ADR 0020](0020-dads-foundations.md)）。Noto Sans JP のファイルが分からなければ、この ADR の游ゴシックに落ちて組む（幅の測り方・設定の `fonts` の書き方はそのまま）。
