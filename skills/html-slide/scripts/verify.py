"""焼いた PPTX を開き直して照合する。

1. スライドごとに、クリックで出る段（nodeType="clickEffect"）の数 ＝ ノートの［クリック］の数
2. --odp: LibreOffice（soffice --headless --convert-to odp）で読ませ、各ページの効果の数（presentation:preset-id）が
   report（render が保存した build.json）の効果の数と一致するか。PowerPoint 以外のアプリでもアニメーションが
   読めているかの代わりの確かめ。soffice が無ければこの照合だけ飛ばす（警告）

    verify(pptx, odp=False, report=None) -> dict  … {"slides": [...], "ok": bool, "odp": {...} | None}
    python3 verify.py <deck.pptx> [--odp] [--report build.json]   rc: 0 合格 / 2 不一致 / 1 読めない
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CLICK = "［クリック］"


def _slide_names(z: zipfile.ZipFile) -> list[str]:
    """presentation.xml の並び順のスライド（ファイル名の番号順ではない）。"""
    pres = z.read("ppt/presentation.xml").decode()
    rels = z.read("ppt/_rels/presentation.xml.rels").decode()
    target = dict(re.findall(r'<Relationship [^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels))
    target.update({k: v for v, k in re.findall(r'<Relationship [^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
    ids = re.findall(r'<p:sldId [^>]*r:id="([^"]+)"', pres)
    return ["ppt/" + target[i].lstrip("/").replace("ppt/", "", 1) for i in ids]


def read_pptx(pptx) -> list[dict]:
    z = zipfile.ZipFile(pptx)
    out = []
    for no, name in enumerate(_slide_names(z), 1):
        s = z.read(name).decode()
        rel_name = name.replace("slides/", "slides/_rels/") + ".rels"
        rel = z.read(rel_name).decode() if rel_name in z.namelist() else ""
        nt = re.search(r'Target="\.\./notesSlides/(notesSlide\d+\.xml)"', rel)
        notes = ""
        if nt:
            notes = "".join(re.findall(r"<a:t>([^<]*)</a:t>", z.read("ppt/notesSlides/" + nt.group(1)).decode()))
        out.append({"no": no, "clicks": s.count('nodeType="clickEffect"'), "notes_clicks": notes.count(CLICK),
                    "effects": len(re.findall(r'presetClass="entr"', s)), "morph": "p159:morph" in s})   # モーフは退役（ADR 0023）。出ていたら NG
    return out


def odp_effects(pptx) -> list[int] | None:
    """LibreOffice で ODP にして、ページごとの効果の数。soffice が無ければ None。"""
    soffice = shutil.which("soffice") or ("/opt/homebrew/bin/soffice" if Path("/opt/homebrew/bin/soffice").exists() else None)
    if not soffice:
        return None
    with tempfile.TemporaryDirectory(prefix="html-slide-odp-") as tmp:
        # 別の LibreOffice が開いていてもぶつからないよう、使い捨てのプロファイルで動かす
        r = subprocess.run([soffice, f"-env:UserInstallation=file://{tmp}/profile", "--headless", "--convert-to", "odp",
                            "--outdir", tmp, str(pptx)], capture_output=True, text=True, timeout=300, encoding="utf-8")
        odp = Path(tmp) / (Path(pptx).stem + ".odp")
        if not odp.exists():
            raise RuntimeError(f"soffice が ODP を作れない: {r.stderr.strip()[-300:]}")
        content = zipfile.ZipFile(odp).read("content.xml").decode()
    pages = re.findall(r"<draw:page\b.*?</draw:page>", content, re.S)
    return [len(re.findall(r'presentation:preset-id="', p)) for p in pages]


def find_report(pptx: Path) -> Path | None:
    for p in (pptx.parent / "build.json", pptx.with_suffix(".build.json")):
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if d.get("renderer") == "html-slide" and Path(d.get("pptx", "")).name == pptx.name:
                return p
    return None


def verify(pptx, odp: bool = False, report=None) -> dict:
    pptx = Path(pptx)
    slides = read_pptx(pptx)
    for s in slides:
        s["ok"] = s["clicks"] == s["notes_clicks"] and not s["morph"]      # モーフは退役（ADR 0023）。出ていたら NG
    res = {"slides": slides, "ok": all(s["ok"] for s in slides), "odp": None}
    if odp:
        rp = Path(report) if report else find_report(pptx)
        rep = json.loads(rp.read_text(encoding="utf-8")) if rp else None
        counts = odp_effects(pptx)
        if counts is None:
            res["odp"] = {"skipped": "soffice が無い"}
        else:
            want = [s["effects"] for s in rep["slides"]] if rep else [s["effects"] for s in slides]
            diff = [i + 1 for i, (a, b) in enumerate(zip(counts, want)) if a != b]
            if len(counts) != len(want):
                diff.append(f"枚数 {len(counts)} ≠ {len(want)}")
            res["odp"] = {"report": str(rp) if rp else None, "odp": counts, "want": want, "diff": diff}
            res["ok"] = res["ok"] and not diff
    return res


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="PPTX のクリックとノートの照合（--odp で LibreOffice の読み取りも）")
    ap.add_argument("pptx")
    ap.add_argument("--odp", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    try:
        res = verify(a.pptx, a.odp, a.report)
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"NG: 読めない: {e}", file=sys.stderr)
        return 1
    o = res["odp"] or {}
    for i, s in enumerate(res["slides"]):
        odp = ""
        if o.get("odp") is not None:
            got = o["odp"][i] if i < len(o["odp"]) else None
            odp = f" odp={got}/{o['want'][i]}"
        print(f"slide{s['no']:>2} clicks={s['clicks']} notes={s['notes_clicks']} effects={s['effects']:>2}{odp} "
              f"morph={s['morph']} {'OK' if s['ok'] and (not odp or got == o['want'][i]) else 'NG'}")
    if o.get("skipped"):
        print(f"警告: ODP の照合を飛ばした（{o['skipped']}）", file=sys.stderr)
    elif o and not o.get("report"):
        print("警告: report（build.json）が見つからないので、PPTX の効果の数と比べた", file=sys.stderr)
    print("ALL OK" if res["ok"] else "NG")
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    sys.exit(main())
