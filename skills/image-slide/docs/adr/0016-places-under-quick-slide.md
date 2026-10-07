# 0016 置き場の根っこは Quick Slide のデータの置き場（~/.quick-slide）にする

- 日付: 2026-10-07
- 状態: 採用。ADR 0015 の置き場（`~/Library/Caches/image-slide/`）を `~/Library/Caches/quick-slide/image-slide/` に置き換える
- 前提: Quick Slide v2 をプラグインとして作り直すにあたり、エンジン（html-slide・image-slide）を v2 のリポジトリの `skills/` に置き、
  プラグインからも CLI で単体で呼べるようにする。そのとき、エンジンが特定の環境の置き場（設定・出力のフォルダ）を決め打ちしていると、
  プラグインを入れた機体で動かない。持ち主が変わっても移しやすい形にする
- html-slide の ADR 0015 と同じ判断

## 決めたこと

1. **置き場は `scripts/places.py` の1か所だけで決める。** html-slide と同じファイル（テストがバイト単位で見比べる）
2. **根っこは Quick Slide のデータの置き場**: 環境変数 `QUICK_SLIDE_HOME`、無ければ `~/.quick-slide/`
   - 設定: `<根っこ>/engines/image-slide/config.yaml`（環境変数 `IMAGE_SLIDE_CONFIG` が優先）
   - `--out` を省いたときの出力: 設定の `out`、無ければ `<根っこ>/engines/image-slide/output/`
   - 描いた絵のキャッシュ: 環境変数 `IMAGE_SLIDE_CACHE` ＞ 設定の `cache_dir` ＞ `~/Library/Caches/quick-slide/image-slide/`（macOS）
3. **前からの環境で設定と出力先を変えたくないときは、`<根っこ>/engines/image-slide/config.yaml` を前の設定へのリンクにする。**
   その設定の `out` に前の出力先を書けば、出力は前と同じ。
   環境変数で渡す形にしないのは、定期実行・常駐の窓口・Codex・手で開いたセッションのそれぞれに渡す必要があるから
4. エンジンの文書（SKILL.md・references）にも特定の環境の絶対パスを書かない。スクリプトは「このスキルのフォルダ」からの相対で書く

## 理由

- **作り手の名前を入れない。** 前の Quick Slide の置き場は、作り手の名前を含むフォルダだった。移管で作り手が変わると、置き場も変わる。
  製品名だけなら、変わるのは製品名が変わるときだけ
- **名前を1か所にしか書かない。** 名前が変わったときに困ったのは、名前そのものより書き散らしだった。
  文書に改名前の置き場が残り、前の Quick Slide も改名（slide-studio → quick-slide）の後、
  配った写しが前の置き場を見たままだった（quick-slide の ADR 0046）。改名のときに直すのは `places.py` と、v2 のサーバの前の名前の一覧だけにする
- **v1 と置き場を分ける。** v1 は前の置き場を使い続けている。v2 が同じ置き場を使うと、DB の形の違う2つのアプリが1つのデータを触る

## 確かめたこと（2026-10-07）

- テスト: image-slide 53 件・html-slide 89 件すべて通過。テストは使い捨ての `QUICK_SLIDE_HOME` で走り、実の置き場を作らない
- 前からの環境: `places.py` が返す設定は `~/.quick-slide/engines/<スキル>/config.yaml`（リンク）、出力は前と同じ、
  キャッシュは `~/Library/Caches/quick-slide/<スキル>`。前のキャッシュ（`~/Library/Caches/image-slide/`）はまだ作られていなかったので、移すものは無かった

## 残したこと

- html-slide は、Chrome を動かす puppeteer-core を html-video の置き場（`~/.cache/html-video/node_modules/`）から借りている。
  プラグインを入れた機体には無いので、v2 の導入の手順で用意する
