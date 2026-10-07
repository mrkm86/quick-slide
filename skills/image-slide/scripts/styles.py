"""スタイル（ADR 0007）。画風・字体・余白の調子を、描く指示の「スタイル」の節として渡す。

組み込みは3つ。standard・visual は html-slide と同じ名前。既定は visual（ピッチモードのエンジンなので）。
infographic は image-slide だけのもので、台本の全ページを1枚に凝縮する（one_page）。
カスタムスタイルは YAML のファイルで持ち込む（`--style <yaml>`）。ファイルはエンジンの外（呼ぶ側のスキル・自分のフォルダ）に置く。

    name: weekly                         # 必須
    description: 社内向け週報のインフォグラフィック   # 任意。選ぶ人向けの補足
    one_page: true                       # 任意。true なら台本の全ページを1枚に凝縮する（infographic と同じ）
    bleed: true                          # 任意。true なら本文のページも全面に描かせ、下端に無地の帯を作らない（visual と同じ）
    image_look: illustration             # 任意。絵の部分（写真・挿絵）の画風（photo・illustration・handdrawn。--image-look が勝つ）
    text: |                              # 必須。描く指示に入れるスタイルの文章
      フラットイラストのインフォグラフィック。写真・リアルな画像は使わない…

レイアウト（構図）は別の引数（`--layout`・layouts.py）。色はブランド（`--theme`）。素材は `--ref`・`--logo`。
束ねたいときは、呼ぶ側のスキルが組み合わせて渡す（ADR 0007 で「デザイン」のような束は作らないと決めた）。
"""
from __future__ import annotations

import pathlib

import script_io as S

# standard: 説明・報告・社内資料向け（前の名前は business）
STANDARD = ("見出しを大きく、1ページ1主張。読みやすい日本語のゴシック体。図解・アイコン・イラストを使って、"
            "ひと目で伝わる絵にする。余白をしっかり取り、要素を詰め込まない")
# visual: ビジュアル寄り。プレゼン・ピッチ向け（前の名前は pitch）。
# 本文のページも全面に描かせる（bleed）。全面の絵の下に白い帯が残ると見栄えが落ちた（2026-10-06）
# 「描く文字は一字一句すべて描く」は条項が優先するので、ここでは主役以外を控えめにする、と言う
VISUAL = ("登壇・ピッチ向けの印象に残る1枚。画面の主役を1つに絞り、大胆に見せる（大きなビジュアル・大きな数字・大きな言葉のどれか）。"
          "主役のビジュアルは画面の6〜8割を使ってよい。与えられた文字はすべて描くが、主役以外は小さく控えめに置く。"
          "等分割のカードや網羅的な箇条書きの並びは避け、余白を大きく取る。写真調・イラスト・抽象的なグラフィックで、"
          "見た人の記憶に残る絵にする。読みやすい日本語のゴシック体")

# infographic: 資料全体を1枚に凝縮したインフォグラフィック（2026-10-05 に追加）。
# このスタイルだけは枚数が変わる（台本の全ページを区画として1枚に描く・one_page）。週報はこれにカスタムレイアウトを足した特化版。
# 「1枚を最初から設計する」言い方は、5枚を区画として並べさせるより、ポスターらしい配置になった（2026-10-05 の試し・ADR 0007 追記）
INFOGRAPHIC = ("1枚のインフォグラフィックのポスターを、最初から設計する。資料全体の中身を材料として読み、"
               "一番言いたいこと（主張・気づき・結論）を中心に、材料どうしの関係が伝わる1枚の構成を自分で考える。"
               "材料の順番や数にとらわれず、まとめる・つなぐ・大小をつけるのは自由。"
               "同じ大きさの枠を並べた、スライドを並べたような見た目にはしない。"
               "図解・アイコン・フラットなイラスト・短い文で、読んで分かる1枚にする。写真は控えめにする。"
               "文字は多くなるので、読める大きさを守る。読みやすい日本語のゴシック体")

