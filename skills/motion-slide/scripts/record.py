"""組んだ HTML の自動再生を、仮想の時計で1コマずつ撮って mp4 にする（効果音と BGM も時刻どおりに重ねる）。

    python3 -E record.py <index.html> [--out <video.mp4>] [--fps 30] [--start 1.1] [--seconds N] [--crf 18]
                         [--no-sfx] [--no-bgm] [--keep-ctrl]

- 時計は scripts/vclock.js をページより先に差し込む。タイマー・rAF・performance.now を差し替え、CSS の動き（Web Animations）を
  止めて時刻を手で合わせるので、台本を変えずにコマ落ちなく撮れる（Chrome の仮想時間は CSS の動きに効かなかった・ADR 0002）
- 撮る長さは既定で、--start の拍から終わりまで（自動再生の時刻表）＋ 1.5 秒
- 効果音はページが鳴らした音の記録（MS.state.sfx）を、MS.renderSfx で作った同じ音で並べる。BGM は HTML の <audio id="bgm"> を
  時刻表の位置から重ねる（音量は ms-config の bgmVolume・既定 0.55）
- 操作ボタン（一時停止・音あり）は隠して撮る（--keep-ctrl で残す）
- 実時間は動画の長さの約 3.5 倍かかる（3分の動画で約12分・2026-10-06 実測）
出力: <out>（既定は HTML と同じフォルダの video.mp4）。終了コード: 0=成功 / 2=引数の誤り / 4=依存不足
"""
import argparse, asyncio, base64, json, pathlib, re, shutil, subprocess, sys, tempfile, time, wave

HERE = pathlib.Path(__file__).resolve().parent
SR = 44100


def fail(msg, rc=2):
    print("NG: " + msg, file=sys.stderr); sys.exit(rc)


async def shoot(a, html, video):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1920, "height": 1080})
        await pg.add_init_script(path=str(HERE / "vclock.js"))
        await pg.goto(html.as_uri())
        await pg.evaluate("__vt.advance(1000)")
        if not a.keep_ctrl:
            await pg.add_style_tag(content="#ms-ctrl{display:none!important}*{cursor:none!important}")
        st = await pg.evaluate("MS.state")
        cfg = await pg.evaluate("JSON.parse(document.getElementById('ms-config').textContent)")
        s, bt = (int(x) - 1 for x in a.start.split("."))
        if not (0 <= s < len(st["scenes"]) and 0 <= bt < st["scenes"][s]["beats"]):
            fail(f"--start {a.start} が台本の外（{len(st['scenes'])} 場面）")
        dflt = cfg.get("dur", 4200)
        durs = [[(sc["dur"][i] if i < len(sc["dur"]) and sc["dur"][i] else dflt) for i in range(sc["beats"])] for sc in st["scenes"]]
        t_start = (sum(sum(d) for d in durs[:s]) + sum(durs[s][:bt])) / 1000      # 時刻表の上での撮り始め（BGM の位置）
        total = sum(sum(d) for d in durs) / 1000
        seconds = a.seconds or (total - t_start + 1.5)
        await pg.evaluate(f"MS.go({s},{bt})"); await pg.evaluate("__vt.advance(1200)")   # 入りを済ませてから
        vt0 = await pg.evaluate("__vt.now"); await pg.evaluate("MS.state.sfx.length = 0")
        await pg.keyboard.press("p")                                                     # 自動再生（エンジンのタイマーで進む）
        cdp = await pg.context.new_cdp_session(pg)
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-c:v", "mjpeg", "-i", "-",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(a.crf), "-preset", "medium", str(video)], stdin=subprocess.PIPE)
        n, dt, t0 = int(round(seconds * a.fps)), 1000 / a.fps, time.time()
        for i in range(n):
            await pg.evaluate(f"__vt.advance({dt})")
            shot = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 92})
            ff.stdin.write(base64.b64decode(shot["data"]))
            if i and i % (a.fps * 30) == 0:
                print(f"  {i / a.fps:.0f}s / {seconds:.0f}s", file=sys.stderr)
        ff.stdin.close(); ff.wait()
        log = [{"n": x["n"], "t": (x["t"] - vt0) / 1000} for x in (await pg.evaluate("MS.state.sfx")) if x["t"] >= vt0]
        await b.close()
    print(f"撮った: {n} コマ・{seconds:.1f} 秒・実時間 {time.time() - t0:.0f} 秒", file=sys.stderr)
    return log, seconds, t_start, cfg


