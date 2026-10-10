"""html-video が焼いた解説動画（project/index.html ＋ 台本 ＋ build.json）→ 部品 JSON（互換の経路）。

extract.mjs --mode hyperframes の結果（単位ごとに GSAP で出る時刻 t と出方 kind）に、試作と同じ規則で段を割り当てる:
- 1文 = 1クリック。その文の show（出す要素）と marks（ペンの印）が、その文のクリックで出る。何も出さない文はクリックにしない
- 型が出すもの（見出し・ラベル・題・出典・前置き・章カードの文字）は step 0（画面に入ったら自動）
- 同じ段の中は、GSAP の時刻の差を 0.5 秒で頭打ちにして積んだ遅れ（ms）
- 印は台本の marks を順に m1, m2… と対応させる（html-video の plan_events と同じ数え方）:
  丸=wheel、下線・矢印=描いた向きの wipe、×・斜線・チェック・手書きの文字=wipe-right、ハンコ=fade、知らない種類=fade
- show の出し方: pop=pop、slide・grow=wipe-right、draw=fade。対象の中で自分も出方を持つ要素は、対象と同じ文でフェード
- 縦（portrait）の動画は扱わない（ValueError → render が rc=2）
- 見出しは画面をまたいでモーフさせる（!!heading）。外枠（ボード・桟・キャラ・演台）は extract が !! 付きで置く
- 絵の中の文字は、あとから出る箱の上に載っていれば、その箱と一緒に出す（箱の地に隠れないように）
- 切り替え: 全部フェード（モーフは退役・ADR 0023）
- ノート: 文ごとに1行。クリックのある文の頭に［クリック］

html-video 自体には触らない（焼いたものを読むだけ）。
"""
from __future__ import annotations

import json
from pathlib import Path

CLICK = "［クリック］"
MAX_GAP = 0.5           # 同じ段の中の遅れを積むときの、1つあたりの上限（秒）
MARK_FX = {"circle": "wheel", "cross": "wipe-right", "strike": "wipe-right", "check": "wipe-right",
           "write": "wipe-right", "stamp": "fade"}
SHOW_FX = {"pop": "pop", "slide": "wipe-right", "grow": "wipe-right", "draw": "fade"}


def dom_id(sid: str, name: str) -> str:
    """html-video の dom_id と同じ（`svg.<id>`・`bottom.left` の「.」は「-」）。"""
    return f"{sid}-{name.replace('.', '-')}"


def mark_kinds(script: dict) -> dict[int, tuple[int, int, str]]:
    """印の番号 n → (画面の番号, 文の番号, 種類)。台本の順に m1, m2…。"""
    out, n = {}, 0
    for si, sc in enumerate(script["scenes"]):
        for li, ln in enumerate(sc.get("lines") or []):
            for m in ln.get("marks") or []:
                n += 1
                out[n] = (si, li, m.get("kind"))
    return out


def draw_direction(prims: list[dict]) -> str:
    """印の最初の線（矢印なら胴）の始点 → 終点の向き。"""
    for p in prims:
        if p["type"] == "path" and p["subs"]:
            pts = [pt for sp in p["subs"] for pt in sp["pts"]]
            (x1, y1), (x2, y2) = pts[0], pts[-1]
            dx, dy = x2 - x1, y2 - y1
            if abs(dx) >= abs(dy):
                return "right" if dx >= 0 else "left"
            return "down" if dy >= 0 else "up"
    return "right"


def attach_texts(prims: list[dict]) -> None:
    """単位を持たない文字が、単位を持つ箱・円の上に載っていれば、その単位に入れる（外枠は除く）。"""
    for i, p in enumerate(prims):
        if p["type"] != "text" or p.get("unit"):
            continue
        cx, cy = p["rect"]["x"] + p["rect"]["w"] / 2, p["rect"]["y"] + p["rect"]["h"] / 2
        for q in reversed(prims[:i]):
            r, u = q.get("rect"), q.get("unit")
            if u and not u.startswith("frame-") and q["type"] in ("rect", "ellipse") and r \
                    and r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"]:
                p["unit"] = u
                break


def shown_ancestor(u: str, units: dict, show_line: dict) -> str | None:
    """u の外側の単位（extract が parent に入れる）をたどり、show の対象に当たればその単位。"""
    seen = set()
    p = (units.get(u) or {}).get("parent")
    while p and p not in seen:
        if p in show_line:
            return p
        seen.add(p)
        p = (units.get(p) or {}).get("parent")
    return None


def _delays(items: list[tuple[float, str]]) -> dict[str, int]:
    """[(時刻, 単位)] → 単位ごとの遅れ（ms）。差を MAX_GAP で頭打ちにして積む。"""
    out, d, prev = {}, 0.0, None
    for t, u in sorted(items, key=lambda x: (x[0] if x[0] is not None else 0, x[1])):
        if prev is not None:
            d += min(max((t or 0) - prev, 0), MAX_GAP)
        out[u] = int(round(d * 1000))
        prev = t or 0
    return out


