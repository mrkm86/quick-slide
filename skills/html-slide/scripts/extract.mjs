// HTML を Chrome で開き、スライドごとの部品（箱・文字・画像・SVG）と動きの属性を「部品 JSON」に書き出す
//
// 使い方: node extract.mjs --mode contract|hyperframes <html> <out.json>
//   contract    … 決まり（references/contract.md）の HTML。section[data-slide] を1枚ずつ表示して読み、決まり違反も集める
//   hyperframes … html-video が焼いた project/index.html（互換）。GSAP の timeline を各画面の終わりへ進めて読む。
//                 段（step）は決めない（from_video.py が台本から決める）。単位ごとに出る時刻 t と出方 kind を残す
// 部品 JSON の形は references/parts-format.md。
// 終了コード: 0 = 読めた（違反の有無は JSON の violations）/ 1 = 実行時エラー / 4 = Chrome・puppeteer-core が無い
//
// Node のモジュール（puppeteer-core）と Chrome の在りかは呼ぶ側（browser.py）が探して環境変数 HTML_SLIDE_NODE_MODULES・
// HTML_SLIDE_CHROME で渡す（ADR 0016）。直に呼ばれて渡されなかったときだけ、前からの置き場（~/.cache/…）を見る。
// スキルの中に node_modules を置かない。
import fs from 'fs';
import os from 'os';
import path from 'path';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const NM = process.env.HTML_SLIDE_NODE_MODULES || path.join(os.homedir(), '.cache/html-video/node_modules');
const CHROME_DIR = path.join(os.homedir(), '.cache/puppeteer/chrome-headless-shell');
const SETUP_HINT = '入れ方はスキルの references/deploy.md（Quick Slide から使うなら `python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --setup`）';

function die(msg, rc) {
  process.stderr.write(`NG: ${msg}\n`);
  process.exit(rc);
}

function chromePath() {
  if (process.env.HTML_SLIDE_CHROME) return process.env.HTML_SLIDE_CHROME;
  if (!fs.existsSync(CHROME_DIR)) return null;
  // 新しい版から（mac_arm-152.0.… > mac_arm-131.0.…）
  const ver = v => (v.match(/\d+/g) || []).map(Number);
  const dirs = fs.readdirSync(CHROME_DIR).sort((a, b) => {
    const x = ver(a), y = ver(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] || 0) !== (y[i] || 0)) return (y[i] || 0) - (x[i] || 0);
    return 0;
  });
  for (const d of dirs) {
    const exe = path.join(CHROME_DIR, d, 'chrome-headless-shell-mac-arm64', 'chrome-headless-shell');
    if (fs.existsSync(exe)) return exe;
  }
  return null;
}