async def render_sfx(html, names):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page()
        await pg.goto(html.as_uri()); await pg.wait_for_timeout(400)
        out = {n: await pg.evaluate(f"MS.renderSfx({json.dumps(n)})") for n in names}
        await b.close()
    return out


def write_wav(path, samples):
    import numpy as np
    x = np.asarray(samples, np.float32)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--out")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", default="1.1", help="撮り始める 場面.拍（1 始まり）")
    ap.add_argument("--seconds", type=float, help="撮る長さ（既定: 終わりまで＋1.5秒）")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--no-bgm", action="store_true")
    ap.add_argument("--keep-ctrl", action="store_true", help="操作ボタンを隠さない")
    a = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+", a.start):
        fail("--start は 場面.拍（例 3.1）")
    for cmd in ("ffmpeg", "ffprobe"):
        if not shutil.which(cmd):
            fail(f"{cmd} が無い", 4)
    try:
        import numpy  # noqa: F401
        import playwright  # noqa: F401
    except ImportError as e:
        fail(f"依存が無い: {e}", 4)
    html = pathlib.Path(a.html).resolve()
    out = pathlib.Path(a.out or html.with_name("video.mp4")).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td); video = td / "video.mp4"
        log, seconds, t_start, cfg = asyncio.run(shoot(a, html, video))
        inputs, filters = [], []
        if not a.no_sfx and log:
            import numpy as np
            snd = {k: np.asarray(v, np.float32) for k, v in asyncio.run(render_sfx(html, sorted({x["n"] for x in log}))).items()}
            track = np.zeros(int((seconds + 2) * SR), np.float32)
            for x in log:
                i = int(x["t"] * SR); s = snd[x["n"]]
                if 0 <= i < len(track):
                    track[i:i + len(s)] += s[:len(track) - i]
            track /= max(1.0, float(np.abs(track).max()) / 0.89)
            write_wav(td / "sfx.wav", track); inputs += ["-i", str(td / "sfx.wav")]
        m = None if a.no_bgm else re.search(r'<audio id="bgm"[^>]*src="data:audio/([\w.+-]+);base64,([^"]+)"', html.read_text(encoding="utf-8"))
        if m:
            ext = {"mp4": "m4a", "mpeg": "mp3", "ogg": "ogg"}.get(m.group(1), "m4a")
            (td / f"bgm.{ext}").write_bytes(base64.b64decode(m.group(2)))
            inputs += ["-ss", f"{t_start:.3f}", "-i", str(td / f"bgm.{ext}")]
        if not inputs:
            shutil.move(str(video), out)
        else:
            n_in = sum(1 for x in inputs if x == "-i")
            labels = []
            for j in range(n_in):
                is_bgm = m and j == n_in - 1
                vol = cfg.get("bgmVolume", 0.55) if is_bgm else 1.0
                filters.append(f"[{j + 1}:a]aresample={SR},volume={vol}[a{j}]"); labels.append(f"[a{j}]")
            fc = ";".join(filters) + ";" + "".join(labels) + f"amix=inputs={n_in}:normalize=0:duration=longest,alimiter=limit=0.95[aout]"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), *inputs, "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{seconds:.3f}", str(out)], check=True)
    sz = out.stat().st_size / 1e6
    print(f"{out}  {seconds:.1f} 秒・{a.fps}fps・{sz:.1f}MB・効果音 {0 if a.no_sfx else len(log)}・BGM {'あり' if m else 'なし'}")


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932）でなく UTF-8 で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8")
    main()