def convert(parts: dict, script: dict, build: dict | None = None) -> dict:
    """extract --mode hyperframes の部品 JSON に、段・出方・遅れ・ノート・切り替えを入れる（parts をその場で書き換えて返す）。"""
    canvas = parts.get("canvas") or {}
    bc = (build or {}).get("canvas")
    if (build or {}).get("orientation", "landscape") != "landscape" or (bc and list(bc) != [1920, 1080]) \
            or (canvas and (canvas.get("w"), canvas.get("h")) != (1920, 1080)):
        raise ValueError("縦の動画は未対応（PPTX は 1920×1080 の横だけ。landscape の project を渡す）")
    slides, scenes = parts["slides"], script["scenes"]
    if len(slides) != len(scenes):
        raise ValueError(f"HTML の画面 {len(slides)} 枚と台本の画面 {len(scenes)} 枚が合わない（同じ台本で焼いた project か確かめる）")
    layouts = [sc.get("layout") for sc in scenes]
    if build and build.get("scenes"):
        layouts = [b.get("layout") or l for b, l in zip(build["scenes"], layouts)]
    mk = mark_kinds(script)
    blines = (build or {}).get("lines") or []
    for si, (s, sc) in enumerate(zip(slides, scenes)):
        sid = s["id"]
        lines = sc.get("lines") or []
        units, prims = s["units"], s["prims"]
        attach_texts(prims)
        # 文ごとに出すもの: show の対象 → 文、印の番号 → 文
        show_line = {}
        for li, ln in enumerate(lines):
            for ev in ln.get("show") or []:
                show_line[dom_id(sid, ev["target"])] = (li, ev.get("effect", "pop"))
        times = [b for b in blines if s.get("start") is not None and s["start"] - 1e-6 <= b["start"] < s["end"] - 1e-6]
        per_line: dict[int, list[tuple[float, str]]] = {}
        auto: list[tuple[float, str]] = []
        for u, info in units.items():
            if u.startswith("frame-") or info.get("t") is None:
                continue
            t, kind = info["t"], info.get("kind")
            li = None
            if kind == "mark":
                n = int(u.split("-")[0][1:])
                hit = mk.get(n)
                mprims = [p for p in prims if p.get("unit") == u]
                mkind = hit[2] if hit else None
                if mkind in ("underline", "arrow"):
                    info["fx"] = "wipe-" + draw_direction(mprims)
                else:
                    info["fx"] = MARK_FX.get(mkind, "fade")
                info["mark"] = mkind
                if hit and hit[0] == si:
                    li = hit[1]
            elif u in show_line:
                li, eff = show_line[u]
                info["fx"] = SHOW_FX.get(eff, "pop")
            elif shown_ancestor(u, units, show_line):
                # show の対象の中で、自分も出方を持つ要素（draw の中の塗りや文字など）: 対象と同じ文で、遅れてフェード
                li = show_line[shown_ancestor(u, units, show_line)][0]
                info["fx"] = "fade"
            else:
                # 型が出すもの
                info["fx"] = "rise" if kind in ("rise", "slide") else "fade"
                auto.append((t, u))
                continue
            if li is None:
                # 台本と対応が取れない（台本を直したあと焼き直していない等）: 時刻で文を決める
                li = 0
                for k, b in enumerate(times):
                    if t >= b["start"] - 0.25:
                        li = k
            per_line.setdefault(li, []).append((t, u))
        # 段: 何かを出す文だけを、文の順に 1, 2, …
        step_of = {li: k + 1 for k, li in enumerate(sorted(per_line))}
        for li, items in per_line.items():
            for u, d in _delays(items).items():
                units[u]["step"], units[u]["delay"] = step_of[li], d
        for u, d in _delays(auto).items():
            units[u]["step"], units[u]["delay"] = 0, d
        if f"{sid}-heading" in units:
            units[f"{sid}-heading"]["keep"] = "heading"
        # ノート・切り替え
        s["notes"] = "\n".join((CLICK if li in per_line else "") + ln.get("text", "") for li, ln in enumerate(lines))
        s["transition"] = "fade"
        s["layout"] = layouts[si]
    parts["title"] = script.get("title") or parts.get("title") or ""
    parts["mode"] = "hyperframes"
    return parts


def from_video(project: str | Path, script_path: str | Path, build_path: str | Path | None = None,
               out_json: str | Path | None = None) -> dict:
    """project（index.html のあるフォルダか index.html）＋台本＋build.json → 部品 JSON（out_json があれば書く）。"""
    import render
    parts = render.extract(project, mode="hyperframes")
    script = json.loads(Path(script_path).read_text())
    build = json.loads(Path(build_path).read_text()) if build_path else None
    convert(parts, script, build)
    if out_json:
        Path(out_json).write_text(json.dumps(parts, ensure_ascii=False))
    return parts


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 5:
        sys.exit("使い方: python3 from_video.py <project> <script.json> <build.json> <out.parts.json>")
    p = from_video(*sys.argv[1:5])
    for s in p["slides"]:
        steps = sorted({u["step"] for u in s["units"].values() if u.get("step")})
        print(f"{s['id']:<4} {s.get('layout') or '':<8} {s['transition']:<5} steps={len(steps)} notes={s['notes'].count(CLICK)}")
