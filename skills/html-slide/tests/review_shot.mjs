// review.html を開き、各ページの iframe（等倍）を PNG に撮る。試験用。
//   node review_shot.mjs <review.html> <出力ディレクトリ> [幅]
// 幅を渡すと、その幅で開いて横にはみ出していないか（scrollWidth）も JSON で出す
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import url from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require(path.join(process.env.HTML_SLIDE_NODE_MODULES, "puppeteer-core"));
const [file, outDir, width] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const browser = await puppeteer.launch({ executablePath: process.env.HTML_SLIDE_CHROME, headless: true,
  args: ["--allow-file-access-from-files", "--no-sandbox", "--font-render-hinting=none"] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: width ? +width : 3000, height: 1200 });
  await page.goto(url.pathToFileURL(path.resolve(file)).href, { waitUntil: "load" });
  await page.evaluate(async () => { await document.fonts.ready; });
  const info = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
  if (!width) {
    await page.click("#actual");
    await page.click("#openall");
    const frames = await page.$$(".stage iframe");
    for (let i = 0; i < frames.length; i++) {
      const f = await frames[i].contentFrame();
      await f.evaluate(async () => { await document.fonts.ready; });
      await frames[i].screenshot({ path: path.join(outDir, `frame-${String(i + 1).padStart(2, "0")}.png`) });
    }
  }
  console.log(JSON.stringify(info));
} finally {
  await browser.close();
}
