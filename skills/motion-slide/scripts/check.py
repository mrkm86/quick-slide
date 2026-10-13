"""組んだ HTML 1枚を Chrome で開き、全部の拍を順に進めて、崩れを数える。拍ごとの画像と、場面ごとの一覧画像も出す。

    python3 -E check.py <index.html> [--out <フォルダ>] [--wait <ms>] [--only s3,s5] [--reuse <report.json>] [--strict]

見るもの:
  NG（rc=2）  JS のエラー ／ 見えている要素が舞台（1920×1080）の外にはみ出す ／ 文字が箱からあふれて切れる
  警告        見えている文字どうしの重なり ／ 1〜2字だけ次の行に残る（泣き別れ）／ .x なのにふわっと出ない（transition の上書き） ／ 道しるべ（パンくず・拍の点・章のレール・言葉・ロゴ）との重なり
              （--strict で警告も NG。わざと重ねる要素には data-overlap-ok を付ける）
--reuse は撮り済みの拍の記録（check.json と同じ形の配列）。その拍の画像は <out>/beats/ に置いてあるものとして、撮らずに記録へ混ぜる
  （--only で撮る場面を絞り、それ以外を --reuse で渡すと、場面ごとのキャッシュになる。--only "" なら Chrome を起動しない）
出力: <out>/beats/sNN-bM.png ・ <out>/sheet-sNN.png（場面ごとの一覧）・ <out>/check.json ・ <out>/inspect.md（検品役が読む写し）
"""
import argparse, asyncio, json, pathlib, sys

