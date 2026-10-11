# 機体に置く

`skills/` の中身はどの機体でも同じ。機体ごとに要るのは、依存と config だけ。

| 要るもの | 何に使う | 無いと |
|---|---|---|
| python3 ＋ PyYAML ＋ Pillow | build（テーマを読む・ロゴの余白を測る） | build が落ちる |
| `skills/html-slide/themes/`（このスキルの隣） | 色の正本。build が自分の実体（`Path(__file__).resolve()`）から辿るので、リンク越しに置いても動く | build が `NG: テーマが無い` |
| Playwright（python）＋ Chromium | build のサムネイル・check（全拍を開く）・record | check が落ちる。build はサムネイル無しで組む。tests は全拍の検査を飛ばす |
| numpy ＋ ffmpeg | 動画にする（`scripts/record.py`・効果音を並べる） | 動画だけ作れない（rc=4） |
| swift（macOS）＋ ffmpeg | BGM を m4a にする（`scripts/bgm/render.sh`） | BGM だけ作れない（rc=4）。**Linux では作れない** |
| `<QUICK_SLIDE_HOME>/engines/motion-slide/config.yaml` | テーマ・ロゴ・出力の根っこ `out:` の既定（雛形は `config.example.yaml`） | 無くても動く（ロゴ無し・default のテーマ・出力は `<QUICK_SLIDE_HOME>/engines/motion-slide/output`） |

## 置き場の決め方（html-slide と同じ）

- 根っこ: 環境変数 `QUICK_SLIDE_HOME`、無ければ `~/.quick-slide`
- 設定: 環境変数 `MOTION_SLIDE_CONFIG`、無ければ `<根っこ>/engines/motion-slide/config.yaml`
- 出力: 設定の `out:`、無ければ `<根っこ>/engines/motion-slide/output`（`build.py --print-out-root` で見える）

Chrome は Playwright の Chromium を使う（html-slide の puppeteer-core ＋ headless shell とは別の道）。在りかの探し方は Playwright の決まりに従う。

ロゴのパスは機体ごとに違ってよい（config に書く。スキルの中に書かない）。
