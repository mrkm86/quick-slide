"""台本の HTML に、エンジン・テーマの色・ロゴ・BGM を埋め込んで、HTML 1枚にする。

    python3 -I build.py <台本.html> [--out <出力.html>] [--theme default|forest|warm|<YAML>]
                        [--logo <PNG>|none] [--logo-alt <文字>] [--logo-height <px>] [--bgm <m4a|mp3>] [--no-thumbs]
    python3 -I build.py --print-out-root

一覧（Esc / O）のサムネイルは、組んだあとに Chrome で場面ごとに撮って埋め込む（1場面 約 10KB・Playwright が要る）。

--out を省くと台本と同じフォルダの index.html。テーマとロゴの既定は設定（環境変数 MOTION_SLIDE_CONFIG、無ければ <QUICK_SLIDE_HOME>/engines/motion-slide/config.yaml・既定の置き場は ~/.quick-slide）

台本の決まり（references/authoring.md）:
  - <head> に <script type="application/json" id="ms-config">（crumb・chapters など）
  - <body> の中に <div id="stage"> と、その中の <section class="scene" …>
  - 場面ごとの動きは、末尾の <script> で MS.hook("<場面 id>", (b, x) => {…})
出力は外へ何も取りに行かない（字体は端末のもの）。終了コード: 0=成功 / 2=台本の誤り
"""
import argparse, base64, colorsys, json, os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
THEMES = SKILL.parent / "html-slide" / "themes"   # 色の正本は html-slide のテーマ（隣のスキル。リンク越しでも resolve した実体から辿る）


def home():   # Quick Slide のデータの置き場（html-slide の places.py と同じ決め方）
    return pathlib.Path(os.environ.get("QUICK_SLIDE_HOME") or pathlib.Path.home() / ".quick-slide").expanduser()


def config_path():   # 機体ごとの既定（ロゴの置き場など）。無くても動く
    return pathlib.Path(os.environ.get("MOTION_SLIDE_CONFIG") or home() / "engines" / "motion-slide" / "config.yaml").expanduser()


def load_config():
    path = config_path()
    if not path.is_file():
        return {}
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def out_root(cfg=None):   # 出力の根っこ。設定の out:、無ければ <QUICK_SLIDE_HOME>/engines/motion-slide/output
    cfg = load_config() if cfg is None else cfg
    return pathlib.Path(str(cfg.get("out") or home() / "engines" / "motion-slide" / "output")).expanduser()


def fail(msg):
    print("NG: " + msg, file=sys.stderr); sys.exit(2)


def hex2rgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb2hex(c):
    return "#" + "".join("%02X" % round(max(0, min(1, v)) * 255) for v in c)


def mix(a, b, t):   # a を t だけ b に寄せる
    return rgb2hex(tuple(x + (y - x) * t for x, y in zip(hex2rgb(a), hex2rgb(b))))


def darker(h, by):
    hh, l, s = colorsys.rgb_to_hls(*hex2rgb(h)); return rgb2hex(colorsys.hls_to_rgb(hh, max(0, l - by), s))


def load_theme(name):
    import yaml
    p = pathlib.Path(name)
    if not p.suffix:
        p = THEMES / f"{name}.yaml"
    if not p.exists():
        fail(f"テーマが無い: {p}")
    t = yaml.safe_load(p.read_text(encoding="utf-8"))
    for k in ("background", "ink", "primary"):
        if k not in t:
            fail(f"テーマに {k} が無い: {p}")
    return t


def theme_css(t):
    bg, ink, pri = t["background"], t["ink"], t["primary"]
    acc = t.get("accent", pri)
    v = {"--bg": bg, "--ink": ink, "--pri": pri, "--pri-d": darker(pri, .10), "--pri-m": mix(pri, "#FFFFFF", .55),
         "--pri-l": mix(pri, "#FFFFFF", .88), "--sky": mix(pri, bg, .96), "--acc": acc}
    return ":root{" + ";".join(f"{k}:{c}" for k, c in v.items()) + "}"


def logo_json(path, alt, height):
    from PIL import Image
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        fail(f"ロゴが無い: {p}")
    im = Image.open(p)
    # ロゴの絵そのものは変えない（memory: ロゴは加工しない）。透明な余白の外側を「見せない」だけ
    bbox = im.convert("RGBA").getbbox() or (0, 0, im.width, im.height)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return {"src": f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode(), "size": [im.width, im.height],
            "crop": [bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]], "alt": alt, "height": height}


