"""型の中に置くアイコン（台本の `icon: "名前"`）。ADR 0019・0019 の追記。

- 形は Material Symbols（Outlined・weight 400）の部分集合 100 種。実体は `skills/dads/icons/material-symbols.json`
  （名前 → {path, viewBox, symbol}・Apache License 2.0・出どころは同じフォルダの README.md と LICENSE-material-symbols.txt）
- **塗りの図形**（線ではない）。viewBox は "0 -960 960 960"。穴は path の巻き方向で作ってあり、1 つの path に全部の部分パスが入る
  （PPTX では 1 つの自由形のまま写り、穴も開く）。色は呼ぶ側が渡し、style の fill に書く（SVG の属性は var() を読まない）。stroke は使わない
- 名前は物の名前（英小文字とハイフン）。台本係はこの一覧から選び、合うものが無ければ書かない
- JSON はこのファイルの位置から辿る（エンジンは symlink 経由でも呼ばれる）。説明（日本語）は台本係が選ぶ手がかりで、ここに持つ
"""

from __future__ import annotations

import json
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "dads" / "icons" / "material-symbols.json"

# 名前 → 日本語の説明（台本係が選ぶ一覧になる。JSON の名前と過不足なく同じ）
DESCRIPTIONS: dict[str, str] = {
    "doc": "書類・原稿",
    "list": "一覧・手順",
    "checklist": "決まり・確かめること",
    "code": "プログラム",
    "chart": "数字・グラフ",
    "trend": "伸び・増える",
    "scissors": "切る・削る",
    "image": "写真・画像",
    "video": "動画・再生",
    "mic": "声・話す",
    "speaker": "音",
    "captions": "字幕・文字",
    "chat": "会話・相談",
    "mail": "連絡・メール",
    "sparkle": "強調・仕上げ・AI",
    "bulb": "思いつき・アイデア",
    "target": "目標・狙い",
    "flag": "決めたこと・到達点",
    "check": "できた・よい",
    "cross": "やめる・よくない",
    "alert": "注意・困りごと",
    "search": "探す・調べる",
    "folder": "フォルダ・置き場",
    "layers": "重ねる・仕組み",
    "gear": "設定・仕組み",
    "shield": "守る・安全",
    "lock": "鍵・閉じる",
    "link": "つなぐ",
    "refresh": "繰り返す・見直す",
    "upload": "入れる・出す",
    "download": "受け取る",
    "calendar": "日付・予定",
    "clock": "時間",
    "yen": "お金・費用",
    "user": "人・担当",
    "users": "人たち・チーム",
    "building": "会社・建物",
    "laptop": "パソコン・手元",
    "phone": "スマホ",
    "cloud": "外のサービス・クラウド",
    "book": "学ぶ・手引き",
    "pin": "場所",
    "star": "おすすめ・大事",
    "rocket": "始める・速く",
    "hub": "司令塔・まとめ役",
    "info": "情報・補足",
    "notice": "お知らせ・告知",
    "help": "疑問・ヘルプ",
    "error": "エラー・失敗",
    "done": "チェックの印だけ（丸なし）",
    "arrow-right": "次へ・進む",
    "arrow-left": "戻る",
    "arrow-up": "上・昇順",
    "arrow-down": "下・降順",
    "chevron-right": "リンクの強調・次",
    "chevron-left": "前",
    "chevron-down": "開く（アコーディオン・選択）",
    "chevron-up": "閉じる",
    "compare": "比べる",
    "trend-down": "減る・下がる",
    "add": "追加",
    "remove": "削除・引く",
    "close": "閉じる・×",
    "edit": "編集",
    "delete": "ごみ箱・削除",
    "copy": "複製",
    "external-link": "新しいタブで開く",
    "eye": "見る・表示",
    "filter": "絞り込み",
    "sort": "並べ替え",
    "menu": "メニュー",
    "more-vert": "縦三点（メニューを開く）",
    "home": "ホーム・トップ",
    "article": "記事・本文",
    "table": "表",
    "slideshow": "スライド・資料",
    "assignment": "課題・申請",
    "fact-check": "検証・照合",
    "database": "データ・保存",
    "pie": "割合・構成比",
    "analytics": "分析・気づき",
    "timer": "所要時間・計る",
    "hourglass": "待つ・処理中（動かない砂時計）",
    "history": "履歴・経過",
    "event": "催し・日程",
    "terminal": "コマンド・端末",
    "key": "鍵・認証",
    "bolt": "速い・電気・即時",
    "robot": "AI・ロボット",
    "brain": "考える・判断",
    "tree": "構造・分岐",
    "puzzle": "部品・拡張",
    "tool": "道具・直す",
    "speed": "速さ・性能",
    "school": "学ぶ・研修",
    "work": "仕事・業務",
    "handshake": "合意・連携",
    "globe": "世界・公開",
    "translate": "言語・翻訳",
    "award": "成果・表彰",
}


def _load() -> dict[str, dict[str, str]]:
    try:
        return json.loads(_SRC.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"アイコンの図形が読めない: {_SRC}（skills/dads が配られていない）") from exc


_SHAPES = _load()

# 名前 → (日本語の説明, 図形 {path, viewBox, symbol})
ICONS: dict[str, tuple[str, dict[str, str]]] = {k: (DESCRIPTIONS[k], v) for k, v in _SHAPES.items()}


def names() -> list[str]:
    return list(ICONS)


def svg(name: str, size: float, color: str) -> str:
    """アイコンの SVG（size px 四方）。名前が無ければ KeyError（呼ぶ側が台本の誤りとして止める）。"""
    shape = ICONS[name][1]
    return (f'<svg width="{size:g}" height="{size:g}" viewBox="{shape["viewBox"]}">'
            f'<path d="{shape["path"]}" style="fill:{color}"/></svg>')


def words() -> str:
    """台本の決まりと台本係に渡す一覧（名前＝説明）。"""
    return "・".join(f"{k}（{v[0]}）" for k, v in ICONS.items())
