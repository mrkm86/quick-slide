# アイコン（Material Symbols の部分集合）

スライドと画面で使う線のアイコン 100 種。**Material Symbols**（Outlined・weight 400・fill 0・grade 0）から、使う分だけを `名前 → {path, viewBox, symbol}` にしたもの。DADS の Figma のアイコン素材が Material Symbols（Apache License 2.0）なので、それに合わせた（利用上の注意事項「デザインデータ（Figma）の利用について」1-2）。

| 項目 | 内容 |
|---|---|
| 実体 | [`material-symbols.json`](material-symbols.json)（名前 → `{"path": "<d 属性>", "viewBox": "0 -960 960 960", "symbol": "<Material Symbols の名前>"}`） |
| 取得元 | npm パッケージ **`@material-symbols/svg-400` 0.47.6**（版は固定）。`https://cdn.jsdelivr.net/npm/@material-symbols/svg-400@0.47.6/outlined/<symbol>.svg` |
| 作り直し | `python3 skills/dads/scripts/build_icons.py`（取得して書く）／`--check`（通信せず JSON と対応表の一致を確かめる）／`--table`（下の対応表を出す） |
| ライセンス | Apache License 2.0（[`LICENSE-material-symbols.txt`](LICENSE-material-symbols.txt)・パッケージの LICENSE をそのまま）。Material Symbols は Google が提供するアイコン |
| 加工 | SVG から `<path>` の `d` と `viewBox` だけを取り出した。形は変えていない |

> 出典の表記（DADS 全体）は [`../README.md`](../README.md)。アイコンの置き方・色・大きさの決まりは [`../references/slide-local.md`](../references/slide-local.md) の 6。

## 座標系（必ず読む）

- **`viewBox` は `0 -960 960 960`**。原点が左上ではなく、y が負の向きに広がる（Material Symbols の元の座標）。24 の格子を 40 倍した値で、1 マス = 40
- そのまま使う: `<svg viewBox="0 -960 960 960" width="40" height="40"><path d="…" fill="currentColor"/></svg>`
- **塗りの図形**（線ではない）。色は `fill`。`stroke` は付けない
- **optical size（opsz）は 48**。このパッケージの SVG は weight 400・fill 0・grade 0・opsz 48 だけで、opsz 24 は含まない。24px に小さく置くと、opsz 24 用の絵より線がやや細く見える。スライドのように大きく置く用途には向く。24px 用の太さが要るなら、別のパッケージ（`@material-symbols/font-400` の可変フォントなど）から取り直す
- 絵はおよそ viewBox の 83%（80〜880）に収まる。周りに約 8% の余白がある。大きさを決めるときは、アイコンの箱（`slide-local.md` の 6）で測る
- **穴のある形**（輪の中の点・チェックの丸）は、path の**巻き方向**で穴を作っている。1 つの path に全部の部分パス（`M`…`Z`）が入っている。**別々の図形に分けない**。PPTX のカスタム図形に写すときは、部分パスを 1 つの図形のままにして、穴が正しく出るかを確かめる

## 対応表（既存の 45 種 → Material Symbols）と追加

html-slide の `scripts/deck_icons.py` の 45 種は、**名前を変えず、意味を引き継ぐ**。左の名前が台本の `icon: "名前"`。追加は、注釈・成功・エラー・警告・情報・矢印・開閉・操作・時間・文書・仕組みなど、スライドと画面でよく使うもの。

