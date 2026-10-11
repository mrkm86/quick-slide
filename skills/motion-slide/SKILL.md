---
name: motion-slide
description: クリックで「拍」が進む Web プレゼン（Motion Slide）を HTML 1枚で作るスキル。場面×拍の台本 HTML を書けば、エンジンが道しるべ（パンくず・拍の点・章のレール・ここまでの言葉）・一覧・話す台本・自動再生・効果音（Web Audio の合成）・BGM（MIDI を macOS 内蔵音源で鳴らした m4a）を足し、外へ何も取りに行かない HTML 1枚にする。値がカードから表のマスへ飛ぶ・線でつなぐ・手書きの丸・その場で触れる LIVE の部品・キー付きの表が使える。色は html-slide のテーマ（既定は default）。全部の拍を Chrome で開いて、はみ出し・切れ・重なり・泣き別れを数える検査つき。「モーションスライド」「Motion Slide」「動くプレゼン」「Web プレゼン」「クリックで進む HTML」「Stage みたいなスライド」「記事をモーションスライドに」「コラムを動くスライドに」「motion-slide」等で発動。※編集できる PPTX が要るなら html-slide、画像生成で描く1枚絵の資料は image-slide、mp4 の解説動画は html-video の領分。
---

# motion-slide — クリックで拍が進む Web プレゼン（HTML 1枚）

台本の HTML（場面×拍）→ `build.py` がエンジン・色・ロゴ・BGM を埋め込む → HTML 1枚 → `check.py` が全拍を開いて検査。
**初版（2026-10-06）。** 参考にした Web プレゼンの分析と見本2本の知見を固めたもの。使いながら直していく前提。

> **ドキュメント**: 台本の書き方（決まり・部品・作り方の原則）は [`references/authoring.md`](references/authoring.md)、効果音と BGM は [`references/sound.md`](references/sound.md)、動画にするのは [`references/video.md`](references/video.md)、設計判断は [`docs/adr/`](docs/adr/)。
> **テスト**: `bash tests/run.sh`
> **出荷検品**: [`INSPECTION.md`](INSPECTION.md)（画面型）。検品役が読むのは `<出力先>/check/inspect.md`。
> 指摘を受けたら、手順を直したうえで `FEEDBACK.md` に記録する（無ければ作る。無いうちは置かない）。

## いつ使うか

| 状況 | 入口 |
|---|---|
| 記事・資料・講座から Motion Slide を作る | **Workflow: make** |
| 書いた台本を1枚にまとめる | `scripts/build.py <deck.html>` |
| 全部の拍を開いて崩れを数える・一覧画像を出す | `scripts/check.py <index.html>` |
| 自動再生をそのまま動画（mp4）にする | `scripts/record.py <index.html>`（[`references/video.md`](references/video.md)） |
| 自動再生の時刻表を見る（BGM を合わせるとき） | `scripts/timeline.py <deck.html>` |
| BGM を付ける | [`references/sound.md`](references/sound.md) |

すべて `python3 -I <このスキルのフォルダ>/scripts/<名前>.py …`（リンク越しに置いても動く）。終了コード: 0=成功 / 2=台本の誤り・検査の NG / 4=依存不足。
出力は `<出力の根っこ>/<題材>/`（台本 `deck.html`・本体 `index.html`・検査 `check/`）。出力の根っこは設定の `out:`、無ければ `<QUICK_SLIDE_HOME>/engines/motion-slide/output`（既定は `~/.quick-slide`）で、`build.py --print-out-root` が出す（html-slide と同じ決め方）。

## Workflow: make

1. **題材を読み、話の筋を決める。** 場面は1つの主張、拍は1つの変化。筋と拍の切り方は [`references/authoring.md`](references/authoring.md) の「作り方の原則」を先に読む
   - 元の文章に**無い数・断定を足さない**（図にするときに一番起きる）。例の会社・ケースは元にあるものだけ
     - 実例（検品で落ちた）: 本文は★4を「初期侵入を防ぐ**だけでなく**…」と説明していた。それを裏返して★3の壁に「初期侵入を防ぐ」と付けた。**図のラベルも、本文の言い方のまま付ける**
   - 公開前の記事・社内の数字を含むときは、出力を外のサービス（Artifact・GitHub Pages・Vercel 等）に出さない