BUILTIN = {
    "standard": {"name": "standard", "description": "説明・報告・社内資料向け。図解とイラストで、文字と図を整理して並べる",
                 "text": STANDARD, "path": None, "one_page": False, "bleed": False},
    "visual": {"name": "visual", "description": "ビジュアル寄り。プレゼン・ピッチ向けで、主役を1つに絞り、大きな画像で大胆に見せる",
               "text": VISUAL, "path": None, "one_page": False, "bleed": True},
    "infographic": {"name": "infographic", "description": "資料全体を1枚に凝縮したインフォグラフィックのポスター（台本の全ページが1枚になる）",
                    "text": INFOGRAPHIC, "path": None, "one_page": True, "bleed": False},
}
DEFAULT = "visual"
FIELDS = {"name", "description", "text", "one_page", "bleed", "image_look"}

# 絵の部分の画風（--image-look・スタイルの image_look）。html-slide の imagegen.IMAGE_LOOKS の写し（テストで見比べる）。
# 指定しなければ足さない（画風はスタイルの文章のまま）。html-slide では Codex で描く画像の枠の画風になる
IMAGE_LOOKS = {
    "photo": "画風: 自然光の写実的な写真。実在の特定の人物・商品・ロゴに似せない",
    "illustration": "画風: やわらかい色の平らなイラスト（フラットデザイン）。輪郭はすっきり、影は控えめ",
    "handdrawn": "画風: 手描きのスケッチ。ペンや鉛筆の線に水彩の淡い色、白い紙の質感",
}
RENAMED = {"pitch": "visual", "business": "standard"}


def load(spec: str | None) -> dict:
    """--style の値（standard・visual・YAML のパス）から {name, description, text, path}。誤りは rc=2。"""
    spec = spec or DEFAULT
    if spec in BUILTIN:
        return dict(BUILTIN[spec])
    if spec in RENAMED:
        raise S.ScriptError(f"スタイル {spec} は {RENAMED[spec]} に名前が変わった（ADR 0007）")
    path = pathlib.Path(spec).expanduser()
    if not path.suffix.lower() in (".yaml", ".yml"):
        raise S.ScriptError(f"スタイルは standard・visual か、カスタムスタイルの YAML のパス（{spec!r}）")
    path = path.resolve()
    if not path.is_file():
        raise S.ScriptError(f"カスタムスタイルのファイルが無い（--style）: {path}")
    import yaml
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise S.ScriptError(f"{path}: スタイルの YAML が読めない（{e}）")
    if not isinstance(d, dict):
        raise S.ScriptError(f"{path}: スタイルは name・description・text のキーで書く")
    extra = [k for k in d if k not in FIELDS]
    if extra:
        raise S.ScriptError(f"{path}: スタイルに知らないキー: {', '.join(map(repr, extra))}（使えるのは {', '.join(sorted(FIELDS))}）")
    for k in ("name", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise S.ScriptError(f"{path}: {k} が無い（文字で書く）")
    if d.get("description") is not None and not isinstance(d["description"], str):
        raise S.ScriptError(f"{path}: description は文字で書く")
    for k in ("one_page", "bleed"):
        if d.get(k) is not None and not isinstance(d[k], bool):
            raise S.ScriptError(f"{path}: {k} は true か false")
    if d.get("image_look") is not None and d["image_look"] not in IMAGE_LOOKS:
        raise S.ScriptError(f"{path}: image_look は {' / '.join(IMAGE_LOOKS)} のどれか（{d['image_look']!r}）")
    return {"name": d["name"].strip(), "description": (d.get("description") or "").strip(), "text": d["text"].strip(),
            "path": path, "one_page": bool(d.get("one_page")), "bleed": bool(d.get("bleed")), "image_look": d.get("image_look")}


def prompt_part(style: dict, colors: str, note: str = "") -> str:
    """描く指示の「スタイル」の節。"""
    head = f"スタイル「{style['name']}」" + (f"（{style['description']}）" if style["description"] else "") + "（条項の範囲で適用）:"
    s = f"{head}\n{style['text'].rstrip('。')}。{colors}"
    if style.get("image_look"):
        s += f"\n絵の部分（写真・挿絵・背景の絵）の{IMAGE_LOOKS[style['image_look']]}。"
    if note.strip():
        s += f"\nスタイルへの追加の注文: {note.strip()}"
    return s
