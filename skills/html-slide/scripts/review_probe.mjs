// review 用の診断。資料の HTML（deck.html）を Chrome で開き、ページごとのはみ出しと見出しの行数を JSON で 1 行出す。
// PNG は撮らない。
//
//   node review_probe.mjs <deck.html>
//
// 出力: {"overflow": [{slide, kind, label, detail}], "headlines": [{slide, lines}]}
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import url from "node:url";
import { overflowProblems, headlineLines } from "./overflow.mjs";

const NM = process.env.HTML_SLIDE_NODE_MODULES || path.join(os.homedir(), ".cache/html-video/node_modules");
const require = createRequire(import.meta.url);
const puppeteer = require(path.join(NM, "puppeteer-core"));

if (process.argv.length < 3) {
  console.error("使い方: node review_probe.mjs <deck.html>");
  process.exit(2);
}
if (!process.env.HTML_SLIDE_CHROME) {
  console.error("HTML_SLIDE_CHROME（Chrome の場所）が渡されていない");
  process.exit(4);
}
const browser = await puppeteer.launch({
  executablePath: process.env.HTML_SLIDE_CHROME,
  headless: true,
  args: ["--allow-file-access-from-files", "--no-sandbox", "--font-render-hinting=none"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto(url.pathToFileURL(path.resolve(process.argv[2])).href, { waitUntil: "load" });
  await page.evaluate(async () => { await document.fonts.ready; });
  const overflow = await page.evaluate(overflowProblems);
  const headlines = await page.evaluate(headlineLines);
  console.log(JSON.stringify({ overflow, headlines }));
} finally {
  await browser.close();
}