// --- ブラウザの中で使う道具（window.__HP に置く） ----------------------------------------
function installLib() {
  const H = {};
  const cv = document.createElement('canvas');
  cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const r2 = v => Math.round(v * 100) / 100;
  H.r2 = r2;
  H.r4 = v => Math.round(v * 10000) / 10000;
  H.R = (r, o) => ({ x: r2(r.left - o.left), y: r2(r.top - o.top), w: r2(r.width), h: r2(r.height) });

  // 色 → {hex, a}。透明は null。rgb()・color(srgb …) 以外（oklab など）はキャンバスに塗って読む
  H.col = s => {
    if (!s) return null;
    s = s.trim();
    if (s === 'none' || s === 'transparent' || s.startsWith('url(')) return null;
    let r, g, b, a = 1, m;
    if ((m = s.match(/^rgba?\(([^)]+)\)$/))) {
      const p = m[1].split(/[\s,/]+/).filter(Boolean);
      [r, g, b] = p.slice(0, 3).map(Number);
      if (p[3] !== undefined) a = p[3].endsWith('%') ? parseFloat(p[3]) / 100 : +p[3];
    } else if ((m = s.match(/^color\(srgb\s+([^)]+)\)$/))) {
      const p = m[1].split(/[\s/]+/).filter(Boolean);
      [r, g, b] = p.slice(0, 3).map(v => +v * 255);
      if (p[3] !== undefined) a = p[3].endsWith('%') ? parseFloat(p[3]) / 100 : +p[3];
    } else {
      cx.clearRect(0, 0, 1, 1);
      cx.fillStyle = '#000';
      cx.fillStyle = s;
      cx.fillRect(0, 0, 1, 1);
      const d = cx.getImageData(0, 0, 1, 1).data;
      [r, g, b] = d;
      a = d[3] / 255;
    }
    if (!(a > 0.002) || [r, g, b].some(isNaN)) return null;
    return { hex: [r, g, b].map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join(''), a: Math.round(a * 1000) / 1000 };
  };
  H.fade = (c, op) => (c && op < 0.999 ? { ...c, a: Math.round(c.a * op * 1000) / 1000 } : c);

  // 一番外側のカンマで切る（rgb(…) の中のカンマは切らない）
  H.splitTop = s => {
    const out = [];
    let d = 0, cur = '';
    for (const ch of s) {
      if (ch === '(') d++;
      if (ch === ')') d--;
      if (ch === ',' && d === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  };
  const COLOR_RE = /^((?:rgba?|color|hsla?|lab|lch|oklab|oklch|hwb)\([^)]*\)|#[0-9a-fA-F]{3,8}|[a-zA-Z]+)\s*(.*)$/;
  const DIRS = { 'to top': 0, 'to right': 90, 'to bottom': 180, 'to left': 270, 'to top right': 45, 'to right top': 45,
    'to bottom right': 135, 'to right bottom': 135, 'to bottom left': 225, 'to left bottom': 225, 'to top left': 315, 'to left top': 315 };
  const angle = s => {
    const v = parseFloat(s);
    if (s.endsWith('turn')) return v * 360;
    if (s.endsWith('grad')) return v * 0.9;
    if (s.endsWith('rad')) return v * 180 / Math.PI;
    return v;
  };
  // background-image の1層 → {kind, angle(CSS の deg), stops:[{color, pos 0..1}]}。グラデーションでなければ null
  H.gradient = layer => {
    const m = layer.match(/^(repeating-)?(linear|radial|conic)-gradient\(([\s\S]*)\)$/);
    if (!m) return null;
    const args = H.splitTop(m[3]);
    let ang = 180;
    if (m[2] === 'linear') {
      if (/^-?[\d.]+(deg|turn|rad|grad)$/.test(args[0])) ang = angle(args.shift());
      else if (/^to /.test(args[0])) ang = DIRS[args.shift()] ?? 180;
    } else if (!H.col((args[0].match(COLOR_RE) || [])[1])) args.shift();     // radial の形と位置
    const stops = [];
    for (const a of args) {
      const mm = a.match(COLOR_RE);
      if (!mm) continue;
      const c = H.col(mm[1]) || { hex: '000000', a: 0 };
      const p = (mm[2] || '').trim().split(/\s+/)[0];
      const pos = p.endsWith('%') ? parseFloat(p) / 100 : (p === '0' || p === '0px') ? 0 : null;
      stops.push({ color: c, pos });
    }
    if (!stops.length) return null;
    if (stops[0].pos === null) stops[0].pos = 0;
    if (stops[stops.length - 1].pos === null) stops[stops.length - 1].pos = 1;
    for (let i = 1; i < stops.length - 1; i++) {
      if (stops[i].pos !== null) continue;
      let j = i;
      while (stops[j].pos === null) j++;
      const a0 = stops[i - 1].pos, a1 = stops[j].pos;
      for (let k = i; k < j; k++) stops[k].pos = a0 + (a1 - a0) * (k - i + 1) / (j - i + 1);
    }
    return { kind: m[2], repeating: !!m[1], angle: ang, stops: stops.map(s => ({ color: s.color, pos: Math.round(s.pos * 1000) / 1000 })) };
  };
  H.layers = s => (!s || s === 'none') ? [] : H.splitTop(s);

  // box-shadow → [{inset, color, dx, dy, blur, spread}]（Chrome の計算値は色が先頭）
  H.shadows = s => (!s || s === 'none') ? [] : H.splitTop(s).map(x => {
    const inset = /\binset\b/.test(x);
    x = x.replace(/\binset\b/, '').trim();
    const cm = x.match(/((?:rgba?|color|hsla?|lab|lch|oklab|oklch|hwb)\([^)]*\)|#[0-9a-fA-F]{3,8})/);
    const color = cm ? H.col(cm[1]) : null;
    const n = ((cm ? x.replace(cm[1], '') : x).match(/-?[\d.]+(?:px)?/g) || []).map(parseFloat);
    return { inset, color, dx: n[0] || 0, dy: n[1] || 0, blur: n[2] || 0, spread: n[3] || 0 };
  }).filter(v => v.color);

  H.opacity = (el, stop) => {
    let op = 1;
    for (let n = el; n && n.nodeType === 1 && n !== stop; n = n.parentNode) op *= parseFloat(getComputedStyle(n).opacity || 1);
    return op;
  };
  H.firstFamily = ff => (ff || '').split(',')[0].trim().replace(/^["']|["']$/g, '');
  H.isInline = el => {
    const d = getComputedStyle(el).display;
    return d === 'inline' || d === 'contents';
  };
  const NONTEXT = new Set(['img', 'svg', 'canvas', 'video', 'iframe', 'object', 'embed', 'audio', 'br']);
  // 文字を直に持つ箱か（自分の文字か、文字を持つ inline の子がある）。inline の子は run になる
  H.isTextBlock = el => {
    if (el.namespaceURI !== 'http://www.w3.org/1999/xhtml' || H.isInline(el)) return false;
    for (const c of el.childNodes) {
      if (c.nodeType === 3 && c.textContent.trim()) return true;
      if (c.nodeType === 1 && !NONTEXT.has(c.tagName.toLowerCase()) && H.isInline(c) && c.textContent.trim()) return true;
    }
    return false;
  };
  H.rot = cs => {
    const t = cs.transform;
    if (!t || t === 'none') return 0;
    const m = t.match(/^matrix\(([^)]+)\)$/);
    if (!m) return 0;
    const [a, b] = m[1].split(',').map(Number);
    return Math.round(Math.atan2(b, a) * 180 / Math.PI * 100) / 100;
  };
  H.desc = (el, stop) => {
    const parts = [];
    for (let n = el, k = 0; n && n.nodeType === 1 && n !== stop && k < 3; n = n.parentNode, k++) {
      let s = n.tagName.toLowerCase();
      if (n.id) s += '#' + n.id;
      const cls = typeof n.className === 'string' ? n.className.trim().split(/\s+/).filter(Boolean).slice(0, 2) : [];
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s);
    }
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ');
    return parts.join(' > ') + (t ? ` 「${t.slice(0, 24)}${t.length > 24 ? '…' : ''}」` : '');
  };

  // --- 文字 ---------------------------------------------------------------------------
  // 文字の箱 → run の列。run は {text, color, size, bold, italic, underline, strike, font, spc, line}、強制改行は {br: true}
  // line はブラウザが折り返した行の番号（PowerPoint で同じ所で切りたいときに使う）
  function runStyle(cs, colorProp) {
    const spc = parseFloat(cs.letterSpacing);
    const deco = cs.textDecorationLine || '';
    return {
      color: H.col(colorProp || cs.color), size: parseFloat(cs.fontSize), bold: parseInt(cs.fontWeight) >= 600,
      italic: cs.fontStyle === 'italic' || cs.fontStyle.startsWith('oblique'), underline: deco.includes('underline'),
      strike: deco.includes('line-through'), font: H.firstFamily(cs.fontFamily), spc: isNaN(spc) ? 0 : r2(spc),
    };
  }
  H.textRuns = (block, op) => {
    const items = [];
    // 隠れている inline（visibility: hidden・opacity: 0）の文字は出さない。inline の不透明度は run の色に掛ける
    const walk = (n, iop) => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) {
          // 隠れた文字・透明な文字（color: transparent）は出さない（PowerPoint では既定の色で見えてしまう）
          const ncs = getComputedStyle(n);
          if (ncs.visibility === 'visible' && H.col(ncs.color)) items.push({ node: c, el: n, op: iop });
        }
        else if (c.nodeType === 1) {
          const tag = c.tagName.toLowerCase();
          if (tag === 'br') items.push({ br: true });
          else if (NONTEXT.has(tag)) continue;
          else if (H.isInline(c)) {
            const cs = getComputedStyle(c);
            const cop = iop * parseFloat(cs.opacity || 1);
            if (cs.display !== 'none' && cop >= 0.002) walk(c, cop);
          }
        }
      }
    };
    walk(block, 1);
    // 行に切る（1つの文字の並びが複数行にまたがるときは、1文字ずつ行を見る）
    const frags = [];
    const rg = document.createRange();
    const vis = rs => [...rs].filter(r => r.width > 0 || r.height > 0);
    for (const it of items) {
      if (it.br) { frags.push({ br: true }); continue; }
      const t = it.node.textContent;
      if (!t) continue;
      const cs = getComputedStyle(it.el);
      rg.selectNodeContents(it.node);
      const rs = vis(rg.getClientRects());
      if (rs.length <= 1) { frags.push({ text: t, cs, rect: rs[0] || null, op: it.op }); continue; }
      let start = 0, top = null, h = 0;
      for (let i = 0; i < t.length; i++) {
        rg.setStart(it.node, i); rg.setEnd(it.node, i + 1);
        const r = vis(rg.getClientRects())[0];
        if (!r || !t[i].trim()) continue;
        if (top !== null && r.top > top + h * 0.5) {
          rg.setStart(it.node, start); rg.setEnd(it.node, i);
          frags.push({ text: t.slice(start, i), cs, rect: rg.getBoundingClientRect(), op: it.op });
          start = i;
        }
        top = r.top; h = r.height;
      }
      rg.setStart(it.node, start); rg.setEnd(it.node, t.length);
      frags.push({ text: t.slice(start), cs, rect: rg.getBoundingClientRect(), op: it.op });
    }
    // 行の番号（上から。前の行の下端より下に始まったら次の行）
    let line = 0, bottom = null, forced = false;
    for (const f of frags) {
      if (f.br) { line += 1; bottom = null; forced = true; continue; }
      if (!f.rect || !(f.rect.width > 0 || f.rect.height > 0)) { f.line = line; continue; }
      if (bottom !== null && !forced && f.rect.top >= bottom - Math.min(2, f.rect.height * 0.3)) line += 1;
      forced = false;
      f.line = line;
      bottom = bottom === null || f.rect.top >= bottom - 2 ? f.rect.bottom : Math.max(bottom, f.rect.bottom);
    }
    // 空白をまとめる（white-space: pre 系は残す）。行の頭と終わりの空白は落とす
    const runs = [];
    let prevSpace = true;
    for (const f of frags) {
      if (f.br) { runs.push({ br: true }); prevSpace = true; continue; }
      const pre = /^(pre|pre-wrap|break-spaces)$/.test(f.cs.whiteSpace);
      let text = f.text;
      if (!pre) {
        text = text.replace(/[ \t\n\r\f]+/g, ' ');
        if (prevSpace && text.startsWith(' ')) text = text.slice(1);
      }
      if (!text) continue;
      const pieces = pre ? text.split('\n') : [text];
      pieces.forEach((p, k) => {
        if (k > 0) runs.push({ br: true });
        if (p) {
          const st = runStyle(f.cs);
          runs.push({ text: p, ...st, color: H.fade(st.color, f.op), line: f.line });
        }
      });
      prevSpace = !pre && text.endsWith(' ');
    }
    // 強制改行の前と、文字の終わりの空白。自動で折り返した所の空白は単語の切れ目なので残す
    // （PowerPoint が自分で折り返すとき要る。改行を固定するときは pptx_writer が落とす）
    for (let i = 0; i < runs.length; i++) {
      const r = runs[i], n = runs[i + 1];
      if (r.br || !r.text.endsWith(' ')) continue;
      if (!n || n.br) r.text = r.text.replace(/ +$/, '');
    }
    const out = runs.filter(r => r.br || r.text);
    for (const r of out) if (r.color) r.color = H.fade(r.color, op);
    return { runs: out, frags: frags.filter(f => f.rect && !f.br) };
  };

  H.text = (el, cs, out, ctx, unit, op) => {
    const { runs, frags } = H.textRuns(el, op);
    if (!runs.some(r => r.text && r.text.trim())) return;
    const lines = new Set(frags.map(f => f.line)).size;
    const u = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
    for (const f of frags) {
      u.left = Math.min(u.left, f.rect.left); u.top = Math.min(u.top, f.rect.top);
      u.right = Math.max(u.right, f.rect.right); u.bottom = Math.max(u.bottom, f.rect.bottom);
    }
    if (!isFinite(u.left)) return;
    const ta = cs.textAlign;
    const align = ta === 'center' || ta === '-webkit-center' ? 'ctr' : (ta === 'right' || ta === 'end') ? 'r' : ta === 'justify' ? 'just' : 'l';
    const p = { type: 'text', unit, name: ctx.nameOf(el), runs, lines, align, rot: H.rot(cs) || undefined };
    if (lines > 1) {
      // 複数行: 要素の中身の箱（枠と余白の内側）の幅で折り返す。行送りは実際の行の間隔から
      const b = el.getBoundingClientRect();
      const pl = parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth), pr = parseFloat(cs.paddingRight) + parseFloat(cs.borderRightWidth);
      const pt = parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth), pb = parseFloat(cs.paddingBottom) + parseFloat(cs.borderBottomWidth);
      const tops = {};
      for (const f of frags) if (!(f.line in tops) || f.rect.top < tops[f.line].top) tops[f.line] = f.rect;
      const ks = Object.keys(tops).map(Number).sort((a, b) => a - b);
      const first = tops[ks[0]], last = tops[ks[ks.length - 1]];
      const lhCss = parseFloat(cs.lineHeight);
      const lh = ks.length > 1 ? (last.top - first.top) / (ks[ks.length - 1] - ks[0]) : (isNaN(lhCss) ? first.height : lhCss);
      // 1行目の行の箱の上端 = 文字の箱の上端 − 行の上の余白（(行送り − 文字の高さ) / 2）
      const lineTop = first.top - (lh - first.height) / 2;
      const hasBlock = [...el.children].some(c => !H.isInline(c) && c.tagName.toLowerCase() !== 'br');
      const x = hasBlock ? u.left : b.left + pl, w = hasBlock ? u.right - u.left : b.width - pl - pr;
      p.rect = { x: r2(x - ctx.origin.left), y: r2(lineTop - ctx.origin.top), w: r2(w), h: r2(lh * lines) };
      p.lh = r2(lh);
      p.ca = r2(first.height);
      p.box = { x: r2(b.left + pl - ctx.origin.left), y: r2(b.top + pt - ctx.origin.top), w: r2(b.width - pl - pr), h: r2(b.height - pt - pb) };
      // 文節で折る（auto-phrase・keep-all）・行をならす（balance・pretty）は PowerPoint にできない。ブラウザの改行の位置を固定する
      const tw = cs.textWrapStyle || cs.textWrap || '';
      if (['auto-phrase', 'keep-all'].includes(cs.wordBreak) || /balance|pretty/.test(tw)) p.pin = true;
    } else {
      p.rect = H.R({ left: u.left, top: u.top, width: u.right - u.left, height: u.bottom - u.top }, ctx.origin);
      const lhCss = parseFloat(cs.lineHeight);
      p.lh = r2(isNaN(lhCss) ? u.bottom - u.top : lhCss);
    }
    out.push(p);
  };

  // --- 箱 -----------------------------------------------------------------------------
  // 背景（ベタ・グラデーション）・枠・角丸・影。広がりだけの影（0 0 0 6px）は、後ろに一回り大きい箱を置く
  H.box = (el, cs, out, ctx, unit, op) => {
    const b = el.getBoundingClientRect();
    if (b.width <= 0 || b.height <= 0) return;
    const r = H.R(b, ctx.origin);
    let fill = H.fade(H.col(cs.backgroundColor), op), grad = null;
    for (const layer of H.layers(cs.backgroundImage)) {
      const g = H.gradient(layer);
      if (!g) continue;
      if (g.kind === 'linear' && !g.repeating) grad = g;
      else if (!fill) fill = g.stops[g.stops.length - 1].color;     // 放射などは一番外の色のベタで近似
      break;
    }
    if (grad) grad = { ...grad, stops: grad.stops.map(s => ({ ...s, color: H.fade(s.color, op) })) };
    const side = ['Top', 'Right', 'Bottom', 'Left'].map(k => ({
      w: parseFloat(cs[`border${k}Width`]) || 0, style: cs[`border${k}Style`], color: H.col(cs[`border${k}Color`]),
    })).map(s => (s.style === 'none' || s.style === 'hidden' || !s.color ? { ...s, w: 0 } : s));
    const uniform = side.every(s => s.w === side[0].w && s.style === side[0].style && s.color && side[0].color && s.color.hex === side[0].color.hex && s.color.a === side[0].color.a);
    const rad = v => (v.endsWith('%') ? parseFloat(v) / 100 * Math.min(b.width, b.height) : parseFloat(v)) || 0;
    const rx = rad(cs.borderTopLeftRadius);
    const shadows = H.shadows(cs.boxShadow).filter(s => !s.inset);
    for (const s of shadows) {
      if (s.blur === 0 && s.dx === 0 && s.dy === 0 && s.spread > 0) {
        out.push({ type: 'rect', unit, name: ctx.nameOf(el, 'ring'),
          rect: { x: r2(r.x - s.spread), y: r2(r.y - s.spread), w: r2(r.w + 2 * s.spread), h: r2(r.h + 2 * s.spread) },
          rx: rx > 0 ? rx + s.spread : 0, fill: H.fade(s.color, op) });
      }
    }
    const drop = shadows.find(s => !(s.blur === 0 && s.dx === 0 && s.dy === 0));
    const stroke = uniform && side[0].w > 0 ? H.fade(side[0].color, op) : null;
    if (fill || grad || stroke) {
      out.push({ type: 'rect', unit, name: ctx.nameOf(el), rect: r, rx, fill, grad: grad || undefined, stroke,
        sw: stroke ? side[0].w : 0, dash: stroke && side[0].style === 'dashed' ? 'dash' : stroke && side[0].style === 'dotted' ? 'dot' : undefined,
        inset: true, shadow: (fill || grad) && drop ? { dx: drop.dx, dy: drop.dy, blur: drop.blur, color: drop.color } : undefined });
    }
    if (!uniform) {
      // 辺ごとに違う枠は、辺ごとの細い箱にする
      const [t, rr, bb, l] = side;
      const sides = [
        [t, { x: r.x, y: r.y, w: r.w, h: t.w }], [bb, { x: r.x, y: r.y + r.h - bb.w, w: r.w, h: bb.w }],
        [l, { x: r.x, y: r.y, w: l.w, h: r.h }], [rr, { x: r.x + r.w - rr.w, y: r.y, w: rr.w, h: r.h }],
      ];
      for (const [s, rect] of sides) if (s.w > 0) out.push({ type: 'rect', unit, name: ctx.nameOf(el, 'border'), rect, rx: 0, fill: H.fade(s.color, op) });
    }
  };

  // 疑似要素の箱（hyperframes のボードの上の桟だけに使う。決まりの HTML では違反）
  H.pseudoBox = (el, which, ctx, unit, name) => {
    const ps = getComputedStyle(el, which);
    if (!ps.content || ps.content === 'none' || ps.content === 'normal') return null;
    const b = el.getBoundingClientRect(), cs = getComputedStyle(el);
    const x = b.left + parseFloat(cs.borderLeftWidth) + parseFloat(ps.left), y = b.top + parseFloat(cs.borderTopWidth) + parseFloat(ps.top);
    return { type: 'rect', unit, name, rect: H.R({ left: x, top: y, width: parseFloat(ps.width), height: parseFloat(ps.height) }, ctx.origin),
      rx: parseFloat(ps.borderTopLeftRadius) || 0, fill: H.col(ps.backgroundColor) };
  };

  // --- 画像 ---------------------------------------------------------------------------
  H.img = (el, cs, ctx, unit, op) => {
    const b = el.getBoundingClientRect();
    const pl = parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth), pt = parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth);
    const bw = b.width - pl - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
    const bh = b.height - pt - parseFloat(cs.paddingBottom) - parseFloat(cs.borderBottomWidth);
    const iw = el.naturalWidth, ih = el.naturalHeight;
    let src = el.currentSrc || el.src;
    if (src.startsWith('file://')) src = decodeURIComponent(new URL(src).pathname);
    const pos = (cs.objectPosition || '50% 50%').split(/\s+/).map(v => (v.endsWith('%') ? parseFloat(v) / 100 : 0.5));
    let rect = { left: b.left + pl, top: b.top + pt, width: bw, height: bh }, crop = null;
    const fit = cs.objectFit;
    if (iw && ih && fit !== 'fill') {
      let s = fit === 'cover' ? Math.max(bw / iw, bh / ih) : fit === 'contain' ? Math.min(bw / iw, bh / ih) : fit === 'none' ? 1 : Math.min(1, bw / iw, bh / ih);
      const dw = iw * s, dh = ih * s;
      const ox = (bw - dw) * pos[0], oy = (bh - dh) * pos[1];
      // 箱からはみ出す分は切り抜き、足りない分は箱を縮める
      const cl = Math.max(0, -ox) / dw, cr = Math.max(0, dw + ox - bw) / dw, ct = Math.max(0, -oy) / dh, cb = Math.max(0, dh + oy - bh) / dh;
      if (cl + cr + ct + cb > 1e-4) crop = { l: H.r4(cl), t: H.r4(ct), r: H.r4(cr), b: H.r4(cb) };   // 2桁に丸めると絵が約1%伸びる
      rect = { left: b.left + pl + Math.max(0, ox), top: b.top + pt + Math.max(0, oy), width: Math.min(bw, dw), height: Math.min(bh, dh) };
    }
    const drop = H.shadows(cs.boxShadow).find(s => !s.inset && !(s.blur === 0 && s.dx === 0 && s.dy === 0));
    return { type: 'img', unit, name: ctx.nameOf(el), rect: H.R(rect, ctx.origin), src, crop: crop || undefined, alt: el.getAttribute('alt') || undefined,
      radius: parseFloat(cs.borderTopLeftRadius) || 0, op: op < 0.999 ? Math.round(op * 1000) / 1000 : undefined,
      shadow: drop ? { dx: drop.dx, dy: drop.dy, blur: drop.blur, color: drop.color } : undefined };
  };

  // --- SVG ----------------------------------------------------------------------------
  H.svg = (svg, out, ctx, svgOp) => {
    const O = ctx.origin;
    const hiddenIn = el => {
      for (let n = el; n && n !== svg; n = n.parentNode) {
        const c = getComputedStyle(n);
        if (c.display === 'none' || c.visibility === 'hidden') return true;
      }
      return false;
    };
    for (const el of svg.querySelectorAll('rect,circle,ellipse,line,polyline,polygon,path,text')) {
      if (el.closest('defs,clipPath,mask,pattern,marker,symbol')) continue;
      if (hiddenIn(el)) continue;
      const cs = getComputedStyle(el);
      let op = svgOp;
      for (let n = el; n && n !== svg; n = n.parentNode) op *= parseFloat(getComputedStyle(n).opacity || 1);
      if (op < 0.002) continue;
      const m = el.getScreenCTM();
      if (!m) continue;
      const sc = Math.sqrt(m.a * m.a + m.b * m.b);
      const P = (x, y) => { const p = new DOMPoint(x, y).matrixTransform(m); return [r2(p.x - O.left), r2(p.y - O.top)]; };
      const unit = ctx.unitOf(el);
      const name = ctx.nameOf(el);
      const tag = el.tagName.toLowerCase();
      const rot = Math.abs(m.b) > 1e-6 || Math.abs(m.c) > 1e-6;
      if (tag === 'text') {
        // 行: 位置指定のある tspan（x・y・dy）で行が変わる。親 text の直の文字と位置指定のない tspan は、その時点の行に続く
        const posed = c => c.nodeType === 1 && c.tagName.toLowerCase() === 'tspan' && (c.hasAttribute('x') || c.hasAttribute('y') || c.hasAttribute('dy'));
        const rows = [];
        let cur = [];
        for (const c of el.childNodes) {
          if (posed(c)) { if (cur.length) rows.push(cur); cur = [c]; } else cur.push(c);
        }
        if (cur.length) rows.push(cur);
        const rg = document.createRange();
        for (const row of rows) {
          const runs = [];
          const u = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
          const add = (n, parent) => {
            if (n.nodeType === 3) {
              if (!n.textContent) return;
              const s = getComputedStyle(parent);
              if (s.visibility !== 'visible' || !H.col(s.fill)) return;
              rg.selectNodeContents(n);
              const b = rg.getBoundingClientRect();
              if (b.width > 0) {
                u.left = Math.min(u.left, b.left); u.top = Math.min(u.top, b.top);
                u.right = Math.max(u.right, b.right); u.bottom = Math.max(u.bottom, b.bottom);
              }
              runs.push({ text: n.textContent.replace(/\s+/g, ' '), ...runStyle(s, s.fill), line: 0 });
            } else if (n.nodeType === 1 && n.tagName.toLowerCase() === 'tspan' && getComputedStyle(n).display !== 'none') {
              for (const c of n.childNodes) add(c, n);
            }
          };
          for (const n of row) add(n, el);
          if (!isFinite(u.left)) continue;
          const b = { left: u.left, top: u.top, width: u.right - u.left, height: u.bottom - u.top };
          if (runs.length) { runs[0].text = runs[0].text.replace(/^ /, ''); runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/ $/, ''); }
          for (const r of runs) { r.size = r2(r.size * sc); r.spc = r2(r.spc * sc); r.color = H.fade(r.color, op); }
          if (!runs.some(r => r.text.trim())) continue;
          out.push({ type: 'text', unit, name, rect: H.R(b, O), runs: runs.filter(r => r.text), lines: 1, align: 'ctr',
            lh: r2(b.height), rot: rot ? Math.round(Math.atan2(m.b, m.a) * 18000 / Math.PI) / 100 : undefined });
        }
        continue;
      }
      // line は面が無いので塗らない（既定の fill: black を拾わない）
      const fill = tag === 'line' ? null : H.fade(H.col(cs.fill), op * parseFloat(cs.fillOpacity)), stroke = H.fade(H.col(cs.stroke), op * parseFloat(cs.strokeOpacity));
      const sw = r2(parseFloat(cs.strokeWidth) * sc);
      if (!fill && !(stroke && sw > 0)) continue;
      // 破線: 繰り返しの長さが線の長さより十分短いときだけ（html-video の印は dasharray = 全長で、描き終えたら実線）
      let dash;
      if (cs.strokeDasharray && cs.strokeDasharray !== 'none' && el.getTotalLength) {
        const ds = cs.strokeDasharray.split(/[\s,]+/).map(parseFloat).filter(v => !isNaN(v));
        if (ds.length && Math.max(...ds) < el.getTotalLength() * 0.3) dash = Math.max(...ds) <= sw * 1.5 ? 'dot' : 'dash';
      }
      const st = { fill, stroke: sw > 0 ? stroke : null, sw, cap: cs.strokeLinecap, join: cs.strokeLinejoin, dash };
      if (tag === 'rect' && !rot) {
        const x = el.x.baseVal.value, y = el.y.baseVal.value, w = el.width.baseVal.value, h = el.height.baseVal.value;
        const [x1, y1] = P(x, y), [x2, y2] = P(x + w, y + h);
        out.push({ type: 'rect', unit, name, rect: { x: Math.min(x1, x2), y: Math.min(y1, y2), w: r2(Math.abs(x2 - x1)), h: r2(Math.abs(y2 - y1)) },
          rx: r2((el.rx.baseVal.value || el.ry.baseVal.value || 0) * sc), ...st });
        continue;
      }
      if ((tag === 'circle' || tag === 'ellipse') && !rot) {
        const b = el.getBBox();
        const [x1, y1] = P(b.x, b.y), [x2, y2] = P(b.x + b.width, b.y + b.height);
        out.push({ type: 'ellipse', unit, name, rect: { x: Math.min(x1, x2), y: Math.min(y1, y2), w: r2(Math.abs(x2 - x1)), h: r2(Math.abs(y2 - y1)) }, ...st });
        continue;
      }
      let subs = [];
      if (tag === 'line') subs = [{ pts: [P(el.x1.baseVal.value, el.y1.baseVal.value), P(el.x2.baseVal.value, el.y2.baseVal.value)], closed: false }];
      else if (tag === 'polyline' || tag === 'polygon') subs = [{ pts: [...el.points].map(p => P(p.x, p.y)), closed: tag === 'polygon' }];
      else {
        // path（と回った rect・円）: 直線だけの path は頂点をそのまま、曲線は長さに沿って点を取る（サブパスの切れ目は点の飛びで見つける）
        const d = tag === 'path' ? (el.getAttribute('d') || '') : '';
        const simple = tag === 'path' && /^[MLHVZ0-9.,\s-]+$/.test(d);
        if (simple) {
          const toks = d.match(/[MLHVZ]|-?[\d.]+/g) || [];
          let c = null, x = 0, y = 0, i = 0, sx = 0, sy = 0, cur = [];
          while (i < toks.length) {
            if (/[MLHVZ]/.test(toks[i])) c = toks[i++];
            if (c === 'Z') { if (cur.length) subs.push({ pts: cur, closed: true }); cur = []; x = sx; y = sy; continue; }
            if (c === 'M') { if (cur.length) subs.push({ pts: cur, closed: false }); x = +toks[i++]; y = +toks[i++]; sx = x; sy = y; cur = [P(x, y)]; c = 'L'; continue; }
            if (c === 'L') { x = +toks[i++]; y = +toks[i++]; }
            else if (c === 'H') x = +toks[i++];
            else if (c === 'V') y = +toks[i++];
            else { i++; continue; }
            cur.push(P(x, y));
          }
          if (cur.length) subs.push({ pts: cur, closed: false });
        } else {
          // 曲線のある path: 長さに沿って点を取る。サブパス（M・m で始まる部分）ごとに、手前までの長さを測って区間を切る
          // （点の飛びで見分けると、前の終わりの近くから始まる穴を取り違える）。穴は1つの path の中の部分パスで、そのまま別のサブパスとして写す
          const L = el.getTotalLength();
          const stepPx = Math.max(0.5, Math.min(2, L * sc / 400));
          const step = stepPx / sc;
          const cuts = [];
          if (tag === 'path') for (const mt of d.matchAll(/[Mm]/g)) if (mt.index > 0) cuts.push(mt.index);
          const lens = [0];
          if (cuts.length) {
            const tmp = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            el.parentNode.appendChild(tmp);
            for (const c of cuts) { tmp.setAttribute('d', d.slice(0, c)); lens.push(tmp.getTotalLength()); }
            tmp.remove();
          }
          lens.push(L);
          const bounds = [0, ...cuts, d.length];
          const at = s => { const q = el.getPointAtLength(s); return P(q.x, q.y); };
          const rdp = (pts, tol) => {   // 折れ線の間引き（直線の上の点を落とす）
            if (pts.length < 3) return pts;
            const keep = new Uint8Array(pts.length); keep[0] = keep[pts.length - 1] = 1;
            const stack = [[0, pts.length - 1]];
            while (stack.length) {
              const [i, j] = stack.pop();
              const [ax, ay] = pts[i], [bx, by] = pts[j];
              const dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy);
              let worst = -1, wd = tol;
              for (let k = i + 1; k < j; k++) {
                const dd = len < 1e-9 ? Math.hypot(pts[k][0] - ax, pts[k][1] - ay) : Math.abs(dy * (pts[k][0] - ax) - dx * (pts[k][1] - ay)) / len;
                if (dd > wd) { wd = dd; worst = k; }
              }
              if (worst > 0) { keep[worst] = 1; stack.push([i, worst], [worst, j]); }
            }
            return pts.filter((_, k) => keep[k]);
          };
          for (let k = 0; k < lens.length - 1; k++) {
            const s0 = lens[k], s1 = lens[k + 1];
            const text = d.slice(bounds[k], bounds[k + 1]).trim();
            const eps = Math.min(1e-3, (s1 - s0) / 4);      // 区間の境目は前後どちらのサブパスにも読めるので、少し内側から取る
            const pts = [];
            for (let s = s0 + eps; s < s1 - eps; s += step) pts.push(at(s));
            pts.push(at(s1 - eps));
            const a0 = pts[0], b0 = pts[pts.length - 1];
            const closed = tag !== 'path' || /[zZ]$/.test(text) || Math.hypot(a0[0] - b0[0], a0[1] - b0[1]) < 1.5;
            subs.push({ pts: rdp(pts, 0.05), closed });
          }
        }
      }
      if (subs.length) out.push({ type: 'path', unit, name, subs, ...st });
    }
  };

  // --- 木を下りる --------------------------------------------------------------------
  // ctx: {origin（座標の原点の rect）, root（不透明度を掛け合わせる上限）, unitOf(el), nameOf(el, suffix), skip(el)}
  H.walk = (el, out, ctx) => {
    if (ctx.skip && ctx.skip(el)) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none') return;
    const op = H.opacity(el, ctx.root);
    if (op < 0.002) return;              // 見えないもの（出ていない・消えた）
    const tag = el.tagName.toLowerCase();
    const vis = cs.visibility !== 'hidden';
    const unit = ctx.unitOf(el);
    if (tag === 'svg') { if (vis) H.svg(el, out, ctx, op); return; }
    if (tag === 'img') { if (vis && el.naturalWidth) out.push(H.img(el, cs, ctx, unit, op)); return; }
    if (NONTEXT.has(tag)) return;
    const tb = H.isTextBlock(el);
    if (vis && cs.display !== 'contents') {
      H.box(el, cs, out, ctx, unit, op);
      if (tb) H.text(el, cs, out, ctx, unit, op);
    }
    for (const c of H.paintOrder(el)) {
      if (tb && H.isInline(c)) continue;      // run に入れた
      H.walk(c, out, ctx);
    }
  };

  // 兄弟の描画順（CSS の重ね順の簡略版）: z-index が負 → 位置指定のない要素 → 位置指定あり（z-index auto・0）→ z-index が正。
  // 同じ組の中は文書の順。z-index は位置指定のある要素と flex・grid の子にだけ効く。祖先をまたぐ重ね順（stacking context）は再現しない
  // 要素が重ね順の文脈（stacking context）を作るか（決まりで許す CSS の範囲で）
  H.makesContext = el => {
    const cs = getComputedStyle(el);
    const parent = el.parentElement;
    const flexItem = parent && /flex|grid/.test(getComputedStyle(parent).display);
    return (cs.zIndex !== 'auto' && (cs.position !== 'static' || flexItem)) || /fixed|sticky/.test(cs.position) ||
      parseFloat(cs.opacity) < 1 || cs.isolation === 'isolate' || (cs.mixBlendMode && cs.mixBlendMode !== 'normal') ||
      /paint|layout|strict|content/.test(cs.contain || '') || cs.transform !== 'none' || cs.filter !== 'none';
  };

  H.paintOrder = el => {
    const pd = getComputedStyle(el).display;
    const flexish = /flex|grid/.test(pd);
    return [...el.children].map((c, i) => {
      const cs = getComputedStyle(c);
      const positioned = cs.position !== 'static';
      const z = cs.zIndex === 'auto' || !(positioned || flexish) ? null : parseInt(cs.zIndex);
      const group = z !== null && z < 0 ? 0 : z !== null && z > 0 ? 3 : positioned || z === 0 ? 2 : 1;
      return { c, i, group, z: z || 0 };
    }).sort((a, b) => a.group - b.group || a.z - b.z || a.i - b.i).map(x => x.c);
  };

  window.__HP = H;
}

