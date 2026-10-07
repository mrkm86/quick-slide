"""画像の枠を Codex で埋めるときの、描く指示と鍵（ADR 0011）。Codex を呼ぶのは html_slide.py（codex_run の写し）。

- 絵の出どころは3つ: 持ち込み（台本の path・置き場 images/sNN-枠.png に置いた絵）／生成（prompt だけの枠。Codex で描く）／
  図解（型が HTML で組む。ここは関わらない）
- 生成した絵の置き場は `<台本のフォルダ>/images/gen-<鍵>.png`。鍵は描く指示（prompt・画風・色・キャラ・形）から作る。
  ページ番号を鍵に入れないので、ページを足しても並べ替えても絵は中身についていく。画風を変えると鍵が変わり、描き直す
  （前の画風の絵は残るので、戻すときは描かない）
- 画風（image_look）の3語と言い回しは image-slide と同じ（image-slide の styles.IMAGE_LOOKS が写し。テストで見比べる）
- キャラ（台本のページの `_characters`）は image-slide と同じ書き方。そのページの最初の生成の枠に参照画像として渡す
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

from deck_types import IMAGE_EXTS, DeckError

# 画風。台本の prompt には「何を描くか」だけを書き、画風はここから足す（資料の中で絵の調子をそろえる）
IMAGE_LOOKS = {
    "photo": "画風: 自然光の写実的な写真。実在の特定の人物・商品・ロゴに似せない",
    "illustration": "画風: やわらかい色の平らなイラスト（フラットデザイン）。輪郭はすっきり、影は控えめ",
    "handdrawn": "画風: 手描きのスケッチ。ペンや鉛筆の線に水彩の淡い色、白い紙の質感",
}
DEFAULT_LOOK = "illustration"
COLOR_LOOKS = ("illustration", "handdrawn")     # 色をテーマに寄せる画風（写真は寄せない。不自然な色かぶりになる）
MAX_REFS = 5                                     # Codex の画像ツールの上限（image-slide の ADR 0011）

# 直し（revise）。言い方は image-slide の prompt.py（quick-slide の _REVISION_LEADS と同じ）を、枠の絵に合わせて「ページ」を「絵」にした
REVISION_LEADS = {
    "minor": ("改稿（微修正）: 渡された現在の絵を土台にする。次の指示だけを反映し、"
              "指示に触れていない要素・配色・配置はそのまま保つ。"),
    "remake": ("改稿（作り直し）: 渡された参考画像は参考に留め、次の指示に沿ってこの絵を作り直す。"
               "参考画像をそのまま写さない。"),
}
REVISION_ONLY_IMAGES = "文章の指示は無い。渡された参考画像がそのまま指示である。"
REVISION_SCOPE = "指示に無い内容を足さない。"


def look_of(name: str | None) -> str:
    name = name or DEFAULT_LOOK
    if name not in IMAGE_LOOKS:
        raise DeckError(f"画風（image_look）は {' / '.join(IMAGE_LOOKS)} のどれか（{name!r}）")
    return name


def characters_of(raw, script_dir: pathlib.Path, no: int) -> list[dict]:
    """ページの `_characters`（[{name, image, pose}]）。image-slide の script_io.characters_of と同じ検査。"""
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise DeckError(f"{no}枚目: _characters は [{{name, image, pose}}] の配列で書く")
    out = []
    for i, c in enumerate(raw, 1):
        where = f"{no}枚目: _characters の {i} 人目"
        if not isinstance(c, dict):
            raise DeckError(f"{where}は {{name, image, pose}} で書く")
        extra = sorted(set(c) - {"name", "image", "pose"})
        if extra:
            raise DeckError(f"{where}に知らないキー: {', '.join(extra)}（使えるのは name・image・pose）")
        name, pose, image = c.get("name"), c.get("pose", ""), c.get("image")
        if not isinstance(name, str) or not name.strip():
            raise DeckError(f"{where}: name（誰か）が無い。参照画像の何枚目が誰かを結ぶのに使う")
        if not isinstance(pose, str):
            raise DeckError(f"{where}: pose は文字で書く")
        if not isinstance(image, str) or not image.strip():
            raise DeckError(f"{where}: image（絵のパス）が無い")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", image):
            raise DeckError(f"{where}: image に URL は使えない（ローカルのファイルのパスで書く）: {image}")
        q = pathlib.Path(image).expanduser()
        q = q if q.is_absolute() else script_dir / q
        if q.suffix.lower() not in IMAGE_EXTS:
            raise DeckError(f"{where}: image は {' / '.join(IMAGE_EXTS)} のどれか: {q.name}")
        if not q.is_file():
            raise DeckError(f"{where}: 絵が無い: {q}")
        out.append({"name": name.strip(), "pose": pose.strip(), "path": q.resolve()})
    return out


def character_words(chars: list[dict], first: int = 1) -> str:
    """参照画像の first 枚目から並ぶキャラの言い方。image-slide の character_words を、枠の絵に合わせて「ページ」を「絵」にした。"""
    lines = [f"添付の参照画像{i}は {c['name']}" + (f"（ポーズ・表情: {c['pose']}）" if c["pose"] else "")
             for i, c in enumerate(chars, first)]
    return ("\n".join(lines) + "\n上のキャラクターをこの絵に登場させる。造形・顔・髪・服・色・塗りは参照画像のまま描き"
            "（画風に合わせて描き直さない）、ポーズと表情は参照画像と添えた言葉に合わせる。"
            "名前は対応を示すためだけ。名前の文字を新しく書き足さない（参照画像に描かれている名札などはそのまま）")


def shape_of(w: float, h: float) -> str:
    """Codex に伝える絵の形（英語の外側の指示に入る）。寸法は指定できないので、向きと比だけ言う。"""
    r = w / h
    if r >= 1.2:
        return f"landscape, about {r:.2f}:1 (width:height)"
    if r <= 1 / 1.2:
        return f"portrait, about 1:{1 / r:.2f} (width:height)"
    return "square, about 1:1"


def text_of(prompt_full: str, look: str, pal: dict, chars: list[dict]) -> str:
    """生成の枠1つの描く指示（ページ番号は入れない。鍵がページについていかないように）。"""
    parts = [prompt_full, IMAGE_LOOKS[look] + (f"。主な色は {pal['primary']} と {pal['accent']} に寄せる"
                                               if look in COLOR_LOOKS else "") + "。"]
    if chars:
        parts.append(character_words(chars))
    return "\n".join(parts)


def _digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def key_of(text: str, shape: str, chars: list[dict]) -> str:
    """生成の絵の鍵。描く指示・形・キャラの絵の中身から作る（キャラの絵を差し替えたら描き直す）。"""
    src = json.dumps({"text": text, "shape": shape, "refs": [_digest(c["path"]) for c in chars]}, ensure_ascii=False)
    return hashlib.sha256(src.encode()).hexdigest()[:12]


def revision_part(mode: str, note: str, base_no: int | None = None, attach: tuple[int, int] | None = None) -> str:
    """描く指示の最後に足す直しの節（image-slide の prompt.revision_part と同じ組み立て）。"""
    parts = [REVISION_LEADS[mode]]
    roles = []
    if base_no:
        roles.append(f"添付の参照画像{base_no}は、いまのこの絵（土台）")
    if attach:
        a, b = attach
        roles.append(f"添付の参照画像{a}" + (f"〜{b}" if b > a else "") + "は、利用者が添えた参考画像")
    if roles:
        parts.append("渡された画像の役割: " + "。".join(roles) + "。")
    parts.append("利用者の指示:\n" + note.strip() if note and note.strip() else REVISION_ONLY_IMAGES)
    parts.append(REVISION_SCOPE)
    return "\n".join(parts)
