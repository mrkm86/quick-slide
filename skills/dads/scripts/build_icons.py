#!/usr/bin/env python3
"""icons/material-symbols.json を作り直す（標準ライブラリだけ）。

Material Symbols（Outlined・weight 400・fill 0・grade 0）の SVG を、npm パッケージ
@material-symbols/svg-400 の**固定した版**から取り、使う分だけを 名前 → {path, viewBox} にして書く。

    python3 skills/dads/scripts/build_icons.py            # 取得して icons/material-symbols.json を書く
    python3 skills/dads/scripts/build_icons.py --table    # 対応表（Markdown）を出す（icons/README.md に貼る）
    python3 skills/dads/scripts/build_icons.py --check    # JSON の中身が MAP と合っているか（通信しない）

- 名前は台本で使う名前（英小文字とハイフン）。左の 45 種は html-slide の deck_icons.py の名前を意味ごと引き継ぐ
- 版を上げるときは VERSION を替えて作り直し、icons/README.md の版を合わせる
- 取った SVG は 1 つの <path> だけを持つ前提で、そうでなければ止まる（部品を黙って落とさない）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

VERSION = "0.47.6"
BASE = f"https://cdn.jsdelivr.net/npm/@material-symbols/svg-400@{VERSION}"
ICONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "icons"
OUT = ICONS_DIR / "material-symbols.json"

# 台本の名前 → (Material Symbols の名前, 使いどころ)。「引き継ぎ」は deck_icons.py の 45 種
INHERITED: dict[str, tuple[str, str]] = {
    "doc": ("description", "書類・原稿"),
    "list": ("format_list_bulleted", "一覧・手順"),
    "checklist": ("checklist", "決まり・確かめること"),
    "code": ("code", "プログラム"),
    "chart": ("bar_chart", "数字・グラフ"),
    "trend": ("trending_up", "伸び・増える"),
    "scissors": ("content_cut", "切る・削る"),
    "image": ("image", "写真・画像"),
    "video": ("play_circle", "動画・再生"),
    "mic": ("mic", "声・話す"),
    "speaker": ("volume_up", "音"),
    "captions": ("closed_caption", "字幕・文字"),
    "chat": ("chat", "会話・相談"),
    "mail": ("mail", "連絡・メール"),
    "sparkle": ("wand_stars", "強調・仕上げ・AI"),
    "bulb": ("lightbulb", "思いつき・アイデア"),
    "target": ("target", "目標・狙い"),
    "flag": ("flag", "決めたこと・到達点"),
    "check": ("check_circle", "できた・よい"),
    "cross": ("cancel", "やめる・よくない"),
    "alert": ("warning", "注意・困りごと"),
    "search": ("search", "探す・調べる"),
    "folder": ("folder", "フォルダ・置き場"),
    "layers": ("layers", "重ねる・仕組み"),
    "gear": ("settings", "設定・仕組み"),
    "shield": ("verified_user", "守る・安全"),
    "lock": ("lock", "鍵・閉じる"),
    "link": ("link", "つなぐ"),
    "refresh": ("refresh", "繰り返す・見直す"),
    "upload": ("upload", "入れる・出す"),
    "download": ("download", "受け取る"),
    "calendar": ("calendar_month", "日付・予定"),
    "clock": ("schedule", "時間"),
    "yen": ("currency_yen", "お金・費用"),
    "user": ("person", "人・担当"),
    "users": ("group", "人たち・チーム"),
    "building": ("apartment", "会社・建物"),
    "laptop": ("laptop_mac", "パソコン・手元"),
    "phone": ("mobile", "スマホ"),
    "cloud": ("cloud", "外のサービス・クラウド"),
    "book": ("menu_book", "学ぶ・手引き"),
    "pin": ("location_on", "場所"),
    "star": ("star", "おすすめ・大事"),
    "rocket": ("rocket_launch", "始める・速く"),
    "hub": ("hub", "司令塔・まとめ役"),
}

# 足した名前。注釈・成功・エラー・警告・情報・矢印・開閉・時間・文書・仕組みなど、スライドと画面でよく使うもの
ADDED: dict[str, tuple[str, str]] = {
    # 注釈・状態（notice-block・notification-banner・チップラベルと組む）
    "info": ("info", "情報・補足"),
    "notice": ("campaign", "お知らせ・告知"),
    "help": ("help", "疑問・ヘルプ"),
    "error": ("error", "エラー・失敗"),
    "done": ("check", "チェックの印だけ（丸なし）"),
    # 矢印・開閉
    "arrow-right": ("arrow_forward", "次へ・進む"),
    "arrow-left": ("arrow_back", "戻る"),
    "arrow-up": ("arrow_upward", "上・昇順"),
    "arrow-down": ("arrow_downward", "下・降順"),
    "chevron-right": ("chevron_right", "リンクの強調・次"),
    "chevron-left": ("chevron_left", "前"),
    "chevron-down": ("keyboard_arrow_down", "開く（アコーディオン・選択）"),
    "chevron-up": ("keyboard_arrow_up", "閉じる"),
    "compare": ("compare_arrows", "比べる"),
    "trend-down": ("trending_down", "減る・下がる"),
    # 操作
    "add": ("add", "追加"),
    "remove": ("remove", "削除・引く"),
    "close": ("close", "閉じる・×"),
    "edit": ("edit", "編集"),
    "delete": ("delete", "ごみ箱・削除"),
    "copy": ("content_copy", "複製"),
    "external-link": ("open_in_new", "新しいタブで開く"),
    "eye": ("visibility", "見る・表示"),
    "filter": ("filter_list", "絞り込み"),
    "sort": ("sort", "並べ替え"),
    "menu": ("menu", "メニュー"),
    "more-vert": ("more_vert", "縦三点（メニューを開く）"),
    "home": ("home", "ホーム・トップ"),
    # 文書・データ
    "article": ("article", "記事・本文"),
    "table": ("table_chart", "表"),
    "slideshow": ("slideshow", "スライド・資料"),
    "assignment": ("assignment", "課題・申請"),
    "fact-check": ("fact_check", "検証・照合"),
    "database": ("database", "データ・保存"),
    "pie": ("pie_chart", "割合・構成比"),
    "analytics": ("analytics", "分析・気づき"),
    # 時間
    "timer": ("timer", "所要時間・計る"),
    "hourglass": ("hourglass_empty", "待つ・処理中（動かない砂時計）"),
    "history": ("history", "履歴・経過"),
    "event": ("event", "催し・日程"),
    # 仕組み・技術
    "terminal": ("terminal", "コマンド・端末"),
    "key": ("key", "鍵・認証"),
    "bolt": ("bolt", "速い・電気・即時"),
    "robot": ("smart_toy", "AI・ロボット"),
    "brain": ("psychology", "考える・判断"),
    "tree": ("account_tree", "構造・分岐"),
    "puzzle": ("extension", "部品・拡張"),
    "tool": ("build", "道具・直す"),
    "speed": ("speed", "速さ・性能"),
    # 人・組織・場
    "school": ("school", "学ぶ・研修"),
    "work": ("work", "仕事・業務"),
    "handshake": ("handshake", "合意・連携"),
    "globe": ("public", "世界・公開"),
    "translate": ("translate", "言語・翻訳"),
    "award": ("trophy", "成果・表彰"),
}

MAP: dict[str, tuple[str, str]] = {**INHERITED, **ADDED}


def _fetch(symbol: str) -> str:
    url = f"{BASE}/outlined/{symbol}.svg"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310（固定の https の URL）
        return r.read().decode("utf-8")


def _parse(symbol: str, svg: str) -> dict[str, str]:
    paths = re.findall(r'<path\b[^>]*\bd="([^"]*)"', svg)
    box = re.search(r'viewBox="([^"]*)"', svg)
    if len(paths) != 1 or not box:
        raise SystemExit(f"{symbol}: <path> が 1 つではない（{len(paths)} 個）か viewBox が無い")
    return {"path": paths[0], "viewBox": box.group(1)}


def build() -> dict[str, dict[str, str]]:
    symbols = sorted({s for s, _ in MAP.values()})
    with ThreadPoolExecutor(max_workers=8) as ex:
        svgs = dict(zip(symbols, ex.map(_fetch, symbols)))
    out: dict[str, dict[str, str]] = {}
    for name, (symbol, _) in MAP.items():
        out[name] = {**_parse(symbol, svgs[symbol]), "symbol": symbol}
    return out


def table() -> str:
    rows = ["| 名前 | Material Symbols | 使いどころ | 出どころ |", "|---|---|---|---|"]
    for name, (symbol, note) in MAP.items():
        src = "deck_icons.py から引き継ぎ" if name in INHERITED else "追加"
        rows.append(f"| `{name}` | `{symbol}` | {note} | {src} |")
    return "\n".join(rows)


def check() -> int:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    bad = []
    if set(data) != set(MAP):
        bad.append(f"名前の差: {sorted(set(data) ^ set(MAP))}")
    for name, (symbol, _) in MAP.items():
        e = data.get(name)
        if e and (e.get("symbol") != symbol or not e.get("path") or e.get("viewBox") != "0 -960 960 960"):
            bad.append(f"{name}: 中身が MAP と合わない")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"OK: {len(data)} 種（版 {VERSION}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="store_true", help="対応表（Markdown）を出す")
    ap.add_argument("--check", action="store_true", help="JSON が MAP と合っているか（通信しない）")
    a = ap.parse_args()
    if a.table:
        print(table())
        return 0
    if a.check:
        return check()
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(data)} 種（Material Symbols {len({s for s, _ in MAP.values()})} 個・版 {VERSION}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
