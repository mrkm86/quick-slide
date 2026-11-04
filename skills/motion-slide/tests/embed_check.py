"""埋め込み API v1 の受け入れ試験（docs/adr/0005）。組んだ HTML を sandbox="allow-scripts" の iframe に入れて確かめる。
期待する拍数・通知は、組んだ HTML の場面（data-beats）から画面のコードとは別に作り、届いた通知の全部と完全に比べる。

    python3 -E embed_check.py <index.html> <作業フォルダ>   # 0=すべて通る・1=外れがある（外れた項目を標準エラーへ）
"""
import asyncio
import json
import pathlib
import re
import shutil
import sys

SK = pathlib.Path(__file__).resolve().parent.parent
# 親ページ。f が対象、g が別フレーム（f へ命令を送り、送ったあとに親へ確認の知らせを返す）。LOG は f からの通知、SENT は g からの知らせ
HOST = ('<!doctype html><meta charset="utf-8"><iframe id="f" sandbox="allow-scripts" src="deck.html%s" '
        'style="width:1920px;height:1080px;border:0"></iframe><iframe id="g" sandbox="allow-scripts" srcdoc="" style="width:10px;height:10px"></iframe>'
        '<script>window.LOG=[];window.SENT=[];const f=document.getElementById("f"),g=document.getElementById("g");'
        'addEventListener("message",e=>{if(e.source===f.contentWindow)LOG.push(e.data);else if(e.source===g.contentWindow)SENT.push(e.data)});</script>')
SEND = "(m) => document.getElementById('f').contentWindow.postMessage(m, '*')"
# 履歴・位置を書く呼び出しを数える（全フレームの読み込み前に入れる）
SPY = "window.__H=0;for(const k of ['replaceState','pushState']){const o=History.prototype[k];History.prototype[k]=function(){window.__H++;return o.apply(this,arguments)}}"
V1 = {"namespace": "motion-slide", "version": 1}
fails = []


def check(ok, what):
    if not ok:
        fails.append(what)


def scene_beats(text):
    """組んだ HTML の場面ごとの拍数（画面のコードを通さず、<section class="scene"> の data-beats から）"""
    out = []
    for tag in re.findall(r'<section\b[^>]*\bclass="scene"[^>]*>', text):
        m = re.search(r'data-beats="(\d+)"', tag)
        out.append(int(m.group(1)) if m else 1)
    return out


class Model:
    """期待する通知を、拍数から作る"""
    def __init__(self, beats):
        self.beats = beats
        self.total = sum(beats)
        self.pos = [(s, b) for s, n in enumerate(beats) for b in range(n)]

    def ready(self):
        return {**V1, "type": "ready", "scenes": len(self.beats), "beats": self.total}

    def change(self, i):
        s, b = self.pos[i]
        return {**V1, "type": "change", "scene": s, "beat": b, "index": i, "total": self.total}

    def end(self):
        return {**V1, "type": "end", "total": self.total}

    def arrive(self, i):   # 位置 i へ移ったときの通知
        return [self.change(i)] + ([self.end()] if i == self.total - 1 else [])


