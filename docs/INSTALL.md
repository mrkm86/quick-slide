# 導入・検証ガイド

通常の導入は [README](../README.md) の会話への依頼で進めます。
このページは手動で操作するときと、実機の検証に使います。
プラグイン未導入から会話で進める場合は [初回導入プロンプト](FIRST-INSTALL.md) を使ってください。

## 手動で準備する

`<プラグイン>` は、インストールされた Quick Slide の根です。
更新後も展開元の古いコピーを実行し続けないよう、使っているスキルの場所を確認してください。

```text
python3 "<プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py" --doctor
python3 "<プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py" --setup --yes
python3 "<プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py" --doctor
python3 "<プラグイン>/skills/quick-slide/scripts/quick_slide_start.py" --start-server
```

Windows は `python3` を `py -3` に替えます。空白のあるパスは引用符で囲みます。
ログインは別に `codex login`、Claude Code では `/login` を行います。
診断の `manual` に残ったものは、表示された案内に従って準備してください。
セットアップはプラグイン登録やアカウントへのログインを代行しません。

### 管理された Python でパッケージを入れられない場合

Homebrew 等の Python で `externally-managed-environment` が出る場合は、
環境の管理を解除する代わりに、プラグインの外の利用者が管理するフォルダに仮想環境を作れます。
以下の `<導入用フォルダ>` はプラグイン更新で消えない場所にします。

```text
python3 -m venv "<導入用フォルダ>/venv"
"<導入用フォルダ>/venv/bin/python" -m pip install python-pptx lxml Pillow PyYAML playwright
"<導入用フォルダ>/venv/bin/python" -m playwright install chromium
"<導入用フォルダ>/venv/bin/python" "<プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py" --setup --yes
"<導入用フォルダ>/venv/bin/python" "<プラグイン>/skills/quick-slide/scripts/quick_slide_start.py" --start-server
```

モーションを使わない場合は Playwright と Chromium は不要です。
診断・起動・投入・修正はすべて、この仮想環境の Python で実行してください。
仮想環境の場所と実行する Python を導入結果に残し、次の会話でも同じものを使います。
Windows の仮想環境の Python は `<導入用フォルダ>/venv/Scripts/python.exe` です。
`--setup` 自体が仮想環境を自動で作るわけではありません。

## 手元のフォルダから入れる

配布元のリポジトリを使わず、手元のフォルダ（リポジトリの写しや、ZIP を展開したもの）から入れることもできます。
フォルダには隠しフォルダ `.codex-plugin`・`.claude-plugin` と `skills`・`src` が必要です。ZIP は隠しフォルダごと展開してください。

```text
codex plugin marketplace add "<フォルダ>"
codex plugin add quick-slide@quick-slide

claude plugin marketplace add "<フォルダ>"
claude plugin install quick-slide@quick-slide
```

この形で入れたものは、配布元から新しい版を自動で取ってきません。更新のしかたは下の「ローカルフォルダ・ZIP から更新する」です。

## デスクトップアプリで確認する

macOS・Windows のそれぞれで、Codex・Claude Code を個別に確認します。
Cowork はホスト側の CLI・サーバに到達できるかも別途確認します。

- プラグイン一覧に Quick Slide があり、新しい会話で `quick-slide` と `quick-slide-setup` が認識される。
- セットアップを依頼して診断が動き、使いたい形式の `ready` が真になる。
- サーバを起動して、ホームに3形式の見本が表示される。
- 会話で返った URL を内蔵ブラウザで開き、画面が実際に表示される。URL を出せただけでは合格にしない。
- 内蔵ブラウザが使えない場合は外部ブラウザで開ける。結果は内蔵／外部を区別して記録する。
- 会話から短い資料を作り、完成したページが表示される。台本・画像の CLI にサブスクのログインを使う。
- 会話で1ページを直し、指定したページに反映される。
- プレゼンの PPTX・PDF をダウンロードし、開ける。モーションは HTML を開ける。
- アプリを再起動してもプラグインと作成した資料が残る。

記録には OS・アプリと CLI の版・導入元・確認した形式・ブラウザの経路・結果を残します。
実機のログインや内蔵ブラウザは、自動テストの偽物の CLI では検証できません。

## 更新する

会話で次のように依頼します。

> Quick Slide を更新して。登録済みの配布元から更新し、資料と設定は残して。
> 更新前後の版と診断結果を確認して、再起動など必要な操作を教えて。

Codex・Claude は更新スキルから `skills/quick-slide/scripts/quick_slide_update.py` を実行し、
最新の版と変更点を確認して更新します。以下は手動で操作するときの補足です。
両方入っている機械では、どちらを更新するか指定できます。Windows でもプラグイン管理のコマンドは同じです。

