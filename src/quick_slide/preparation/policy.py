"""素材準備の上限（§3.5 の初期値）。共通の 1 か所から読み、超えたときは理由を返して止まる（黙って切り詰めない）。

設定できる群は §3.3 の 3 群に絞る: (1) 1 操作の時間・準備全体の時間 (2) 1 操作の出力量 (3) 入力予算。
通常のファイル・フォルダは子プロセスを起こさないので、(1)(2) は使わない。
ここに持つのは (3) 入力予算と、準備係の答えを直させる回数。
"""

from __future__ import annotations

from dataclasses import dataclass

KIB = 1024
MIB = 1024 * KIB


@dataclass(frozen=True)
class Policy:
    guide_bytes: int = 256 * KIB          # 説明書き 1 つ
    text_total_bytes: int = 2 * MIB       # 読んだテキストの合計（説明書き・テキスト・JSON・YAML）
    listing_items: int = 5000             # フォルダ 1 つの目録（ファイルとフォルダの数）
    image_bytes: int = 20 * MIB           # 画像 1 枚
    image_pixels: int = 40_000_000        # 画像 1 枚の画素数（ヘッダで先に確かめる）
    selected_total_bytes: int = 200 * MIB  # 選んでコピーする画像の合計
    repair_attempts: int = 2              # 準備係の答えを直させる回数（初回＋2 回で合計 3 回）
    llm_prompt_bytes: int = 3 * MIB       # 準備係へ渡す指示文の上限（読んだものの合計＋目録の表示）
    writer_prompt_bytes: int = 3 * MIB    # 台本係へ渡す完成した指示文の上限（テンプレートの回だけ。原稿・原文・既存の台本を含む全体）


DEFAULT_POLICY = Policy()
