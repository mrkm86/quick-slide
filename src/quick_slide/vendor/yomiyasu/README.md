# yomiyasu（同梱）

日本語の文章の読みやすさを調べるリンター。台本係が書いた台本の検査に使う（`src/quick_slide/writer/jlint.py`）。
このフォルダは上流の写しで、**中身に手を入れない**（`__init__.py` と、この README・`update.sh` だけがこちらで足したもの）。

- 版: v1.1.1
- コミット: 373356eb874af5184962c88e925ea7eb58450318
- 取り寄せ元: [yomiyasu のリポジトリ](https://github.com/nanaism/yomiyasu)
- 写した日: 2026-10-11

## 写したファイル

上流の `skills/yomiyasu/scripts/` から次の 2 つ。リンター本体が読むものはこれで全部。

- `yomiyasu_lint.py` — リンター本体（台本の検査は `lint_text` だけを呼ぶ）
- `markdown_visibility.py` — リンターが読む、Markdown の見えている文字を取り出す部品

上流のリポジトリ直下の `LICENSE`・`UNICODE-LICENSE.txt` は、原文のままここに置く。
差分を調べる `yomiyasu_diff.py`、試験の文例（`tests/corpus`）・記事・評価は使わないので写さない。

## ライセンス

- `LICENSE` — MIT。複製には、この表示を含めることが条件
- `UNICODE-LICENSE.txt` — リンターの絵文字の判定に、Unicode の公開データから作った表が入っている。その Unicode License V3

どちらも、写したコードと同じこのフォルダに置く。消さない・書き換えない。

## 手を加えた所

なし。

## 更新の手順

版は固定で、定期的な自動更新はしない。上げるときだけ、手で次を実行する。

```bash
bash src/quick_slide/vendor/yomiyasu/update.sh <タグ>   # 例: v1.1.1
```

タグは `v1.1.1` のような形だけ受ける（合わなければ何も触らずに止まる）。指定のタグを一時フォルダに浅く clone し、
上の 2 つのファイルとライセンス文・書き換えた README を一時フォルダにそろえて確かめてから、vendor に入れ替える
（途中で失敗したら元に戻す）。そのあとリポジトリの試験（`tests/run.sh`）を流し、落ちたら元の版に戻す
（`--keep-on-fail` を付けると、新しい版のまま残して調べられる）。ネットワークに出るのはこのスクリプトだけ。

試験のうち `tests/test_jlint.py` の固定の試験が、上流の決まりの変化（台本の検査に使う 3 つが今の挙動のままか、
使わない決まりが NG に混ざらないか）を教える。落ちたら、変化の中身を読んでから `jlint.py` の扱いを決める。
差分を確かめるまでコミットしない。