| 名前 | Material Symbols | 使いどころ | 出どころ |
|---|---|---|---|
| `doc` | `description` | 書類・原稿 | deck_icons.py から引き継ぎ |
| `list` | `format_list_bulleted` | 一覧・手順 | deck_icons.py から引き継ぎ |
| `checklist` | `checklist` | 決まり・確かめること | deck_icons.py から引き継ぎ |
| `code` | `code` | プログラム | deck_icons.py から引き継ぎ |
| `chart` | `bar_chart` | 数字・グラフ | deck_icons.py から引き継ぎ |
| `trend` | `trending_up` | 伸び・増える | deck_icons.py から引き継ぎ |
| `scissors` | `content_cut` | 切る・削る | deck_icons.py から引き継ぎ |
| `image` | `image` | 写真・画像 | deck_icons.py から引き継ぎ |
| `video` | `play_circle` | 動画・再生 | deck_icons.py から引き継ぎ |
| `mic` | `mic` | 声・話す | deck_icons.py から引き継ぎ |
| `speaker` | `volume_up` | 音 | deck_icons.py から引き継ぎ |
| `captions` | `closed_caption` | 字幕・文字 | deck_icons.py から引き継ぎ |
| `chat` | `chat` | 会話・相談 | deck_icons.py から引き継ぎ |
| `mail` | `mail` | 連絡・メール | deck_icons.py から引き継ぎ |
| `sparkle` | `wand_stars` | 強調・仕上げ・AI | deck_icons.py から引き継ぎ |
| `bulb` | `lightbulb` | 思いつき・アイデア | deck_icons.py から引き継ぎ |
| `target` | `target` | 目標・狙い | deck_icons.py から引き継ぎ |
| `flag` | `flag` | 決めたこと・到達点 | deck_icons.py から引き継ぎ |
| `check` | `check_circle` | できた・よい | deck_icons.py から引き継ぎ |
| `cross` | `cancel` | やめる・よくない | deck_icons.py から引き継ぎ |
| `alert` | `warning` | 注意・困りごと | deck_icons.py から引き継ぎ |
| `search` | `search` | 探す・調べる | deck_icons.py から引き継ぎ |
| `folder` | `folder` | フォルダ・置き場 | deck_icons.py から引き継ぎ |
| `layers` | `layers` | 重ねる・仕組み | deck_icons.py から引き継ぎ |
| `gear` | `settings` | 設定・仕組み | deck_icons.py から引き継ぎ |
| `shield` | `verified_user` | 守る・安全 | deck_icons.py から引き継ぎ |
| `lock` | `lock` | 鍵・閉じる | deck_icons.py から引き継ぎ |
| `link` | `link` | つなぐ | deck_icons.py から引き継ぎ |
| `refresh` | `refresh` | 繰り返す・見直す | deck_icons.py から引き継ぎ |
| `upload` | `upload` | 入れる・出す | deck_icons.py から引き継ぎ |
| `download` | `download` | 受け取る | deck_icons.py から引き継ぎ |
| `calendar` | `calendar_month` | 日付・予定 | deck_icons.py から引き継ぎ |
| `clock` | `schedule` | 時間 | deck_icons.py から引き継ぎ |
| `yen` | `currency_yen` | お金・費用 | deck_icons.py から引き継ぎ |
| `user` | `person` | 人・担当 | deck_icons.py から引き継ぎ |
| `users` | `group` | 人たち・チーム | deck_icons.py から引き継ぎ |
| `building` | `apartment` | 会社・建物 | deck_icons.py から引き継ぎ |
| `laptop` | `laptop_mac` | パソコン・手元 | deck_icons.py から引き継ぎ |
| `phone` | `mobile` | スマホ | deck_icons.py から引き継ぎ |
| `cloud` | `cloud` | 外のサービス・クラウド | deck_icons.py から引き継ぎ |
| `book` | `menu_book` | 学ぶ・手引き | deck_icons.py から引き継ぎ |
| `pin` | `location_on` | 場所 | deck_icons.py から引き継ぎ |
| `star` | `star` | おすすめ・大事 | deck_icons.py から引き継ぎ |
| `rocket` | `rocket_launch` | 始める・速く | deck_icons.py から引き継ぎ |
| `hub` | `hub` | 司令塔・まとめ役 | deck_icons.py から引き継ぎ |
| `info` | `info` | 情報・補足 | 追加 |
| `notice` | `campaign` | お知らせ・告知 | 追加 |
| `help` | `help` | 疑問・ヘルプ | 追加 |
| `error` | `error` | エラー・失敗 | 追加 |
| `done` | `check` | チェックの印だけ（丸なし） | 追加 |
| `arrow-right` | `arrow_forward` | 次へ・進む | 追加 |
| `arrow-left` | `arrow_back` | 戻る | 追加 |
| `arrow-up` | `arrow_upward` | 上・昇順 | 追加 |
| `arrow-down` | `arrow_downward` | 下・降順 | 追加 |
| `chevron-right` | `chevron_right` | リンクの強調・次 | 追加 |
| `chevron-left` | `chevron_left` | 前 | 追加 |
| `chevron-down` | `keyboard_arrow_down` | 開く（アコーディオン・選択） | 追加 |
| `chevron-up` | `keyboard_arrow_up` | 閉じる | 追加 |
| `compare` | `compare_arrows` | 比べる | 追加 |
| `trend-down` | `trending_down` | 減る・下がる | 追加 |
| `add` | `add` | 追加 | 追加 |
| `remove` | `remove` | 削除・引く | 追加 |
| `close` | `close` | 閉じる・× | 追加 |
| `edit` | `edit` | 編集 | 追加 |
| `delete` | `delete` | ごみ箱・削除 | 追加 |
| `copy` | `content_copy` | 複製 | 追加 |
| `external-link` | `open_in_new` | 新しいタブで開く | 追加 |
| `eye` | `visibility` | 見る・表示 | 追加 |
| `filter` | `filter_list` | 絞り込み | 追加 |
| `sort` | `sort` | 並べ替え | 追加 |
| `menu` | `menu` | メニュー | 追加 |
| `more-vert` | `more_vert` | 縦三点（メニューを開く） | 追加 |
| `home` | `home` | ホーム・トップ | 追加 |
| `article` | `article` | 記事・本文 | 追加 |
| `table` | `table_chart` | 表 | 追加 |
| `slideshow` | `slideshow` | スライド・資料 | 追加 |
| `assignment` | `assignment` | 課題・申請 | 追加 |
| `fact-check` | `fact_check` | 検証・照合 | 追加 |
| `database` | `database` | データ・保存 | 追加 |
| `pie` | `pie_chart` | 割合・構成比 | 追加 |
| `analytics` | `analytics` | 分析・気づき | 追加 |
| `timer` | `timer` | 所要時間・計る | 追加 |
| `hourglass` | `hourglass_empty` | 待つ・処理中（動かない砂時計） | 追加 |
| `history` | `history` | 履歴・経過 | 追加 |
| `event` | `event` | 催し・日程 | 追加 |
| `terminal` | `terminal` | コマンド・端末 | 追加 |
| `key` | `key` | 鍵・認証 | 追加 |
| `bolt` | `bolt` | 速い・電気・即時 | 追加 |
| `robot` | `smart_toy` | AI・ロボット | 追加 |
| `brain` | `psychology` | 考える・判断 | 追加 |
| `tree` | `account_tree` | 構造・分岐 | 追加 |
| `puzzle` | `extension` | 部品・拡張 | 追加 |
| `tool` | `build` | 道具・直す | 追加 |
| `speed` | `speed` | 速さ・性能 | 追加 |
| `school` | `school` | 学ぶ・研修 | 追加 |
| `work` | `work` | 仕事・業務 | 追加 |
| `handshake` | `handshake` | 合意・連携 | 追加 |
| `globe` | `public` | 世界・公開 | 追加 |
| `translate` | `translate` | 言語・翻訳 | 追加 |
| `award` | `trophy` | 成果・表彰 | 追加 |

