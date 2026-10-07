// 資料の HTML の各 section を PNG に撮る。ページの画像（渡せる見た目のまま・1920×1080）とサムネイルの元。
// --pdf を付けると、同じ HTML を Chrome で印刷して PDF にする（PPTX と同じ 13.333×7.5 インチ・文字は選べる・ADR 0012）
//
//   node snapshot.mjs <deck.html> <出力ディレクトリ|-> [--only 3,5] [--pdf <deck.pdf>]
//
// 出力は <出力ディレクトリ>/slide-01.png …（- なら撮らない）。node_modules は ~/.cache/html-video/node_modules（puppeteer-core）を、
// Chrome は ~/.cache/puppeteer/chrome-headless-shell の一番新しい版を使う（どちらもネットワークに出ない）
// 文字のはみ出し（scrollWidth・scrollHeight が箱より大きい）と section の外に出る要素は、撮るついでに警告する
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import url from "node:url";

const HOME = os.homedir();
const require = createRequire(path.join(HOME, ".cache/html-video/node_modules/"));
const puppeteer = require("puppeteer-core");

function chromePath() {
  if (process.env.HTML_SLIDE_CHROME) return process.env.HTML_SLIDE_CHROME;
  const root = path.join(HOME, ".cache/puppeteer/chrome-headless-shell");
  const vers = fs.readdirSync(root).filter((d) => d.startsWith("mac_arm-")).sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true }));
  for (const v of vers.reverse()) {
    const p = path.join(root, v, "chrome-headless-shell-mac-arm64", "chrome-headless-shell");
    if (fs.existsSync(p)) return p;
  }
  throw new Error(`chrome-headless-shell が無い: ${root}`);
}

const args = process.argv.slice(2);
if (args.length < 2) {
  console.error("使い方: node snapshot.mjs <deck.html> <出力ディレクトリ|-> [--only 3,5] [--pdf <deck.pdf>]");
  process.exit(2);
}
const [htmlPath, outDir] = args;
const oi = args.indexOf("--only");
const only = oi >= 0 ? new Set(args[oi + 1].split(",").map(Number)) : null;
const pi = args.indexOf("--pdf");
const pdfPath = pi >= 0 ? args[pi + 1] : null;
const shots = outDir !== "-";
if (shots) fs.mkdirSync(outDir, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: chromePath(),
  headless: true,
  args: ["--allow-file-access-from-files", "--no-sandbox", "--font-render-hinting=none"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto(url.pathToFileURL(path.resolve(htmlPath)).href, { waitUntil: "load" });
  await page.evaluate(async () => { await document.fonts.ready; });
  const fonts = await page.evaluate(() => [...document.fonts].map((f) => `${f.family} ${f.weight} ${f.status}`));
  console.log(`字体: ${fonts.join(" / ")}`);
  const problems = !shots ? [] : await page.evaluate(() => {
    const out = [];
    document.querySelectorAll("section[data-slide]").forEach((sec, i) => {
      const sr = sec.getBoundingClientRect();
      sec.querySelectorAll("*").forEach((el) => {
        if (el.closest("aside.notes") || el.closest("svg")) return;
        const r = el.getBoundingClientRect();
        const label = `${el.className || el.tagName}「${(el.textContent || "").trim().slice(0, 16)}」`;
        if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {
          if (el.clientWidth > 0 || el.clientHeight > 0) out.push(`${i + 1}枚目: はみ出し ${label} (${el.scrollWidth}x${el.scrollHeight} > ${el.clientWidth}x${el.clientHeight})`);
        }
        if (r.width && (r.left < sr.left - 1 || r.right > sr.right + 1 || r.top < sr.top - 1 || r.bottom > sr.bottom + 1)) {
          out.push(`${i + 1}枚目: 画面の外 ${label}`);
        }
      });
    });
    return out;
  });
  for (const p of problems) console.log(`警告 ${p}`);
  const sections = await page.$$("section[data-slide]");
  let n = 0;
  for (let i = 0; shots && i < sections.length; i++) {
    if (only && !only.has(i + 1)) continue;
    const file = path.join(outDir, `slide-${String(i + 1).padStart(2, "0")}.png`);
    await sections[i].screenshot({ path: file });
    n++;
  }
  if (shots) console.log(`${n} 枚を撮った: ${outDir}`);
  if (pdfPath) {
    // 1ページ = 1 section。1920px を 13.333 インチ（PPTX と同じ）に収めるため 2/3 に縮める
    await page.addStyleTag({ content: "@page { size: 13.333in 7.5in; margin: 0; } html, body { margin: 0 !important; padding: 0 !important; " +
      "background: #fff !important; } section[data-slide] { margin: 0 !important; break-after: page; }" });
    await page.pdf({ path: pdfPath, width: "13.333in", height: "7.5in", scale: 2 / 3, printBackground: true });
    console.log(`PDF: ${pdfPath}（${sections.length} ページ）`);
  }
} finally {
  await browser.close();
}