2. `templates/deck.html` を `<出力先>/deck.html` に写して書く。決まり（場面の属性・`data-in` など・`MS.hook`）は authoring.md
3. `build.py <出力先>/deck.html`（→ `index.html`）。rc=2 は stderr の `NG:` を読んで台本を直す
   - 最後に場面ごとに1枚撮って、一覧（Esc / O）のサムネイルとして埋め込む（30秒ほど・1場面約10KB）。試し組みは `--no-thumbs`
4. `check.py <出力先>/index.html` → **NG と警告を 0 にする。** 文字は短くして直す（字を小さくしない）。わざと重ねる要素だけ `data-overlap-ok`
5. **`check/sheet-sNN.png`（場面ごとの一覧）を全部目で見る。** 検査は「重なり」は数えられても「図が意味を伝えているか」は見ない
   - 飛ぶ・波及するなど時間のかかる動きは `--only <場面> --wait 4000` で撮り直して確かめる
6. 人に見せるなら検品: 検品する側に `<出力先>/check/inspect.md` を渡し、[`INSPECTION.md`](INSPECTION.md) の基準で確かめてもらう（不合格なら直して出し直し・最大3ラウンド）
7. 渡すのは `index.html` 1枚。Chrome / Edge で開く（Slack・Teams のプレビューでは動かない）。操作: クリック・→・Space で進む ／ ← 戻る ／ O 一覧 ／ P 自動再生 ／ M 消音 ／ N 話す台本 ／ F 全画面
8. 動画も要るなら `record.py <出力先>/index.html` → `video.mp4`（効果音・BGM つき。動画の長さの約 3.5 倍かかるので裏で回す）

## 構成

| パス | 中身 |
|---|---|
| `engine/engine.js` `engine/engine.css` | エンジン（場面×拍・道しるべ・一覧・自動再生・効果音・BGM・線・飛ぶ・表）。build が埋め込む |
| `templates/deck.html` | 台本の雛形（出す・照らす・線を引く・飛ぶ・表・LIVE を一通り） |
| `scripts/build.py` | 台本 → HTML 1枚（色は html-slide のテーマ・ロゴは config） |
| `scripts/check.py` | 全拍の検査・拍ごとの画像・場面ごとの一覧・`inspect.md` |
| `scripts/timeline.py` | 自動再生の時刻表（拍の切り替わりの秒） |
| `scripts/record.py` `scripts/vclock.js` | 動画にする（ページの時計を止めて1コマずつ撮る・効果音と BGM を重ねる） |
| `scripts/bgm/` | `midi.py`（音符を書く）・`render.swift` `render.sh`（macOS 内蔵音源で m4a に） |
| `config.example.yaml` | 機体ごとの既定（テーマ・ロゴ）。実体は `<QUICK_SLIDE_HOME>/engines/motion-slide/config.yaml`（環境変数 `MOTION_SLIDE_CONFIG` でも指せる）。出力の根っこ `out:` も書ける |

## 制約・落とし穴

- **PPTX にはしない**（このスキルの出口は HTML 1枚）。PPTX が要る用途は html-slide。拍を1枚ずつ平らにして PPTX にする試作はあるが、このスキルには未取り込み
- **字体は端末のもの**（游明朝・游ゴシック → ヒラギノ）。Windows と Mac で字幅が少し違うので、詰めすぎない
- **音は最初のクリックかキーのあとで鳴る**（ブラウザの決まり）。自動再生ボタンも1回のクリックに数える
- **拍の状態は冪等に書く。** 「進んだときだけ」の演出（飛ぶ・数え上げ）は `x.step` / `x.fwd` で分け、飛ばずに来たら最初から着いている見た目にする（一覧から飛んだとき・戻ったときに崩れる）
