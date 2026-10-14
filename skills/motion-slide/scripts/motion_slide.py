"""場面ごとの台本（JSON）→ Motion Slide（HTML 1枚）。Quick Slide が html-slide と同じ扱いで呼べる入口。

    python3 -E motion_slide.py check --script <台本.json> [--theme <名前|YAML>] [--quick] [--strict] [--wait <ms>]
    python3 -E motion_slide.py deck  --script <台本.json> --out <フォルダ> [--theme …] [--logo <PNG>|none] [--logo-alt …]
                                     [--logo-height <px>] [--format png,thumbs] [--strict] [--wait <ms>] [--no-shoot]

check  台本の形を検査する → 組み立てて Chrome で全部の拍を開き、はみ出し・切れ・JS のエラーを数える。OK なら "OK"。--quick は形の検査だけ（Chrome を使わない）
deck   check と同じ検査のあと、--out に index.html（本体）・deck.html（組み立てた台本）・build.json・deck-info.json・
       slides/slide-NN.png（場面ごとの絵）・thumbs.png（一覧）・inspect.md・check/（拍ごとの画像と検査の記録）を書く

台本の形は references/deck-format.md。NG の行は stderr に `NG: N枚目（id s3）: …`（台本係が直せる言い方）。

--no-shoot（deck のみ）: 形の検査と index.html・deck.html・build.json・deck-info.json の組み立てだけ行い、Chrome を起動しない（1 秒前後）。
       slides/・thumbs.png・inspect.md・check/ は前の回のまま触らず、stdout に `pictures: not-taken` と出し、build.json の pictures.taken が false になる。
       一覧（Esc / O）のサムネイルは、同じ見た目の場面のキャッシュか前の index.html のものを使う
場面ごとのキャッシュ: 撮るとき（deck・check）、見た目が変わりうるものすべて（docs/adr/0004）が同じ場面は、前に撮った拍の絵・記録を使い、鍵が変わった場面だけ Chrome で撮る。
       置き場は MOTION_SLIDE_CACHE（無ければ OS のキャッシュの置き場）・上限は設定の cache_max_mb。stdout に `shots:` の行で撮った場面・使い回した場面の数
警告（重なり・泣き別れなど）は `警告: …`。--strict で警告も NG にする。
html-slide と同じ口で渡る引数（--style・--image-look・--logo-position・--watermark・--no-odp・--mode など）は受けて読み飛ばす。
終了コード: 0=成功 / 2=台本の誤り・検査の NG / 4=依存不足（Playwright・Pillow・PyYAML・Chrome）
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))   # 隣の deckjson を読む（どこから起こされても）
import build as buildmod  # noqa: E402
import deckjson  # noqa: E402
import shotcache  # noqa: E402

PY = sys.executable
MADE = ("deck.html", "index.html", "build.json", "deck-info.json", "inspect.md")
KINDS = {
    "NG JS": ("JS のエラー", "hook・script の書き方を確かめる"),
    "NG はみ出し": ("舞台（1920×1080）の外にはみ出している", "置く位置か大きさを直す"),
    "NG 切れ": ("文字が箱からあふれて切れている", "文字を短くする（字を小さくしない）"),
    "警告 重なり": ("文字が重なっている", "置き場所を離す。わざと重ねるなら data-overlap-ok"),
    "警告 道しるべと重なり": ("道しるべ（パンくず・拍の点・章のレール・言葉・ロゴ）と重なっている", "置く位置を避ける（上 0〜120・左下の章のレール・下 990〜）"),
    "警告 泣き別れ": ("1〜2字だけ次の行に残っている", "字数を整えて行をそろえる"),
    "警告 パッと出る": ("不透明度の動きが無く、ふわっと出ない", ".x の transition に opacity を入れる"),
}
LINE = re.compile(r"^s(\d+)-b(\d+) (\S+) (NG JS|NG はみ出し|NG 切れ|警告 重なり|警告 道しるべと重なり|警告 泣き別れ|警告 パッと出る): (.*)$")


def say(line):
    print(line, file=sys.stderr)


def lacking():
    """要る道具のうち足りないもの"""
    mods = {"playwright": "Playwright", "PIL": "Pillow", "yaml": "PyYAML"}
    return [name for mod, name in mods.items() if importlib.util.find_spec(mod) is None]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy(), encoding="utf-8")


def translate(report, scenes):
    """check.py の標準出力の行 → (NG の行, 警告の行)。同じ場面の同じ指摘は拍をまとめる"""
    seen, order = {}, []
    for ln in report.splitlines():
        m = LINE.match(ln.strip())
        if not m:
            continue
        no, beat, kind, msg = int(m.group(1)), int(m.group(2)), m.group(4), m.group(5)
        key = (no, kind, msg)
        if key not in seen:
            seen[key] = []
            order.append(key)
        seen[key].append(beat)
    ng, warn = [], []
    for no, kind, msg in order:
        beats = seen[(no, kind, msg)]
        what, fix = KINDS[kind]
        sid = scenes[no - 1]["id"] if 0 < no <= len(scenes) else "?"
        line = f"{no}枚目（id {sid}・拍{'・'.join(map(str, beats))}）: {what}: {msg}。{fix}"
        (ng if kind.startswith("NG") else warn).append(line)
    return ng, warn


def write_slides(out, scenes, check_dir):
    """場面ごとの絵 slides/slide-NN.png。check.py が撮った拍の絵から、その場面の thumb（無ければ最後）の拍を使う"""
    slides = out / "slides"
    slides.mkdir(parents=True, exist_ok=True)
    pngs = []
    for i, s in enumerate(scenes, 1):
        beat = s.get("thumb", s["beats"] - 1) + 1
        dst = slides / f"slide-{i:02d}.png"
        shutil.copyfile(check_dir / "beats" / f"s{i:02d}-b{beat}.png", dst)
        pngs.append(dst)
    for p in slides.glob("slide-[0-9][0-9].png"):   # 場面を減らしたとき、前の回の絵を残さない
        if int(p.stem.split("-")[1]) > len(scenes):
            p.unlink()
    return pngs


def write_thumbs(pngs, scenes, out, title):
    """一覧 thumbs.png（16場面を超えたら thumbs-01.png …）。作り方は html-slide と同じ（隣のスキルの outputs.py）"""
    mod = SKILL.parent / "html-slide" / "scripts"
    if not (mod / "outputs.py").is_file():
        say("警告: html-slide の outputs.py が無いので thumbs.png は作らない")
        return []
    sys.path.insert(0, str(mod))
    import outputs
    labels = [f"{s['title']}（{s['beats']}拍）" for s in scenes]
    return [str(p) for p in outputs.write_thumbs(pngs, labels, out, title)]


def pipeline(a, work, full):
    """検査から組み立てまで。work は deck.html などを置くフォルダ。戻り値は終了コード"""
    data, err = deckjson.load(a.script)
    if err:
        say("NG: " + err)
        return 2
    rep = deckjson.validate(data)
    for w in rep.warn:
        say("警告: " + w)
    if rep.ng:
        for n in rep.ng:
            say("NG: " + n)
        return 2
    if getattr(a, "quick", False):
        return 0
    no_shoot = full and getattr(a, "no_shoot", False)
    lack = [n for n in lacking() if not (no_shoot and n == "Playwright")]
    if lack:
        say(f"NG: {'・'.join(lack)}が要る（pip で入れる。Quick Slide の導入の確かめ --doctor も参照）")
        return 4
    scenes, title = data["scenes"], data.get("title") or pathlib.Path(a.script).stem
    work.mkdir(parents=True, exist_ok=True)
    deck, index, check_dir = work / "deck.html", work / "index.html", work / "check"
    if pathlib.Path(a.script).resolve() in {(work / n).resolve() for n in MADE}:
        say(f"NG: 台本 {a.script} が出力のファイル名と重なる（--out を別のフォルダにする）")
        return 2
    prev_thumbs = buildmod.read_thumbs(index)   # 組み直す前の一覧のサムネイル（--no-shoot で撮り直せない場面に使う）
    deck.write_text(deckjson.assemble(data), encoding="utf-8")

    # 一覧のサムネイルは build.py で撮らず（Chrome をもう1回起動することになる）、拍の絵から作って埋める
    build = [PY, "-E", str(HERE / "build.py"), str(deck), "--out", str(index), "--no-thumbs"]
    theme = a.theme or data.get("theme")
    if theme:
        build += ["--theme", _theme_arg(theme, a.script)]
    if a.logo:
        build += ["--logo", a.logo, "--logo-alt", a.logo_alt or "", "--logo-height", str(a.logo_height or 34)]
    r = run(build)
    if r.returncode != 0:
        for ln in (r.stderr or r.stdout).splitlines():
            if ln.startswith("NG:"):
                say("NG: 台本: " + ln[3:].strip())
        if r.returncode != 2:
            say(f"NG: 組み立て（build.py）が止まった: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
            return 4 if "playwright" in (r.stderr or "").lower() else 1
        return 2
    for ln in r.stderr.splitlines():
        if ln.startswith("警告"):
            say(ln)

    keys = shotcache.scene_keys(data, shotcache.shared_inputs(data, index.read_text(encoding="utf-8"), a.wait))
    store = shotcache.Store()
    rows = [{"no": i, "id": s["id"], "title": s["title"], "beats": s["beats"], "clicks": s["beats"] - 1,
             "page": f"slides/slide-{i:02d}.png"} for i, s in enumerate(scenes, 1)]
    head = {"engine": "motion-slide", "title": title, "scenes": len(scenes), "beats": sum(s["beats"] for s in scenes)}

    if no_shoot:   # 絵は撮らない。前の回の絵・一覧・検査はそのまま
        thumbs = {}
        for s, k in zip(scenes, keys):
            t = (store.get(k) and store.thumb(k)) or prev_thumbs.get(s["id"])
            if t:
                thumbs[s["id"]] = t
        buildmod.embed_thumbs(index, thumbs)
        have = lambda p: str(p) if p.exists() else None   # noqa: E731
        outputs = {"index": str(index), "deck": str(deck), "png": [str(p) for p in sorted((work / "slides").glob("slide-[0-9][0-9].png"))],
                   "thumbs": [str(p) for p in sorted(work.glob("thumbs*.png"))], "inspect": have(work / "inspect.md"), "check": have(check_dir)}
        record = {**head, "pictures": {"taken": False, "reason": "no-shoot"}, "check": None, "slides": rows, "outputs": outputs}
        for name in ("build.json", "deck-info.json"):
            (work / name).write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"index: {index}（{index.stat().st_size / 1024:.0f}KB）")
        print("pictures: not-taken（--no-shoot。slides/・thumbs.png・inspect.md・check/ は前の回のまま）")
        return 0

    # 拍の絵: 鍵が同じ場面はキャッシュから、変わった場面だけ Chrome で撮る
    shutil.rmtree(check_dir, ignore_errors=True)
    beats_dir = check_dir / "beats"
    beats_dir.mkdir(parents=True)
    reuse, hit, miss = [], {}, []
    for i, (s, k) in enumerate(zip(scenes, keys)):
        if not getattr(a, "no_cache", False) and store.get(k):
            try:
                store.restore(k, beats_dir)
                items = store.items(k)
                if len(items) != s["beats"] or not all((beats_dir / f"{it['beat']}.png").is_file() for it in items):
                    raise OSError("拍の数が合わない")
                reuse += items
                hit[i] = k
                continue
            except (OSError, ValueError, KeyError):
                pass   # 壊れた区画は撮り直す（上書きされる）
        miss.append(i)
    todo = ",".join(scenes[i]["id"] for i in miss)
    with tempfile.TemporaryDirectory(prefix="motion-slide-reuse-") as tmp:
        reuse_json = pathlib.Path(tmp) / "reuse.json"
        reuse_json.write_text(json.dumps(reuse, ensure_ascii=False), encoding="utf-8")
        cmd = [PY, "-E", str(HERE / "check.py"), str(index), "--out", str(check_dir), "--wait", str(a.wait),
               "--only", todo, "--reuse", str(reuse_json)]
        r = run(cmd)
    if r.returncode not in (0, 2):
        tail = (r.stderr or r.stdout).strip().splitlines()
        say(f"NG: 全拍の検査（check.py）が動かなかった: {tail[-1] if tail else ''}")
        return 4 if re.search(r"Executable doesn't exist|playwright install|ModuleNotFound", r.stderr) else 1
    ng, warn = translate(r.stdout, scenes)
    if a.strict:
        ng, warn = ng + warn, []
    for w in warn:
        say("警告: " + w)
    for n in ng:
        say("NG: " + n)
    bad = bool(ng)

    # 撮った場面をキャッシュへ（JS のエラー・はみ出し・切れがあった場面は置かない。偶然の失敗を居座らせない）。一覧のサムネイルもここで作る
    report = json.loads((check_dir / "check.json").read_text(encoding="utf-8"))
    thumbs = {}
    for i, s in enumerate(scenes):
        k = keys[i]
        png = beats_dir / f"s{i + 1:02d}-b{s.get('thumb', s['beats'] - 1) + 1}.png"
        thumb = store.thumb(k) if i in hit else None
        if full:   # check は一覧を使わないので作らない（deck が置き場から引いたとき、無ければ拍の絵から作る）
            thumb = thumb or buildmod.thumb_uri(png)
            thumbs[s["id"]] = thumb
        if i in miss:
            mine = [it for it in report if it["id"] == s["id"]]
            if len(mine) == s["beats"] and not any(shotcache.has_ng(it) for it in mine):
                store.put(k, beats_dir, mine, thumb)
    if full:
        buildmod.embed_thumbs(index, thumbs)
    store.trim(say)
    say(f"場面の絵: 撮った {len(miss)}・キャッシュから {len(hit)}")

    if full:
        pngs = write_slides(work, scenes, check_dir)
        fmts = {f.strip() for f in (a.format or "png,thumbs").split(",")}
        thumbs_png = write_thumbs(pngs, scenes, work, title) if "thumbs" in fmts or "png" in fmts else []
        shutil.copyfile(check_dir / "inspect.md", work / "inspect.md")
        outputs = {"index": str(index), "deck": str(deck), "png": [str(p) for p in pngs], "thumbs": thumbs_png,
                   "inspect": str(work / "inspect.md"), "check": str(check_dir)}
        record = {**head, "pictures": {"taken": True, "shot": len(miss), "reused": len(hit)},
                  "check": {"ng": len(ng), "warnings": len(warn)}, "slides": rows, "outputs": outputs}
        for name in ("build.json", "deck-info.json"):
            (work / name).write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"index: {index}（{index.stat().st_size / 1024:.0f}KB）")
        print(f"png: {work / 'slides'}（{len(pngs)}枚）")
        for t in thumbs_png:
            print(f"thumbs: {t}")
        print(f"shots: 撮った場面 {len(miss)}・キャッシュから {len(hit)}")
    return 2 if bad else 0


def _theme_arg(theme, script):   # テーマが YAML のパスなら、台本からの相対でも引けるように（名前はそのまま）
    p = pathlib.Path(str(theme)).expanduser()
    if not p.suffix or p.is_absolute() or p.exists():
        return str(p) if p.suffix else str(theme)
    near = pathlib.Path(script).resolve().parent / p
    return str(near if near.exists() else p)


def cmd_check(a):
    with tempfile.TemporaryDirectory(prefix="motion-slide-check-") as tmp:
        rc = pipeline(a, pathlib.Path(tmp), full=False)
    if rc == 0:
        print("OK")
    return rc


def cmd_deck(a):
    return pipeline(a, pathlib.Path(a.out).expanduser(), full=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="motion-slide — 場面ごとの台本（JSON）から Motion Slide を作る")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("deck", cmd_deck)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--script", required=True, help="場面ごとの台本（JSON）")
        p.add_argument("--theme", help="html-slide のテーマの名前か YAML（台本の theme より優先）")
        p.add_argument("--strict", action="store_true", help="警告も NG にする")
        p.add_argument("--wait", type=int, default=1800, help="拍を進めてから撮るまで（ms）")
        p.add_argument("--no-cache", action="store_true", help="場面ごとのキャッシュを読まず、全部撮り直す（撮ったものは置く）")
        p.add_argument("--logo")
        p.add_argument("--logo-alt")
        p.add_argument("--logo-height", type=int)
        p.add_argument("--format", help="作る出力（png,thumbs）。html-slide の pptx・pdf は作らない")
        for flag in ("--style", "--image-look", "--logo-position", "--watermark", "--logo-white", "--mode",
                     "--parallel", "--retries"):   # html-slide と同じ口で渡る引数。読み飛ばす
            p.add_argument(flag, help=argparse.SUPPRESS)
        for flag in ("--no-odp", "--no-fill", "--placeholder"):
            p.add_argument(flag, action="store_true", help=argparse.SUPPRESS)
        if name == "check":
            p.add_argument("--quick", action="store_true", help="形の検査だけ（Chrome を使わない）")
        else:
            p.add_argument("--out", required=True, help="出力のフォルダ")
            p.add_argument("--no-shoot", action="store_true", help="形の検査と組み立てだけ。Chrome を起動せず、絵・一覧・検査は前の回のまま")
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    sys.exit(main())
