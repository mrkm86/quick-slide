// SVG の画像を PNG に描く（PPTX の取り込みで、PNG の控えを持たない SVG の画像に使う・ADR 0025）
//
//   node svg_raster.mjs <jobs.json>
//
// jobs.json は [{"svg": <SVG のパス>, "png": <書く PNG のパス>, "w": 幅 px, "h": 高さ px}, …]。背景は透明。
// SVG は外から来たファイルなので、<img> の data: URL として開き（中のスクリプトは動かない）、ネットワークへの読み込みは全部止める。
// puppeteer-core と Chrome の在りかは呼ぶ側（browser.py）が環境変数で渡す（ADR 0016）
import fs from 'fs';
import os from 'os';
import path from 'path';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const NM = process.env.HTML_SLIDE_NODE_MODULES || path.join(os.homedir(), '.cache/html-video/node_modules');

function die(msg, rc) {
  process.stderr.write(`NG: ${msg}\n`);
  process.exit(rc);
}

async function main() {
  const [jobsPath] = process.argv.slice(2);
  if (!jobsPath) die('使い方: node svg_raster.mjs <jobs.json>', 1);
  const jobs = JSON.parse(fs.readFileSync(jobsPath, 'utf8'));
  let puppeteer;
  try {
    puppeteer = require(path.join(NM, 'puppeteer-core'));
  } catch (e) {
    die(`puppeteer-core が無い（${NM}）`, 4);
  }
  const exe = process.env.HTML_SLIDE_CHROME;
  if (!exe) die('Chrome の在りか（HTML_SLIDE_CHROME）が無い', 4);
  const browser = await puppeteer.launch({ executablePath: exe, args: ['--font-render-hinting=none'] });
  let failed = 0;
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', r => (r.url().startsWith('data:') || r.url() === 'about:blank' ? r.continue() : r.abort()));
    for (const j of jobs) {
      try {
        const w = Math.max(1, Math.round(j.w)), h = Math.max(1, Math.round(j.h));
        await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
        const data = fs.readFileSync(j.svg).toString('base64');
        await page.setContent(`<!doctype html><html><body style="margin:0;background:transparent">` +
          `<img id="i" src="data:image/svg+xml;base64,${data}" style="display:block;width:${w}px;height:${h}px"></body></html>`,
          { waitUntil: 'load', timeout: 30000 });
        const ok = await page.evaluate(() => { const i = document.getElementById('i'); return i.complete && i.naturalWidth > 0; });
        if (!ok) { failed++; continue; }
        await page.screenshot({ path: j.png, omitBackground: true, clip: { x: 0, y: 0, width: w, height: h } });
      } catch (e) {
        failed++;
      }
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(`svg_raster: ${jobs.length - failed}/${jobs.length}\n`);
}

main().catch(e => die(e && e.stack ? e.stack : String(e), 1));
