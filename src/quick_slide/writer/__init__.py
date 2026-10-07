"""台本係。原稿・デッキの属性・モードから、エンジンの決まりに沿った台本（JSON）を LLM の CLI で書く。

- `prompt.py` — 指示文（決まりはエンジンの `references/` を実行時に読む）
- `backends.py` — `claude -p` / `codex exec` を 1 回呼ぶ・どちらで書くかを決める・JSON を取り出す
- 検査（エンジンの `check`）と書き直しの回数は `worker/pipeline.py` が回す
"""
