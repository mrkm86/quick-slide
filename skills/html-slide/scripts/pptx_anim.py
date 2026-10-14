"""PPTX のアニメーション（p:timing）→ html-slide の段（data-step・data-fx・data-delay）。`pptx_import.py` が使う。

- クリックの組（主の順番 mainSeq の直下の par）ごとに 1 段。最初の組がクリックを待たずに始まるなら段 0（画面に入ったら出る）
- 組の中の「直前の動作と同時」「直前の動作の後」は、同じ段の data-delay（組の始まりからの ms）
- 写すのは開始の効果（entr）だけ。強調・終了・軌跡は写さない（報告に出す）。終了の部品は出たままになる
- 効果の種類は、HTML の決まりの 8 つ（rise・fade・pop・wipe-*・wheel）に寄せる
- 段落ごとの効果（spTgt/txEl/pRg）は、段落の要素に段を付ける。背景（spTgt/bg）は図形の段
"""
from __future__ import annotations

import ooxml as X
from ooxml import P

# presetID → 効果。wipe は向き（presetSubtype）で決める
FX_OF = {1: "fade", 10: "fade", 9: "fade", 2: "rise", 7: "rise", 12: "rise", 42: "rise", 47: "rise", 37: "rise",
         23: "pop", 53: "pop", 26: "pop", 17: "pop", 6: "pop", 15: "pop", 4: "fade", 21: "wheel", 20: "wheel",
         22: "wipe", 18: "wipe", 16: "fade", 3: "fade", 5: "fade", 8: "fade", 13: "fade", 14: "fade"}
WIPE_DIR = {1: "wipe-down", 2: "wipe-left", 4: "wipe-up", 8: "wipe-right"}


class Anims:
    def __init__(self):
        self.shapes: dict[str, dict] = {}          # 図形の id → {"step", "fx", "delay"}
        self.paragraphs: dict[str, dict[int, dict]] = {}   # 図形の id → 段落の番号 → 段
        self.notes: list[str] = []


def _delay(ctn) -> int | None:
    """cTn の始まりの遅れ（ms）。クリック待ち（indefinite）は None。"""
    c = X.q(ctn, "p:stCondLst/p:cond")
    if c is None:
        return 0
    d = c.get("delay")
    if d == "indefinite":
        return None
    try:
        return int(d or 0)
    except ValueError:
        return 0


def _effects(par, base: int):
    """par の下の効果の cTn と、組の始まりからの遅れ。"""
    ctn = X.q(par, "p:cTn")
    if ctn is None:
        return
    d = _delay(ctn) or 0
    if ctn.get("presetClass") is not None:
        yield ctn, base + d
        return
    for sub in X.qa(ctn, "p:childTnLst/p:par"):
        yield from _effects(sub, base + d)


def _targets(ctn):
    """効果の対象: [(図形の id, 段落の範囲 or None, 背景か)]。"""
    out = []
    seen = set()
    for tgt in ctn.iter(P + "spTgt"):
        spid = tgt.get("spid")
        rg = X.q(tgt, "p:txEl/p:pRg")
        bg = X.q(tgt, "p:bg") is not None
        key = (spid, None if rg is None else (rg.get("st"), rg.get("end")), bg)
        if key in seen:
            continue
        seen.add(key)
        out.append((spid, None if rg is None else (X.ival(rg, "st", 0), X.ival(rg, "end", 0)), bg))
    return out


def read(ctx, sld) -> Anims:
    an = Anims()
    if sld is None:
        return an
    timing = X.q(sld, "p:timing")
    if timing is None:
        return an
    seqs = [s for s in timing.iter(P + "seq")]
    main = None
    for s in seqs:
        ctn = X.q(s, "p:cTn")
        if ctn is not None and ctn.get("nodeType") == "mainSeq":
            main = ctn
        elif ctn is not None and ctn.get("nodeType") == "interactiveSeq":
            an.notes.append("図形を押すと始まる効果（トリガー）は写さない")
    if main is None:
        return an
    groups = X.qa(main, "p:childTnLst/p:par")
    step = 0
    skipped = set()
    for gi, g in enumerate(groups):
        gctn = X.q(g, "p:cTn")
        auto = _delay(gctn) is not None
        effects = list(_effects(g, 0))
        entr = [(c, d) for c, d in effects if c.get("presetClass") == "entr"]
        for c, _ in effects:
            cls = c.get("presetClass")
            if cls != "entr":
                skipped.add(cls)
        if not entr:
            continue
        this = 0 if (auto and step == 0 and gi == 0) else step + 1
        if this:
            step = this
        for c, d in entr:
            pid = X.ival(c, "presetID", 10)
            fx = FX_OF.get(pid)
            if fx is None:
                an.notes.append(f"開始の効果（番号 {pid}）はフェードにした")
                fx = "fade"
            if fx == "wipe":
                fx = WIPE_DIR.get(X.ival(c, "presetSubtype", 8), "wipe-right")
            if X.q(c, "p:iterate") is not None:
                an.notes.append("文字ごと・単語ごとの効果は、まとめて出すようにした")
            attrs = {"step": this, "fx": fx, "delay": max(0, d)}
            for spid, rg, bg in _targets(c):
                if not spid:
                    continue
                if rg is not None and not bg:
                    paras = an.paragraphs.setdefault(spid, {})
                    for i in range(rg[0], rg[1] + 1):
                        paras.setdefault(i, attrs)
                elif spid not in an.shapes:
                    an.shapes[spid] = attrs
    if "exit" in skipped:
        an.notes.append("終了の効果は写さない（部品は出たまま。あとから出る部品と重なることがある）")
    if "emph" in skipped:
        an.notes.append("強調の効果は写さない")
    if "path" in skipped:
        an.notes.append("軌跡の効果は写さない")
    if "verb" in skipped or "mediacall" in skipped:
        an.notes.append("動画・音の再生の合図は写さない")
    return an
