# 0015 置き場の根っこは Quick Slide のデータの置き場（~/.quick-slide）にする

- 日付: 2026-10-07
- 状態: 採用。ADR 0014 の置き場（`~/Library/Caches/html-slide/`）を `~/Library/Caches/quick-slide/html-slide/` に置き換える
- image-slide の ADR 0016 と同じ判断

## 決めたこと

1. **置き場は `scripts/places.py` の1か所だけで決める。** image-slide と同じファイル（image-slide のテストがバイト単位で見比べる）
2. 根っこは環境変数 `QUICK_SLIDE_HOME`、無ければ `~/.quick-slide/`
   - 設定: `<根っこ>/engines/html-slide/config.yaml`（環境変数 `HTML_SLIDE_CONFIG` が優先）
   - `--out` を省いたときの出力: 設定の `out`、無ければ `<根っこ>/engines/html-slide/output/`
   - 描いた絵のキャッシュ: 環境変数 `HTML_SLIDE_CACHE` ＞ 設定の `cache_dir` ＞ `~/Library/Caches/quick-slide/html-slide/`（macOS）
3. 前は入口（`html_slide.py`）が環境変数を見ずに決まった場所の設定を読み、`deck.py` は `HTML_SLIDE_CONFIG` を見ていた。どちらも `places.py` を通す

## 理由

- 置き場が入口と `deck.py` で別々に決まっていて、同じ設定を読んでいる保証が無かった。1か所で決めれば、Quick Slide に載せたときも置き場を1つ変えるだけで済む
- 製品のデータの置き場の下にエンジンごとの区画を切れば、使う側の環境の決め打ちが要らない

## 残したこと

- puppeteer-core を html-video の置き場（`~/.cache/html-video/node_modules/`）から借りている。Quick Slide v2 の導入の手順で用意する