### 登録先と現在の版を確認する

Codex:

```text
codex plugin marketplace list
codex plugin list --json
```

Claude:

```text
claude plugin marketplace list
claude plugin list --json
```

以下の `quick-slide` はマーケットプレイス名の既定値です。違う名前で登録されていたら実際の名前を使います。
対象のインストール先、版、有効状態と、配布元の種類を確認します。
CLI の対応コマンドは版によるため、未対応なら `--help` で確認し、アプリのプラグイン画面で更新します。

### Git の配布元から更新する

Codex:

```text
codex plugin marketplace upgrade quick-slide
codex plugin add quick-slide@quick-slide --json
codex plugin list --json
```

`upgrade` は Git の配布元を更新します。続いて `add` でインストールを反映し、結果の版とインストール先を確認します。
既存インストールの更新に対応しない版なら、アプリのプラグイン画面で更新します。
既存の有効状態を記録し、意図せず変わっていれば元に戻します。アンインストールを更新の既定手順にしません。

Claude:

```text
claude plugin marketplace update quick-slide
claude plugin update quick-slide@quick-slide
claude plugin list --json
```

複数の scope に入っている場合は `--scope` で対象を指定します。
管理者が管理するインストールは、管理者の更新手順に従います。

非公開リポジトリの更新には、利用者がその配布元を読めるアクセス権と Git の認証が必要です。
アクセス拒否のときは権限・認証を直してから再実行します。更新のために配布元を公開へ切り替える必要はありません。

### ローカルフォルダ・ZIP から更新する

ローカル登録は、新しい ZIP を自動で取得しません。登録済みの元フォルダを新しい配布物に更新してから、
ホスト別にインストール済みコピーへの反映を確認します。Codex のローカル登録では `marketplace upgrade` は
Git 登録ではないためエラーになります。これを省き `codex plugin add quick-slide@quick-slide --json` を実行します。
Claude は `claude plugin marketplace update quick-slide` と `claude plugin update quick-slide@quick-slide` を使います。
ZIP は別フォルダで展開して隠しフォルダを含む構成を確認します。古いファイルが残る単純な上書きは避け、
元フォルダを退避して新しいものを同じ場所へ置きます。利用者が編集したファイルは退避してから扱います。
元フォルダが Git の作業コピーなら、変更のないことと登録されたブランチを確認して `git pull --ff-only` を使います。
固定 ref を勝手に最新ブランチへ変更しません。ローカル登録では `upgrade` だけで元フォルダは更新されません。

### 新しいコードで動作を確認する

インストール結果から新しい `<プラグイン>` を確認し、同じ Python で実行します。

```text
python3 "<プラグイン>/skills/quick-slide/scripts/quick_slide_start.py" --where
python3 "<プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py" --doctor
```

Windows は `py -3` を使います。診断はこのコピーの導入状態を確認するもので、
起動中のサーバが新しいコードになったことまでは証明しません。

サーバが古い版の場合は、新しい版の `quick_slide_start.py` が次回利用時に入れ替えます。
処理中の仕事があれば完了を待ちます。更新のために手で停止する必要はありません。
Codex の砂場から起動できない場合は、利用者のターミナルで新しい入口の `--start-server` を実行します。
デスクトップアプリを再起動し、新しい会話でスキルの認識と画面の表示を確認してください。

更新結果は「プラグイン更新済み」「サーバ入れ替え済み」「アプリで確認済み」を区別して報告します。
スライドの再生成や見本の入れ直しは更新のために必要ありません。

### 配布する側の準備

配布の手順は [リリースの手順](RELEASING.md) です。版は `tools/release.py prepare` で 3 か所を同じ新しい版へ上げます。
同じ版のまま中身だけ変更すると、利用者が反映を判断しにくくなります。
登録名と配布元を維持して、変更点と必要な追加依存ソフト・再起動の案内を添えます。
配布前には使い捨ての設定で旧版の導入から新版への更新を確認します。

参考: [Codex の公式プラグイン管理](https://developers.openai.com/plugins/build/plugins)、
[Claude の公式マーケットプレイス管理](https://code.claude.com/docs/en/plugin-marketplaces)。

## 開発側の確認

```text
claude plugin validate .
bash tests/run.sh
QUICK_SLIDE_FRESH_E2E=1 bash tests/run.sh
```

最後の試験は使い捨ての環境にコピーして導入から書き出しまで確認します。
描画用ブラウザをダウンロードしますが、本物の台本・画像生成の CLI は呼びません。
プラグインの実インストールとデスクトップアプリの確認は、上の実機項目で別に行います。
