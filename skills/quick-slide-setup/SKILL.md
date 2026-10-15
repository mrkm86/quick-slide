---
name: quick-slide-setup
description: Quick Slide の初回導入・キッティング・セットアップ・診断をする。「Quick Slide を使えるようにして」「Quick Slide をセットアップして」「見本を開いて」と頼まれたら使う。macOS と Windows の依存ソフト、CLI のログイン、サーバの起動、ブラウザの到達を確認する。
---

# Quick Slide のセットアップ

目的は、利用者が見本を開いて使い始められる状態にすること。
同じプラグインの `skills/quick-slide/SKILL.md` の Windows・導入・Codex の注意事項を読んで進める。
`${PLUGIN_ROOT}` はこのファイルの2つ上。作業フォルダではなく、インストールされたこのスキルの場所から決める。

1. Python 3.12 以上を確認する。Windows は `py -3`。見つからない場合は quick-slide の手順に従う。
2. 次を実行し、最終行の JSON の `checks`・`ready`・`inside_codex` を読む。

   ```text
   python3 "${PLUGIN_ROOT}/skills/quick-slide/scripts/quick_slide_setup.py" --doctor
   ```

3. 足りないものと、導入先・ダウンロードが数百 MB になることを短く伝える。
   利用者が「必要なものを入れて」「セットアップして」と導入を依頼済みなら、管理者権限なしで導入する
   以下の処理はその依頼の範囲として実施する。診断だけの依頼なら、導入してよいか確認する。

   ```text
   python3 "${PLUGIN_ROOT}/skills/quick-slide/scripts/quick_slide_setup.py" --setup --yes
   ```

   Codex の砂場で CLI が見えない場合は未導入と断定せず、利用者のターミナルで診断する手順を示す。
   `manual` に出る Python・Node・グローバル CLI の導入は自動で行ったと報告しない。
   必要な操作を具体的に示す。ログインは利用者が行う。API キーを要求しない。
   管理された Python で追加パッケージを入れられない場合は、[導入ガイド](../../docs/INSTALL.md#管理された-python-でパッケージを入れられない場合) の仮想環境を使える。
   以降の診断・起動・投入はその Python を使い、場所を利用者に伝える。
4. 同じ Python で診断し直す。使いたい形式の `ready` を確認し、まだ不足があればその項目を報告する。
5. サーバを起動する。

   ```text
   python3 "${PLUGIN_ROOT}/skills/quick-slide/scripts/quick_slide_start.py" --start-server
   ```

   `start_in_terminal` が返ったら、返った `command` を利用者のターミナルで実行してもらう。
   砂場を回避するためにデータの置き場を変更しない。
6. 起動結果に含まれる URL を開く。ブラウザツールがあれば内蔵ブラウザから到達を確認し、見本が表示されるか確認する。
   ツールがなければ URL を提示する。内蔵ブラウザで確かめていなければ「内蔵ブラウザで動作確認済み」と書かない。
7. 最後に、使える形式・不足している項目・画面の URL・利用者に残る操作を短く返す。
   スライドを新たに生成する必要はない。同梱の見本で起動を確かめる。

Windows では上記の `python3` をすべて `py -3` に読み替える。
プラグインが認識されない場合は [導入ガイド](../../docs/INSTALL.md) を参照する。