// --- 決まりの HTML ------------------------------------------------------------------------
function contractMain() {
  const H = window.__HP;
  const FX = ['rise', 'fade', 'pop', 'wipe-right', 'wipe-left', 'wipe-up', 'wipe-down', 'wheel'];
  const TRANS = ['morph', 'fade', 'none'];
  const violations = [];
  const sections = [...document.querySelectorAll('section[data-slide]')];
  const V = (slide, el, rule, detail, level = 'error', stop = null) => violations.push({
    slide, el: !el ? null : el.matches('section[data-slide]') ? `section${el.id ? '#' + el.id : ''}` : H.desc(el, stop), rule, detail, level });
  for (const f of document.fonts) {
    if (f.status === 'error') V(0, null, 'font-load', `字体「${f.family.replace(/"/g, '')}」を読めない（@font-face の src を確かめる。読めないと別の字体で測る）`, 'warn');
  }
  if (!sections.length) V(0, null, 'no-slide', 'section[data-slide] が1つも無い');

  const notesText = aside => {
    // <br>・<p>・<div>・<li> を改行に。行ごとに前後の空白を落とす
    let s = '';
    const walk = n => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) s += c.textContent.replace(/[ \t\r\n]+/g, ' ');
        else if (c.nodeType === 1) {
          const t = c.tagName.toLowerCase();
          if (t === 'br') { s += '\n'; continue; }
          const block = /^(p|div|li|ul|ol|h[1-6]|blockquote)$/.test(t);
          if (block) s += '\n';
          walk(c);
          if (block) s += '\n';
        }
      }
    };
    walk(aside);
    return s.split('\n').map(l => l.trim()).filter(Boolean).join('\n');
  };

  const slides = [];
  sections.forEach((sec, si) => {
    const no = si + 1;
    for (const s of sections) s.style.display = s === sec ? '' : 'none';
    const o = sec.getBoundingClientRect();
    if (Math.abs(o.width - 1920) > 1 || Math.abs(o.height - 1080) > 1)
      V(no, sec, 'slide-size', `section の大きさが ${o.width}×${o.height}（1920×1080 にする）`);
    const tr = sec.getAttribute('data-transition') || (si === 0 ? 'fade' : 'morph');
    if (!TRANS.includes(tr)) V(no, sec, 'bad-transition', `data-transition="${tr}"（${TRANS.join(' / ')} のどれか）`);
    const asides = [...sec.querySelectorAll('aside.notes')];
    const notes = asides.map(notesText).filter(Boolean).join('\n');
    const inNotes = el => asides.some(a => a.contains(el));

    // 単位: data-step を持つ一番外側の祖先（入れ子の内側は無視）。無ければ data-keep・data-name を持つ一番近い祖先
    const ids = new Map(), units = {};
    let seq = 0;
    const unitEl = el => {
      let outer = null;
      for (let n = el; n && n !== sec; n = n.parentNode) if (n.nodeType === 1 && n.hasAttribute('data-step')) outer = n;
      if (outer) return outer;
      for (let n = el; n && n !== sec; n = n.parentNode) if (n.nodeType === 1 && (n.hasAttribute('data-keep') || n.hasAttribute('data-name'))) return n;
      return null;
    };
    const unitOf = el => {
      const u = unitEl(el);
      if (!u) return null;
      if (!ids.has(u)) {
        const id = `u${++seq}`;
        ids.set(u, id);
        const st = u.getAttribute('data-step');
        const step = st !== null && /^\d+$/.test(st.trim()) ? parseInt(st) : null;
        const dl = u.getAttribute('data-delay');
        units[id] = {
          step, fx: step === null ? null : (u.getAttribute('data-fx') || 'rise'),
          delay: dl && /^\d+(\.\d+)?$/.test(dl.trim()) ? Math.round(parseFloat(dl)) : 0,
          keep: u.getAttribute('data-keep') || null, name: u.getAttribute('data-name') || u.id || null,
        };
      }
      return ids.get(u);
    };
    const nameOf = (el, suffix) => {
      const n = el.getAttribute && (el.getAttribute('data-name') || el.id);
      return n ? (suffix ? `${n}/${suffix}` : n) : null;
    };

    // 決まり違反
    const outside = new Set(), overflow = [];
    const isSvgChild = el => el.ownerSVGElement && el.tagName.toLowerCase() !== 'svg';
    const hiddenDeep = el => { for (let n = el; n && n !== sec; n = n.parentNode) if (getComputedStyle(n).display === 'none') return true; return false; };
    for (const el of [sec, ...sec.querySelectorAll('*')]) {
      if (inNotes(el) || (el !== sec && hiddenDeep(el))) continue;
      const tag = el.tagName.toLowerCase();
      if (isSvgChild(el)) {
        if (['image', 'foreignobject', 'use', 'video'].includes(tag)) V(no, el, 'unsupported-svg', `SVG の <${tag}> は写せない（rect・circle・path などの基本図形と text だけ）`, 'error', sec);
        const cs = getComputedStyle(el);
        if (/^url\(/.test(cs.fill) || /^url\(/.test(cs.stroke)) V(no, el, 'svg-paint', 'SVG の塗り・線にグラデーションや模様（url(#…)）は写せない（色を付けずに描かれる）', 'warn', sec);
        continue;
      }
      const cs = getComputedStyle(el);
      const D = (rule, detail, level = 'error') => V(no, el, rule, detail, level, sec);
      if (cs.transform !== 'none' || (cs.rotate && cs.rotate !== 'none') || (cs.scale && cs.scale !== 'none') || (cs.translate && cs.translate !== 'none'))
        D('transform', `transform: ${cs.transform}（位置は left・top・flex で決める）`);
      if (cs.filter !== 'none') D('filter', `filter: ${cs.filter}`);
      if (cs.backdropFilter && cs.backdropFilter !== 'none') D('backdrop-filter', `backdrop-filter: ${cs.backdropFilter}`);
      if (cs.clipPath !== 'none') D('clip-path', `clip-path: ${cs.clipPath}`);
      if ((cs.maskImage && cs.maskImage !== 'none') || (cs.webkitMaskImage && cs.webkitMaskImage !== 'none')) D('mask', 'mask は写せない');
      if (/url\(/.test(cs.backgroundImage)) D('background-image', '背景画像（url）は写せない（<img> で置く）');
      for (const ps of ['::before', '::after']) {
        const c = getComputedStyle(el, ps).content;
        if (c && c !== 'none' && c !== 'normal') D('pseudo-content', `${ps} に content: ${c}（中身のある疑似要素は写せない。要素にする）`);
      }
      if (/^(vertical|sideways)/.test(cs.writingMode)) D('writing-mode', `writing-mode: ${cs.writingMode}（縦書きは写せない）`);
      if (['canvas', 'video', 'iframe', 'object', 'embed', 'audio'].includes(tag)) D('unsupported-element', `<${tag}> は写せない`);
      if (tag === 'img') {
        const src = el.currentSrc || el.src || '';
        if (/^https?:/.test(src)) D('external-image', `画像はローカルのファイルにする（${src.slice(0, 80)}）`);
        else if (!el.naturalWidth) D('image-load', `画像を読めない（${src.slice(0, 120)}）`);
        else if (/\.svg($|\?)/i.test(src) || src.startsWith('data:image/svg')) D('image-format', 'SVG の画像は写せない（インラインの <svg> にするか png にする）');
      }
      // z-index は、重ね順の文脈（stacking context）を作る親の直接の子どうしでだけ使う（祖先をまたぐ重ね順は写せない）
      if (el !== sec && el.parentElement && el.parentElement !== sec && cs.zIndex !== 'auto' &&
          (cs.position !== 'static' || /flex|grid/.test(getComputedStyle(el.parentElement).display)) && !H.makesContext(el.parentElement))
        D('z-index-context', `z-index: ${cs.zIndex} が、重ね順の文脈を作らない親の中にある（親に position と z-index を付けるか、section の直下に置く）`);
      if (el !== sec && cs.order && cs.order !== '0' && el.parentElement && /flex|grid/.test(getComputedStyle(el.parentElement).display))
        D('order', `order: ${cs.order}（並べ替えると重なり順が変わり、写せない。HTML の順に書く）`);
      if (el.hasAttribute('data-step')) {
        const st = el.getAttribute('data-step').trim();
        if (H.isInline(el)) D('inline-step', 'data-step はブロック要素に付ける（文字の中の inline 要素は文字と一緒に出るので、別に出せない）');
        if (!/^\d+$/.test(st)) D('bad-step', `data-step="${st}"（0 以上の整数）`);
        if (el.parentElement && el.parentElement.closest('[data-step]') && sec.contains(el.parentElement.closest('[data-step]')))
          D('nested-step', 'data-step の中の data-step は無視する（外側の段で出る）', 'warn');
        const fx = el.getAttribute('data-fx');
        if (fx !== null && !FX.includes(fx)) D('bad-fx', `data-fx="${fx}"（${FX.join(' / ')} のどれか）`);
        const dl = el.getAttribute('data-delay');
        if (dl !== null && !/^\d+(\.\d+)?$/.test(dl.trim())) D('bad-delay', `data-delay="${dl}"（0 以上の ms）`);
      } else if (el.hasAttribute('data-fx') || el.hasAttribute('data-delay')) {
        D('fx-without-step', 'data-fx・data-delay は data-step と一緒に付ける（このままでは最初から出ている）', 'warn');
      }
      if (el === sec) continue;
      // section の外にはみ出す（祖先がもう出ていれば重ねて出さない）
      const r = el.getBoundingClientRect();
      if ((r.width > 0 || r.height > 0) && parseFloat(cs.opacity) > 0 &&
          (r.left < o.left - 1 || r.top < o.top - 1 || r.right > o.right + 1 || r.bottom > o.bottom + 1)) {
        let anc = false;
        for (let n = el.parentNode; n && n !== sec; n = n.parentNode) if (outside.has(n)) { anc = true; break; }
        outside.add(el);
        if (!anc) D('outside-slide', `section の外にはみ出す（${Math.round(r.left - o.left)},${Math.round(r.top - o.top)},${Math.round(r.width)}×${Math.round(r.height)}）`);
      }
      // 文字のはみ出し（文字を含む箱で、中身が箱より大きい。いちばん内側だけ出す）
      if (!H.isInline(el) && el.clientWidth > 0 && (el.textContent || '').trim() &&
          (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) overflow.push(el);
    }
    for (const el of overflow) {
      if (overflow.some(x => x !== el && el.contains(x))) continue;
      V(no, el, 'text-overflow', `文字が箱からはみ出す（中身 ${el.scrollWidth}×${el.scrollHeight} > 箱 ${el.clientWidth}×${el.clientHeight}。文を短くする）`, 'error', sec);
    }

    // 部品
    const prims = [];
    const ctx = { origin: o, root: sec, unitOf, nameOf, skip: el => inNotes(el) };
    for (const c of H.paintOrder(sec)) H.walk(c, prims, ctx);
    // 背景（section → body → html の順に、最初に色のあるもの）
    let background = null;
    for (const n of [sec, document.body, document.documentElement]) {
      const cs = getComputedStyle(n);
      const g = H.layers(cs.backgroundImage).map(H.gradient).find(Boolean);
      if (g && g.kind === 'linear') { background = { type: 'grad', angle: g.angle, stops: g.stops }; break; }
      const c = H.col(cs.backgroundColor) || (g ? g.stops[g.stops.length - 1].color : null);
      if (c) { background = { type: 'solid', color: c }; break; }
      if (n === sec && /url\(/.test(cs.backgroundImage)) break;
    }
    // 何も描かない data-step（中身が空・透明）は段に数えない（PowerPoint に効果が出ないため）
    const drawn = new Set(prims.map(p => p.unit));
    for (const [el, id] of ids) {
      if (units[id].step !== null && !drawn.has(id)) V(no, el, 'empty-step', 'data-step の要素が何も描かない（段に数えない）', 'warn', sec);
    }
    // 段の数とノートの［クリック］
    const steps = new Set(Object.entries(units).filter(([id]) => drawn.has(id)).map(([, u]) => u.step).filter(s => s !== null && s >= 1)).size;
    const said = (notes.match(/［クリック］/g) || []).length;
    if (steps !== said) V(no, sec, 'notes-clicks', `段の数 ${steps} とノートの［クリック］${said} 個が合わない`);
    if (/\[クリック\]/.test(notes)) V(no, sec, 'notes-ascii-click', 'ノートの [クリック] は全角の［クリック］で書く（半角は数えない）', 'warn');
    slides.push({ id: sec.id || `s${no}`, transition: tr, background, notes, prims, units, steps, notes_clicks: said });
  });
  for (const s of sections) s.style.display = '';
  return { slides, violations, title: document.title || '' };
}

// --- html-video の HTML（互換） -------------------------------------------------------------
function hyperMain() {
  const H = window.__HP;
  const tl = window.__timelines && window.__timelines.main;
  if (!tl) throw new Error('window.__timelines.main が無い（html-video が焼いた project/index.html か確かめる）');
  const rootEl = document.getElementById('root');
  const O = rootEl.getBoundingClientRect();
  // 印（m{n}-…-0 / -1 / -x）は1つの単位にまとめる
  const key = id => (/^m\d+-/.test(id) ? id.replace(/-(\d+|x)$/, '') : id);
  const anims = {};
  for (const tw of tl.getChildren(true, true, false)) {
    const v = tw.vars || {};
    for (const t of tw.targets()) {
      if (!t || !t.id) continue;
      const k = key(t.id), s = tw.startTime();
      if (/^m\d+-/.test(t.id)) { if (!anims[k] || s < anims[k].t) anims[k] = { t: s, kind: 'mark' }; continue; }
      if (anims[k] || v.opacity !== 1 || !(tw.duration() > 0)) continue;
      anims[k] = { t: s, kind: 'scale' in v ? 'pop' : 'scaleX' in v ? 'grow' : 'y' in v ? 'rise' : 'x' in v ? 'slide' : 'fade' };
    }
  }
  const scenes = [...document.querySelectorAll('#board > .scene')];
  const chapters = {};
  for (const c of document.querySelectorAll('.chapter')) chapters[c.id.replace(/-ch$/, '')] = c;
  const hideAll = () => { for (const s of scenes) s.style.display = 'none'; for (const c of Object.values(chapters)) c.style.display = 'none'; };
  const nameOf = (el, suffix) => (el.id ? (suffix ? `${el.id}/${suffix}` : el.id) : null);

  // 外枠（ボード・桟・キャラ・演台）。最初の画面の頭で取り、全画面に「!!」付きの名前で置く
  hideAll();
  tl.seek(0.01, false);
  const frame = [];
  const fctx = (u) => ({ origin: O, root: rootEl, unitOf: () => u, nameOf });
  const board = document.getElementById('board');
  H.box(board, getComputedStyle(board), frame, fctx('frame-board'), 'frame-board', 1);
  const rail = H.pseudoBox(board, '::before', fctx('frame-rail'), 'frame-rail', 'rail');
  if (rail) frame.push(rail);
  const pose = document.getElementById('c-basic-closed') || document.querySelector('#chara img.pose');
  if (pose && pose.naturalWidth) frame.push(H.img(pose, getComputedStyle(pose), fctx('frame-chara'), 'frame-chara', 1));
  const desk = document.getElementById('desk');
  if (desk) H.walk(desk, frame, fctx('frame-desk'));
  const frameUnits = {
    'frame-board': { step: null, keep: 'board', name: 'board' }, 'frame-rail': { step: null, keep: 'rail', name: 'rail' },
    'frame-chara': { step: null, keep: 'chara', name: 'chara' }, 'frame-desk': { step: null, keep: 'desk', name: 'desk' },
  };
  const rootCs = getComputedStyle(rootEl);
  const lin = H.layers(rootCs.backgroundImage).map(H.gradient).filter(g => g && g.kind === 'linear').pop();
  const rootBg = lin ? { type: 'grad', angle: lin.angle, stops: lin.stops }
    : { type: 'solid', color: H.col(rootCs.backgroundColor) || { hex: 'ffffff', a: 1 } };

  const slides = [];
  for (const sc of scenes) {
    hideAll();
    const a = +sc.dataset.start, b = a + (+sc.dataset.duration);
    const ch = chapters[sc.id];
    const container = ch || sc;
    container.style.display = '';
    // 章カードは入りが終わって消える前（花びらが舞っている途中）、ふつうの画面は終わる直前
    tl.seek(ch ? Math.min(a + 1.0, b - 0.25) : b - 0.02, false);
    const unitOf = el => {
      for (let n = el; n && n !== container; n = n.parentNode) if (n.id && anims[key(n.id)]) return key(n.id);
      return null;
    };
    const prims = ch ? [] : frame.map(p => ({ ...p }));
    const ctx = { origin: O, root: rootEl, unitOf, nameOf };
    let background = rootBg;
    if (ch) {
      const cs = getComputedStyle(ch);
      const g = H.layers(cs.backgroundImage).map(H.gradient).find(Boolean);
      const c = H.col(cs.backgroundColor) || (g ? g.stops[g.stops.length - 1].color : null);
      if (c) background = { type: 'solid', color: { ...c, a: 1 } };
    }
    for (const c of H.paintOrder(container)) H.walk(c, prims, ctx);
    const units = {};
    for (const p of prims) {
      const u = p.unit;
      if (!u || units[u]) continue;
      if (frameUnits[u]) { units[u] = { ...frameUnits[u], fx: null, delay: 0 }; continue; }
      const an = anims[u];
      // 外側で出る単位（show の対象の中に、自分も出方を持つ要素があるとき。from_video が対象の文を引き継ぐ）
      let parent = null;
      const el = document.getElementById(u);
      if (el) for (let n = el.parentNode; n && n !== container; n = n.parentNode) if (n.id && anims[key(n.id)]) { parent = key(n.id); break; }
      units[u] = { step: null, fx: null, delay: 0, keep: null, name: u.replace(`${sc.id}-`, ''), t: an ? Math.round(an.t * 1000) / 1000 : null,
        kind: an ? an.kind : null, parent };
    }
    slides.push({ id: sc.id, start: a, end: Math.round(b * 1000) / 1000, chapter: !!ch, transition: null, background, notes: '', prims, units });
  }
  return { slides, violations: [], title: document.title || '', canvas: { w: Math.round(O.width), h: Math.round(O.height) } };
}

// --- 本体 ------------------------------------------------------------------------------------
async function main() {
  const args = process.argv.slice(2);
  let mode = 'contract';
  const pos = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--mode') mode = args[++i];
    else pos.push(args[i]);
  }
  const [html, outJson] = pos;
  if (!html || !outJson || !['contract', 'hyperframes'].includes(mode)) die('使い方: node extract.mjs --mode contract|hyperframes <html> <out.json>', 1);
  const htmlPath = path.resolve(fs.existsSync(html) && fs.statSync(html).isDirectory() ? path.join(html, 'index.html') : html);
  if (!fs.existsSync(htmlPath)) die(`HTML が無い: ${htmlPath}`, 1);
  let puppeteer;
  try {
    puppeteer = require(path.join(NM, 'puppeteer-core'));
  } catch (e) {
    die(`puppeteer-core が無い（${NM}）。${SETUP_HINT}`, 4);
  }
  const exe = chromePath();
  if (!exe) die(`Chrome（headless shell）が無い（${CHROME_DIR}）。${SETUP_HINT}`, 4);
  const browser = await puppeteer.launch({ executablePath: exe, args: ['--allow-file-access-from-files', '--font-render-hinting=none'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
    await page.goto('file://' + htmlPath, { waitUntil: 'load', timeout: 60000 });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(installLib);
    const data = await page.evaluate(mode === 'contract' ? contractMain : hyperMain);
    const out = { version: 1, mode, source: htmlPath, size: { w: 1920, h: 1080 }, ...data };
    fs.writeFileSync(outJson, JSON.stringify(out));
    const errs = out.violations.filter(v => v.level === 'error').length;
    process.stdout.write(`extract: ${mode} slides=${out.slides.length} prims=${out.slides.reduce((n, s) => n + s.prims.length, 0)} violations=${errs}\n`);
  } finally {
    await browser.close();
  }
}

main().catch(e => die(e && e.stack ? e.stack : String(e), 1));