- 既存の `check`（できた・よい）は `check_circle`（丸にチェック）、`cross`（やめる・よくない）は `cancel`（丸に×）、`alert`（注意・困りごと）は `warning`（三角にビックリ）。**成功・エラー・警告・情報の注釈**には、`check`・`error`（丸にビックリ）／`alert`・`info` を使う
- 追加の `done` はチェックの印だけ（丸なし）。リストの項目の印や、表のセルの「あり」に使う
- 色の決まり: ラベルと組み、地に対して 4.5:1 以上（`slide-local.md` の 6）。意味の色には文字（ラベル）を必ず添える
- Material Symbols は名前が変わったものがあり、以前の名前（`auto_awesome`・`smartphone`・`expand_more`・`expand_less`・`emoji_events` など）は 0.47.6 に無い（取得時に 404 になる）。`sparkle` は `wand_stars`、`phone` は `mobile`、`chevron-down` は `keyboard_arrow_down`、`chevron-up` は `keyboard_arrow_up`、`award` は `trophy` に対応させた

## 足したいとき

1. `scripts/build_icons.py` の `ADDED` に `"名前": ("Material Symbols の名前", "使いどころ")` を足す（名前は英小文字とハイフン）
2. `python3 skills/dads/scripts/build_icons.py` で作り直し、この表を `--table` で更新する
3. `python3 -m unittest discover -s skills/dads/tests` が通ることを確かめる（100 種の上限・名前の形・path の形を見る）
4. Material Symbols の名前は https://fonts.google.com/icons で探せるが、**パッケージの版にある名前**だけが使える（0.47.6 の一覧は取得時に確かめる）
