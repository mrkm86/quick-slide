"""検査を通らない台本を組み立てて Chrome で開き、場面の hook・script が他の場面・共通部へ抜け出さないことを確かめる。

    python3 -E leak_check.py <index.html>   # 0=抜けない（document.body.dataset.leaked が無い・他の場面は動く・エラーはその場面だけ）
"""
import asyncio
import pathlib
import sys


async def main(path):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(pathlib.Path(path).resolve().as_uri())
        await pg.wait_for_timeout(600)
        leaked = await pg.evaluate("document.body.dataset.leaked || null")
        await pg.evaluate("MS.go(1, 1)")
        await pg.wait_for_timeout(300)
        beat = await pg.evaluate("MS.state.B")
        await b.close()
    bad = []
    if leaked:
        bad.append("leaked が立った")
    if beat != 1:
        bad.append(f"他の場面（s2）が動かない（拍 {beat}）")
    if not any("場面 s3" in e for e in errs):
        bad.append(f"s3 のエラーが出ていない: {errs}")
    if bad:
        print("; ".join(bad), file=sys.stderr)
        return 1
    return 0


sys.exit(asyncio.run(main(sys.argv[1])))