PROBE = r"""
() => {
  const st = document.getElementById('stage'), K = st.getBoundingClientRect().width / 1920, S = st.getBoundingClientRect();
  const R = e => { const r = e.getBoundingClientRect(); return { x: (r.left - S.left) / K, y: (r.top - S.top) / K, w: r.width / K, h: r.height / K }; };
  const vis = e => { for (let n = e; n && n !== st; n = n.parentElement) { const c = getComputedStyle(n); if (c.display === 'none' || c.visibility === 'hidden' || +c.opacity < .05) return false; } return true; };
  const label = e => (e.id ? '#' + e.id : e.tagName.toLowerCase()) + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '') + ' 「' + (e.textContent || '').trim().slice(0, 18) + '」';
  const sc = document.querySelector('.scene.active'), out = { outside: [], clipped: [], overlap: [], chrome: [], text: [] };
  const ok = e => e.closest('[data-overlap-ok]');
  const leaves = [...sc.querySelectorAll('*')].filter(e => vis(e) && !e.closest('svg') && !e.closest('.flyer') && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()));
  // 文字は段落（インラインでない一番近い祖先）の単位で拾う。<em> などで文が途切れない
  const blk = e => { let n = e; while (n !== sc && getComputedStyle(n).display.startsWith('inline')) n = n.parentElement; return n; };
  const seenB = new Set();
  for (const e of leaves) { if (e.closest('aside.notes')) continue; const b = blk(e); if (seenB.has(b)) continue; seenB.add(b);
    const t = b.innerText.replace(/\s+/g, ' ').trim(); if (t) out.text.push(t); }
  out.notes = [...sc.querySelectorAll(':scope > aside.notes > p')].map(p => p.textContent.trim())[+sc.dataset.beat] || '';
  for (const e of sc.querySelectorAll('*')) {
    if (!vis(e) || e.closest('svg.wires')) continue;
    const r = R(e); if (r.w < 1 || r.h < 1) continue;
    if (r.x < -2 || r.y < -2 || r.x + r.w > 1922 || r.y + r.h > 1082) out.outside.push(label(e) + ` (${r.x|0},${r.y|0},${r.w|0}×${r.h|0})`);
  }
  for (const e of leaves) {
    const c = getComputedStyle(e);
    if ((c.overflow !== 'visible' || c.textOverflow === 'ellipsis') && (e.scrollWidth > e.clientWidth + 2 || e.scrollHeight > e.clientHeight + 2)) out.clipped.push(label(e));
  }
  // 文字の行の実際の形（Range）で重なりを測る。箱どうしではなく、字どうし
  const boxes = leaves.filter(e => !ok(e)).map(e => { const rg = document.createRange(); rg.selectNodeContents(e);
    return { e, rs: [...rg.getClientRects()].map(r => ({ x: (r.left - S.left) / K, y: (r.top - S.top) / K, w: r.width / K, h: r.height / K })).filter(r => r.w > 2 && r.h > 2) }; });
  const hit = (a, b) => { const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y); return w > 4 && h > 4 ? w * h : 0; };
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const A = boxes[i], B = boxes[j]; if (A.e.contains(B.e) || B.e.contains(A.e)) continue;
    if (A.rs.some(a => B.rs.some(b => hit(a, b) > 0))) out.overlap.push(label(A.e) + ' × ' + label(B.e));
  }
  const ch = ['ms-crumb', 'ms-dots', 'ms-terms', 'ms-logo', 'ms-ctrl'].map(id => document.getElementById(id)).filter(Boolean);
  const rail = document.getElementById('ms-rail'); if (rail && +getComputedStyle(rail).opacity > .5) ch.push(rail.querySelector('ol') || rail);
  for (const c of ch) { const cr = R(c); for (const B of boxes) if (B.rs.some(b => hit(cr, b) > 0)) out.chrome.push('#' + (c.id || c.parentElement.id) + ' × ' + label(B.e)); }
  // 1〜2字だけ次の行に残る（泣き別れ）。行の形を Range で取り、最後の行の幅を字の大きさと比べる
  // 段落（インラインでない要素）の最後の文字の塊を Range で測り、最後の行が 2.6 字ぶんより短ければ数える。
  // <br> や子の箱で意図して切った行は、最後の塊が1行に収まるので数えない
  out.orphan = [];
  for (const e of new Set(boxes.map(B => B.e))) {
    if (getComputedStyle(e).display.startsWith('inline')) continue;
    const w = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); let t = null; while (w.nextNode()) if (w.currentNode.textContent.trim()) t = w.currentNode;
    if (!t) continue; const rg = document.createRange(); rg.selectNodeContents(t);
    const rs = [...rg.getClientRects()].filter(r => r.width > 1), fs = parseFloat(getComputedStyle(t.parentElement).fontSize) * K;
    if (rs.length < 2) continue; const ly = Math.max(...rs.map(r => r.top)), lw = rs.filter(r => Math.abs(r.top - ly) < 3).reduce((x, r) => x + r.width, 0);
    if (Math.abs(rs[0].top - ly) > 3 && lw < fs * 2.6) out.orphan.push(label(e));
  }
  // .x なのに不透明度の動きが無い（後から書いた transition が .x の指定を上書きして、パッと出る）
  out.pop = [];
  for (const e of sc.querySelectorAll('.x')) { const c = getComputedStyle(e), tp = c.transitionProperty, an = c.animationName;
    if (!/\b(all|opacity)\b/.test(tp) && (!an || an === 'none')) out.pop.push(label(e) + ' （transition: ' + tp + '）'); }
  // 道しるべの文字が切れていないか（章のレールの題・ここまでの言葉）
  if (rail && +getComputedStyle(rail).opacity > .5) for (const li of rail.querySelectorAll('li')) if (li.scrollWidth > li.clientWidth + 1) out.clipped.push('章のレールの題が長い 「' + li.textContent.trim() + '」');
  const tm = document.getElementById('ms-terms'); if (tm && tm.scrollWidth > tm.clientWidth + 1) out.clipped.push('ここまでの言葉があふれている（config の terms を減らすか言葉を短く）');
  return out;
}
"""


EMIT = (("errors", "NG JS"), ("outside", "NG はみ出し"), ("clipped", "NG 切れ"), ("overlap", "警告 重なり"),
        ("chrome", "警告 道しるべと重なり"), ("orphan", "警告 泣き別れ"), ("pop", "警告 パッと出る"))


def counts(item):
    """拍の記録 → (NG の数, 警告の数)"""
    n_ng = len(item["errors"]) + len(item["outside"]) + len(item["clipped"])
    n_w = len(item["overlap"]) + len(item["chrome"]) + len(item["orphan"]) + len(item["pop"])
    return n_ng, n_w


def order(item):   # 場面の番号・拍の番号の順
    s, b = item["beat"][1:].split("-b")
    return int(s), int(b)