def thumb_uri(png):
    """拍の絵（PNG のパスかバイト列）→ 一覧のサムネイル（512×288 の WebP の data URI）"""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png) if isinstance(png, bytes) else png).convert("RGB").resize((512, 288), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "WEBP", quality=72)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def read_thumbs(path):
    """組んだ HTML に埋まっている一覧のサムネイル {場面 id: data URI}。無ければ {}"""
    p = pathlib.Path(path)
    if not p.is_file():
        return {}
    m = re.search(r'<script type="application/json" id="ms-thumbs">(.*?)</script>', p.read_text(encoding="utf-8"), re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def embed_thumbs(out, shots):
    """組んだ HTML に一覧のサムネイル {場面 id: data URI} を埋める（あれば置き換える）"""
    out = pathlib.Path(out)
    html = re.sub(r'<script type="application/json" id="ms-thumbs">.*?</script>\n', "", out.read_text(encoding="utf-8"), flags=re.S)
    if shots:
        tag = f'<script type="application/json" id="ms-thumbs">{json.dumps(shots)}</script>\n'
        html = html.replace("</head>", tag + "</head>", 1)
    out.write_text(html, encoding="utf-8")


def shoot_thumbs(out):
    """組んだ HTML を Chrome で開き、場面ごとに1枚撮って、一覧（Esc / O）のサムネイルにする。
    撮る拍は場面の data-thumb（0 始まり）、無ければ最後の拍。前の拍から1つ進めて撮る（飛ぶ動きも着いた後）"""
    import asyncio
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("警告: playwright が無いので一覧のサムネイルは付けない", file=sys.stderr)
        return {}

    async def run():
        shots = {}
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page(viewport={"width": 1920, "height": 1080})
            await pg.goto(pathlib.Path(out).resolve().as_uri()); await pg.wait_for_timeout(600)
            for i, sc in enumerate(await pg.evaluate("MS.state.scenes")):
                bt = sc["thumb"] if sc["thumb"] is not None else sc["beats"] - 1
                await pg.evaluate(f"MS.go({i},{max(0, bt - 1)})"); await pg.wait_for_timeout(120)
                if bt:
                    await pg.evaluate("MS.next()")
                await pg.wait_for_timeout(2200)
                shots[sc["id"]] = thumb_uri(await pg.screenshot())
            await b.close()
        return shots
    return asyncio.run(run())


def build(src, out, theme, logo, logo_alt, logo_height, bgm, thumbs=True):
    html = pathlib.Path(src).read_text(encoding="utf-8")
    if 'id="ms-config"' not in html:
        fail('台本に <script type="application/json" id="ms-config"> が無い')
    m = re.search(r'<script type="application/json" id="ms-config">(.*?)</script>', html, re.S)
    try:
        json.loads(m.group(1))
    except Exception as e:
        fail(f"ms-config が JSON でない: {e}")
    if '<div id="stage">' not in html:
        fail('台本に <div id="stage"> が無い')
    scenes = re.findall(r'<section class="scene[^"]*"([^>]*)>', html)
    if not scenes:
        fail('<section class="scene"> が1つも無い')
    ids = []
    for a in scenes:
        i = re.search(r'\bid="([^"]+)"', a); bts = re.search(r'data-beats="(\d+)"', a)
        if not i or not bts:
            fail(f"場面に id と data-beats が要る: <section{a}>")
        ids.append(i.group(1))
        th = re.search(r'data-thumb="(\d+)"', a)
        if th and int(th.group(1)) >= int(bts.group(1)):
            fail(f"{i.group(1)}: data-thumb={th.group(1)} は拍の外（0 始まり・data-beats={bts.group(1)}）")
        d = re.search(r'data-dur="([^"]*)"', a)
        if d and len([x for x in d.group(1).split(",") if x.strip()]) != int(bts.group(1)):
            fail(f"{i.group(1)}: data-dur の数（{d.group(1)}）が data-beats={bts.group(1)} と合わない")
    if len(set(ids)) != len(ids):
        fail(f"場面の id が重なっている: {ids}")
    for h in re.findall(r'MS\.hook\("([^"]+)"', html):
        if h not in ids:
            fail(f'MS.hook("{h}") の場面が無い')
    css = (SKILL / "engine/engine.css").read_text(encoding="utf-8")
    js = (SKILL / "engine/engine.js").read_text(encoding="utf-8")
    head = f"<style>{theme_css(theme)}\n{css}</style>\n"
    if logo:
        head += f'<script type="application/json" id="ms-logo">{json.dumps(logo_json(logo, logo_alt, logo_height))}</script>\n'
    head += f"<script>{js}</script>\n"
    # エンジンは台本の <style> より前に置く（台本が上書きできる）
    html = re.sub(r"(<head[^>]*>)", lambda m_: m_.group(1) + "\n" + head, html, count=1)
    if bgm:
        b = pathlib.Path(bgm).expanduser()
        mime = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"}.get(b.suffix.lower())
        if not mime:
            fail("BGM は m4a / mp3 / ogg")
        tag = f'<audio id="bgm" preload="auto" src="data:{mime};base64,{base64.b64encode(b.read_bytes()).decode()}"></audio>'
        html = html.replace('<div id="stage">', tag + '\n<div id="stage">', 1)
    if re.search(r'(src|href)="https?://', html):
        print("警告: 外の URL を読みに行く src/href がある（1枚で完結しない）", file=sys.stderr)
    out = pathlib.Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    if thumbs:
        shots = shoot_thumbs(out)
        if shots:
            embed_thumbs(out, shots)
    beats = sum(int(re.search(r'data-beats="(\d+)"', a).group(1)) for a in scenes)
    print(f"{out}  {len(scenes)} 場面・{beats} 拍・{out.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932）でなく UTF-8 で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("--print-out-root", action="store_true", help="出力の根っこ（設定の out:・無ければ既定）を出して終わる")
    ap.add_argument("--out")
    cfg = load_config()
    ap.add_argument("--theme", default=cfg.get("theme", "default"))
    ap.add_argument("--logo", default=cfg.get("logo"), help="既定は config の logo。付けないときは --logo none")
    ap.add_argument("--logo-alt", default=cfg.get("logo_alt", ""))
    ap.add_argument("--logo-height", type=int, default=cfg.get("logo_height", 34))
    ap.add_argument("--bgm")
    ap.add_argument("--no-thumbs", action="store_true", help="一覧のサムネイルを撮らない（速い。試し組み用）")
    a = ap.parse_args()
    if a.print_out_root:
        print(out_root(cfg)); sys.exit(0)
    if not a.src:
        ap.error("台本の HTML を渡す")
    out = a.out or str(pathlib.Path(a.src).with_name("index.html"))
    if pathlib.Path(out).resolve() == pathlib.Path(a.src).resolve():
        fail("--out が台本と同じファイル")
    build(a.src, out, load_theme(a.theme), None if a.logo in (None, "none") else a.logo, a.logo_alt, a.logo_height, a.bgm, not a.no_thumbs)
