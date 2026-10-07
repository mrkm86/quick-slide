// ロゴを置ける場所を調べる（--logo のとき・deck.fit_logo_plates が使う）。ロゴとその地・下の文字・ページ番号を隠して
// （下の文字とページ番号はロゴの角に合わせて動かすので、測る邪魔にしない）、各 section の
// 候補の枠（--logo-position の順）ごとに、枠の周り（枠＋上下左右 M px）を PNG に撮る。色を測るのは Python 側（PIL）。
// あわせて、写真・絵（ロゴ以外の <img>）と文字が、候補の枠にかかるかを JSON（標準出力）で返す
//
//   node logo_ground.mjs <deck.html> <出力ディレクトリ> <M> <x,y,w,h> [<x,y,w,h> …]
//
// 出力は <出力ディレクトリ>/ground-<ページ>-<候補>.png（例 ground-01-0.png）と
// {"pages": [[{"x": 置いた x, "img": bool, "text": bool}, …候補], …ページ]}。
// x に -1 を書いた候補は、そのページの左半分の文字の左端にそろえる（無ければ 120）。node_modules と Chrome は snapshot.mjs と同じ
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

const [htmlPath, outDir, mArg, ...boxArgs] = process.argv.slice(2);
const m = Number(mArg);
const boxes = boxArgs.map((b) => b.split(",").map(Number));
if (!outDir || !Number.isFinite(m) || !boxes.length || boxes.some((b) => b.length !== 4 || b.some((v) => !Number.isFinite(v)))) {
  console.error("使い方: node logo_ground.mjs <deck.html> <出力ディレクトリ> <M> <x,y,w,h> [<x,y,w,h> …]");
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: chromePath(),
  headless: true,
  args: ["--allow-file-access-from-files", "--no-sandbox", "--font-render-hinting=none"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto(url.pathToFileURL(path.resolve(htmlPath)).href, { waitUntil: "load" });
  await page.evaluate(async () => {
    await document.fonts.ready;
    document.querySelectorAll(".logo, .logo-plate, .footer, .pageno, .watermark").forEach((el) => { el.style.visibility = "hidden"; });
  });
  // 枠にかかるもの: 写真・絵（ロゴ以外の <img>）と、文字を直に持つ要素
  const pages = await page.evaluate((boxes) => [...document.querySelectorAll("section[data-slide]")].map((sec) => {
    const o = sec.getBoundingClientRect();
    const hits = (el, [bx, by, bw, bh]) => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return false;
      const l = r.left - o.left, t = r.top - o.top;
      return l < bx + bw && l + r.width > bx && t < by + bh && t + r.height > by;
    };
    const shown = (el) => getComputedStyle(el).visibility !== "hidden" && !el.closest("aside.notes");
    const imgs = [...sec.querySelectorAll("img:not(.logo)")].filter(shown);
    const texts = [...sec.querySelectorAll("*")].filter((el) => shown(el) && !el.closest("svg") &&
      [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
    const lefts = texts.map((el) => el.getBoundingClientRect()).filter((r) => r.width && r.left - o.left < 960)
      .map((r) => Math.round(r.left - o.left));
    return boxes.map(([bx, ...rest]) => {
      const b = [bx < 0 ? (lefts.length ? Math.min(...lefts) : 120) : bx, ...rest];
      return { x: b[0], img: imgs.some((el) => hits(el, b)), text: texts.some((el) => hits(el, b)) };
    });
  }), boxes);
  const sections = await page.$$("section[data-slide]");
  for (let i = 0; i < sections.length; i++) {
    const r = await sections[i].boundingBox();
    for (let k = 0; k < boxes.length; k++) {
      const [, y, w, h] = boxes[k], x = pages[i][k].x;
      const x0 = Math.max(0, x - m), y0 = Math.max(0, y - m);
      const file = path.join(outDir, `ground-${String(i + 1).padStart(2, "0")}-${k}.png`);
      await page.screenshot({ path: file, captureBeyondViewport: true, clip: { x: r.x + x0, y: r.y + y0,
        width: Math.min(1920, x + w + m) - x0, height: Math.min(1080, y + h + m) - y0 } });
    }
  }
  console.log(JSON.stringify({ pages }));
} finally {
  await browser.close();
}