async def shoot(a, html, out, only):
    """only の場面（None なら全部）の拍を撮って、拍ごとの記録の配列を返す。only が空なら Chrome を起動しない"""
    report = []
    if only is not None and not only:
        return report
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(html.as_uri()); await pg.wait_for_timeout(800)
        scenes = await pg.evaluate("MS.state.scenes")
        for si, sc in enumerate(scenes):
            if only is not None and sc["id"] not in only:
                continue
            for bi in range(sc["beats"]):
                # 前の拍から1つ進める（飛ぶ動きなど「進んだときだけ」の演出も通す）。場面へは直に飛ぶので、前の場面を撮ったかどうかに依らない
                await pg.evaluate(f"MS.go({si},{max(0, bi - 1)})"); await pg.wait_for_timeout(150)
                if bi:
                    await pg.evaluate("MS.next()")
                await pg.wait_for_timeout(a.wait)
                r = await pg.evaluate(PROBE)
                name = f"s{si + 1:02d}-b{bi + 1}"
                await pg.screenshot(path=str(out / "beats" / f"{name}.png"))
                e_now = list(errs); errs.clear()
                report.append({"beat": name, "id": sc["id"], "title": sc["title"], "errors": e_now, **r})
        await b.close()
    return report


async def main(a):
    html = pathlib.Path(a.html).resolve()
    out = pathlib.Path(a.out or html.parent / "check"); (out / "beats").mkdir(parents=True, exist_ok=True)
    only = None if a.only is None else {x for x in a.only.split(",") if x}
    reused = json.loads(pathlib.Path(a.reuse).read_text(encoding="utf-8")) if a.reuse else []
    report = sorted(reused + await shoot(a, html, out, only), key=order)
    ng = warn = 0
    for item in report:
        n_ng, n_w = counts(item)
        ng += n_ng; warn += n_w
        for k, tag in EMIT:
            for v in item[k]:
                print(f"{item['beat']} {item['id']} {tag}: {v}")
    finish(out, html, report, ng, warn)
    print(f"拍 {len(report)} ・ NG {ng} ・ 警告 {warn} ・ 画像 {out}")
    return 2 if ng or (a.strict and warn) else 0


def finish(out, html, report, ng, warn):
    sheets(out, report)
    inspect_md(out, html, report, ng, warn)
    (out / "check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def inspect_md(out, html, report, ng, warn):
    """検品役が読む写し。拍ごとに、画面に出ている文字（増えた分）・話す台本・機械の検査の結果"""
    title = ""
    m = __import__("re").search(r"<title>(.*?)</title>", html.read_text(encoding="utf-8"))
    if m:
        title = m.group(1)
    L = [f"# {title}", "", f"- 本体: `{html}`（HTML 1枚。ブラウザで開き、クリックで拍が進む）",
         f"- 場面 {len({r['id'] for r in report})} ・ 拍 {len(report)} ・ 機械の検査: NG {ng} ・ 警告 {warn}", ""]
    seen, cur = set(), None
    for r in report:
        if r["id"] != cur:
            cur, seen = r["id"], set()
            L += ["", f"## {r['id']} {r['title']}"]
        new = [t for t in r["text"] if t not in seen]
        seen.update(r["text"])
        L.append(f"- **拍 {r['beat'].split('-b')[1]}** 増えた文字: " + " ／ ".join(new) if new else f"- **拍 {r['beat'].split('-b')[1]}** （文字は増えない・動きだけ）")
        if r.get("notes"):
            L.append(f"  - 話す台本: {r['notes']}")
        for k in ("errors", "outside", "clipped", "overlap", "chrome", "orphan", "pop"):
            for v in r.get(k, []):
                L.append(f"  - 検査 {k}: {v}")
    (out / "inspect.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def sheets(out, report):
    """場面ごとに、拍の画像を横に並べた一覧（目で見るのはこちら）"""
    from PIL import Image, ImageDraw
    by = {}
    for r in report:
        by.setdefault(r["beat"][:3], []).append(r["beat"])
    for s, names in by.items():
        tw, th, cols = 640, 360, 3
        rows = (len(names) + cols - 1) // cols
        sheet = Image.new("RGB", (tw * min(cols, len(names)), th * rows), "white")
        for i, n in enumerate(names):
            im = Image.open(out / "beats" / f"{n}.png").convert("RGB").resize((tw, th))
            ImageDraw.Draw(im).text((8, 6), n, fill=(200, 0, 0))
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
        sheet.save(out / f"sheet-{s}.png")


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932）でなく UTF-8 で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--out")
    ap.add_argument("--wait", type=int, default=1800, help="拍を進めてから撮るまで（ms）。飛ぶ動き・数え上げが終わる長さ")
    ap.add_argument("--only", help="場面 id をカンマ区切り（空なら撮らない）")
    ap.add_argument("--reuse", help="撮り済みの拍の記録（JSON の配列）。撮らずに混ぜる")
    ap.add_argument("--strict", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
