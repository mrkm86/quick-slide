"""場面ごとの拍の絵のキャッシュ（ADR 0004）。motion_slide.py の deck / check が使う。

撮った拍の絵・その拍の検査の記録・一覧のサムネイルを、場面ごとの鍵（見た目が変わりうるものすべての SHA-256）の名前で置く。
鍵が同じ場面は Chrome を通さず、置いてあるものを使う。置き場の決め方・環境変数（MOTION_SLIDE_CACHE）・上限（設定の cache_max_mb）は
html-slide の cache.py・places.py と同じ（隣のスキルのものをそのまま使う）。出力フォルダごとの区画は切らず、鍵で引く（check と deck で絵を分け合える）。
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL.parent / "html-slide" / "scripts"))
import cache as C   # noqa: E402  置き場・上限の決め方（html-slide と同じ）
import oscompat as OS  # noqa: E402  置き換えの再試行（Windows）
import places as PL  # noqa: E402

NAME = "motion-slide"
FORMAT = 1               # 置くものの形を変えたら上げる（古い鍵は使われなくなる）
VIEWPORT = (1920, 1080)  # check.py が撮る大きさ
ENGINE_FILES = (SKILL / "engine" / "engine.js", SKILL / "engine" / "engine.css", HERE / "check.py")
DEFAULT_TERMS = 6        # engine.js の CFG.terms の既定（ここまでの言葉に出す数）


def _h(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def engine_version():
    """絵と検査の記録を決めるファイルの中身の SHA-256（エンジンの描き方・撮り方・検査が変わったら鍵が変わる）"""
    h = hashlib.sha256(str(FORMAT).encode())
    for f in ENGINE_FILES:
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def shared_inputs(data, index_html, wait):
    """全場面に効くもの。台本の共通部分・テーマの色・ロゴ・エンジンの版・撮るまでの待ち・撮る大きさ。
    テーマとロゴは、組み立てた index.html に実際に入ったものから取る（名前や相対パスの解き方に依らない）"""
    theme = re.search(r"<style>(:root\{[^}]*\})\n", index_html)
    logo = re.search(r'<script type="application/json" id="ms-logo">(.*?)</script>', index_html, re.S)
    return _h({"engine": engine_version(), "wait": int(wait), "viewport": VIEWPORT,
               "theme": theme.group(1) if theme else "", "logo": logo.group(1) if logo else "",
               "config": data.get("config") or {}, "style": data.get("style"), "stage": data.get("stage"),
               "script": data.get("script")})


def scene_keys(data, shared):
    """場面ごとの鍵。場面の JSON のほか、エンジンが隣の場面から計算して画面に出すもの
    （場面の番号と総数・拍の通し番号の位置と総拍数・ここまでの言葉・同じ章の場面の並び）を入れる"""
    scenes = data["scenes"]
    cfg = data.get("config") or {}
    keep = cfg.get("terms", DEFAULT_TERMS)
    total = sum(s["beats"] for s in scenes)
    keys, before = [], 0
    for i, s in enumerate(scenes):
        terms = [x["term"] for x in scenes[:i + 1] if x.get("term")]
        hid = max(0, len(terms) - keep)
        on = bool(cfg.get("chapters")) and not s.get("norail")   # 章のレールが出る場面だけ、同じ章の場面の並びが絵に効く
        rail = [x["title"] for x in scenes if on and x.get("ch") == s.get("ch") and not x.get("norail")]
        same = [x for x in scenes[:i] if on and x.get("ch") == s.get("ch") and not x.get("norail")]
        keys.append(_h({"shared": shared, "scene": s,
                        "ctx": {"i": i, "n": len(scenes), "before": before, "total": total,
                                "terms": terms[hid:], "hidden": hid, "rail": rail, "railpos": len(same)}}))
        before += s["beats"]
    return keys


def has_ng(item):
    """拍の記録に NG（JS のエラー・はみ出し・切れ）があるか。あれば置かない"""
    return bool(item["errors"] or item["outside"] or item["clipped"])


class Store:
    """<置き場>/scenes/<鍵>/ に 1 場面ぶん。beats/sNN-bM.png・items.json（拍ごとの検査の記録）・thumb.txt（一覧のサムネイル）"""

    def __init__(self, cfg=None):
        self.cfg = PL.load_config(NAME) if cfg is None else cfg
        self.base = C.root(NAME, self.cfg) / "scenes"
        self.used = set()

    def path(self, key):
        return self.base / key

    def get(self, key):
        """鍵の場面があればそのフォルダ。使った印に更新時刻を進める"""
        d = self.path(key)
        if not (d / "items.json").is_file():
            return None
        try:
            os.utime(d)
        except OSError:
            pass
        self.used.add(key)
        return d

    def items(self, key):
        return json.loads((self.path(key) / "items.json").read_text(encoding="utf-8"))

    def thumb(self, key):
        p = self.path(key) / "thumb.txt"
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def restore(self, key, beats_dir):
        """置いてある拍の絵を beats_dir に写す"""
        for png in sorted((self.path(key) / "beats").glob("*.png")):
            shutil.copyfile(png, pathlib.Path(beats_dir) / png.name)

    def put(self, key, beats_dir, items, thumb):
        """撮った場面を置く。途中で止まっても半端な区画が残らないよう、別名で作ってから名前を付ける"""
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix=".tmp-", dir=self.base))
        try:
            (tmp / "beats").mkdir()
            for it in items:
                shutil.copyfile(pathlib.Path(beats_dir) / f"{it['beat']}.png", tmp / "beats" / f"{it['beat']}.png")
            if thumb:
                (tmp / "thumb.txt").write_text(thumb, encoding="utf-8")
            (tmp / "items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            dst = self.path(key)
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            OS.replace(tmp, dst)   # Windows で拍の絵が一瞬つかまれて断られたら、待って試し直す
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.used.add(key)

    def trim(self, log=None):
        """合計が上限（設定の cache_max_mb・0 は消さない）を超えたら、最後に使った時刻の古い場面から消す。
        今回使った場面と、少し前に使われた場面（並行して走っている実行）は消さない"""
        cap = C.cap_bytes(self.cfg)
        if cap == 0 or not self.base.is_dir():
            return 0
        rows = [(d.stat().st_mtime, d, C.size_of(d)) for d in self.base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        total, removed, now = sum(r[2] for r in rows), 0, time.time()
        for used, d, size in sorted(rows, key=lambda r: r[0]):
            if total <= cap:
                break
            if d.name in self.used or now - used < C.RECENT:
                continue
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            removed += 1
        if removed and log:
            log(f"キャッシュの上限（{cap // (1024 * 1024)} MB）を超えたので、古い場面を {removed} 件消した")
        return removed
