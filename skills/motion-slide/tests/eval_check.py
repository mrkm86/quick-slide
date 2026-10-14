"""組んだ HTML を Chrome で開き、式の値を確かめる。

    python3 -E eval_check.py <index.html> <式> <期待する値>   # 0=一致（MS.go(2, 1) まで進めてから評価する）
"""
import asyncio
import pathlib
import sys


async def main(path, expr, want):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(pathlib.Path(path).resolve().as_uri())
        await pg.wait_for_timeout(600)
        await pg.evaluate("MS.go(2, 1)")
        await pg.wait_for_timeout(300)
        got = await pg.evaluate(expr)
        await b.close()
    if str(got) != want or errs:
        print(f"{expr} = {got!r}（期待 {want!r}）・エラー {errs}", file=sys.stderr)
        return 1
    return 0


sys.exit(asyncio.run(main(*sys.argv[1:4])))