async def main(html, work):
    from playwright.async_api import async_playwright
    work = pathlib.Path(work); work.mkdir(parents=True, exist_ok=True)
    shutil.copy(html, work / "deck.html")
    M = Model(scene_beats(pathlib.Path(html).read_text(encoding="utf-8")))
    total = M.total
    check(total > 2 and len(M.beats) > 1, f"試験には 2 場面・3 拍以上が要る: {M.beats}")
    async with async_playwright() as p:
        b = await p.chromium.launch()

        async def open_host(hash_=""):
            pg = await b.new_page(viewport={"width": 1920, "height": 1080})
            await pg.add_init_script(SPY)
            (work / "host.html").write_text(HOST % hash_, encoding="utf-8")
            await pg.goto((work / "host.html").resolve().as_uri())
            await pg.wait_for_function("LOG.length >= 2", timeout=8000)
            await pg.wait_for_timeout(300)
            return pg

        def child(pg):
            return next(f for f in pg.frames if f.url.endswith("deck.html") or "deck.html" in f.url)

        async def act(pg, m, ms=250):
            n = len(await pg.evaluate("LOG")); await pg.evaluate(SEND, m); await pg.wait_for_timeout(ms)
            return (await pg.evaluate("LOG"))[n:]

        cmd = lambda name, **k: {**V1, "type": "command", "name": name, **k}
        # 1. 読み込み直後・全拍・戻る・goto・再度の末尾。届いた通知の全部を、期待の列と完全に比べる
        pg = await open_host()
        want = [M.ready(), M.change(0)]
        check(await pg.evaluate("LOG") == want, f"起動の通知: {await pg.evaluate('LOG')} ≠ {want}")
        h0 = await child(pg).evaluate("window.__H")   # 起動のときの履歴の書き込み（従来のハッシュ更新）。以後の命令では増えない
        for i in range(1, total):
            r = await act(pg, cmd("next")); want += M.arrive(i)
            check(r == M.arrive(i), f"next → {i}: {r}")
        check(await act(pg, cmd("next")) == [], "末尾の next は通知しない")
        r = await act(pg, cmd("prev")); want += [M.change(total - 2)]
        check(r == [M.change(total - 2)], f"prev: {r}")
        r = await act(pg, cmd("goto", index=total - 1)); want += M.arrive(total - 1)
        check(r == M.arrive(total - 1), f"末尾へ戻ると end を再発信: {r}")
        check(await act(pg, cmd("goto", index=total - 1)) == [], "同じ index への goto は通知しない")
        r = await act(pg, {**V1, "type": "request-state"}); want += [M.ready(), M.change(total - 1)]
        check(r == [M.ready(), M.change(total - 1)], f"request-state: {r}")
        for i in (M.beats[0], 0):   # 場面をまたぐ goto（2 場面目の先頭・先頭）
            r = await act(pg, cmd("goto", index=i)); want += [M.change(i)]
            check(r == [M.change(i)], f"goto {i}: {r}")
        check(await act(pg, cmd("prev")) == [], "先頭の prev は通知しない")
        check(await pg.evaluate("LOG") == want, "届いた通知の全部が期待の列と一致しない（件数・順序・namespace・version を含む）")
        # 履歴・位置: 親の命令（next・prev・goto）の移動では 1 度も書かない
        check(await child(pg).evaluate("window.__H") == h0, f"親の命令の移動で履歴が書かれた: {h0} → {await child(pg).evaluate('window.__H')}")
        await pg.close()

        # 2. 不正なメッセージは無視（通知が増えず、位置も変わらない）
        pg = await open_host()
        bad = [{**V1, "namespace": "x", "type": "command", "name": "next"}, {**V1, "version": 2, "type": "command", "name": "next"},
               {**V1, "version": "1", "type": "command", "name": "next"}, {**V1, "type": "other", "name": "next"},
               cmd("jump"), "next", 7, None, [V1], [], {**V1, "type": "command"},
               cmd("goto"), cmd("goto", index=1.5), cmd("goto", index="1"), cmd("goto", index=-1), cmd("goto", index=total),
               cmd("goto", index=None), cmd("goto", index=True), cmd("goto", index=[1]), cmd("goto", index=1e9)]
        for m in bad:
            r = await act(pg, m, 60)
            check(r == [], f"不正なメッセージに反応した: {m!r} → {r}")
        r = await act(pg, {**V1, "type": "request-state"})
        check(r == [M.ready(), M.change(0)], f"不正のあとも位置が 0 のまま: {r}")
        # 別フレーム（source が親でない）から送る。そのフレームが実際に送ったこと（親への確認）を待ってから、対象が無視したことを確かめる
        n = len(await pg.evaluate("LOG"))
        script = ("<script>parent.frames[0].postMessage(%s, '*');parent.postMessage({sent: true}, '*')</scr" "ipt>") % json.dumps(cmd("next"))
        await pg.evaluate("(h) => { document.getElementById('g').srcdoc = h }", script)
        try:
            await pg.wait_for_function("SENT.length >= 1", timeout=5000)
        except Exception:
            check(False, "別フレームのスクリプトが実行されず、命令を送っていない")
        await pg.wait_for_timeout(400)
        check((await pg.evaluate("SENT")) == [{"sent": True}], "別フレームから確認の知らせが届かない")
        check(len(await pg.evaluate("LOG")) == n, "別フレームの命令に反応して通知が出た")
        r = await act(pg, {**V1, "type": "request-state"})
        check(r == [M.ready(), M.change(0)], f"別フレームの命令で動いた: {r}")
        await pg.close()

        # 3. 初期位置が末尾なら ready → change → end（完全比較）
        pg = await open_host("#99.99")
        check(await pg.evaluate("LOG") == [M.ready(), M.change(total - 1), M.end()], f"初期位置が末尾の通知: {await pg.evaluate('LOG')}")
        await pg.close()

        # 4. 単独表示では何も発信しない（自分あてのメッセージも出ない）
        pg = await b.new_page(viewport={"width": 1920, "height": 1080})
        await pg.add_init_script("window.SEEN=[];addEventListener('message',e=>SEEN.push(e.data))")
        await pg.goto((work / "deck.html").resolve().as_uri()); await pg.wait_for_timeout(500)
        for k in ("ArrowRight", "ArrowRight", "ArrowLeft"):
            await pg.keyboard.press(k)
        await pg.evaluate("window.postMessage(%s, '*')" % json.dumps(cmd("next")))   # 自分あての命令は受けない（親が居ない）
        await pg.wait_for_timeout(300)
        seen = await pg.evaluate("SEEN"); st = await pg.evaluate("[MS.state.S, MS.state.B]")
        check(seen == [cmd("next")] and st == [0, 1], f"単独表示で発信・受信した: {seen} {st}")
        check(await pg.evaluate("location.hash") == "#1.2", "単独表示のハッシュ更新が変わった")
        await pg.close(); await b.close()

    # 5. 通信の経路で storage・location・history を触らない（コードの確認。実行時の確認は 1. の履歴の数）
    js = (SK / "engine" / "engine.js").read_text(encoding="utf-8")
    block = js[js.index("// ── 埋め込み API v1"):js.index("  function go(")]
    for w in ("localStorage", "sessionStorage", "indexedDB", "location", "history", "document.cookie", "fetch(", "XMLHttpRequest"):
        check(w not in block, f"埋め込み API の経路に {w} がある")
    check(re.search(r"event\.origin|e\.origin", block) is None, "origin で許可判定している")


asyncio.run(main(*sys.argv[1:3]))
for f in fails:
    print("NG:", f, file=sys.stderr)
sys.exit(1 if fails else 0)
