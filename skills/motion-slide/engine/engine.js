/* motion-slide エンジン。場面（.scene）×拍で進む Web プレゼンを動かす。
   台本が書くのは場面の中身と MS.hook だけ。道しるべ・一覧・自動再生・効果音・BGM はここが持つ。
   決まりの正本は references/authoring.md */
(() => {
  // 設定は init で読む（エンジンは <head> の先頭に入るので、この時点では台本の ms-config がまだ無い）
  const CFG = { crumb: "", chapters: [], ambient: true, dur: 4200, sfx: true };
  let LOGO = null, THUMBS = {};
  const HOOKS = {}, READY = [];
  let stage, scenes = [], S = 0, B = 0, K = 1, playing = false, playT = null, last = { S: -1, B: -1 };

  // ── タイマー（拍が変わったら全部消す。戻っても残骸が出ない） ──
  const timers = new Set();
  const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); return t; };
  const every = (fn, ms) => { const t = setInterval(fn, ms); timers.add(t); return t; };
  const clearTimers = () => { timers.forEach(t => { clearTimeout(t); clearInterval(t); }); timers.clear(); };

  // ── 効果音（Web Audio で合成。ファイルは持たない） ──
  const SFX = { on: true, ctx: null, master: null, last: {}, log: [] };
  function audio() {
    if (!SFX.ctx) { const C = window.AudioContext || window.webkitAudioContext; if (!C) return null;
      SFX.ctx = new C(); SFX.master = SFX.ctx.createGain(); SFX.master.gain.value = 0.5; SFX.master.connect(SFX.ctx.destination); }
    if (SFX.ctx.state === "suspended") SFX.ctx.resume();
    return SFX.ctx;
  }
  function noise(ac, dur) {
    const b = ac.createBuffer(1, Math.ceil(ac.sampleRate * dur), ac.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    const s = ac.createBufferSource(); s.buffer = b; return s;
  }
  const env = (g, t, peak, a, r) => { g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + a); g.gain.exponentialRampToValueAtTime(0.0001, t + a + r); };
  const SOUNDS = {
    pop(ac, t) { const o = ac.createOscillator(), g = ac.createGain(); o.frequency.setValueAtTime(420, t); o.frequency.exponentialRampToValueAtTime(900, t + .06); env(g, t, .45, .008, .12); o.connect(g).connect(SFX.master); o.start(t); o.stop(t + .2); },
    ping(ac, t) { for (const [f, v] of [[1320, .22], [1980, .1]]) { const o = ac.createOscillator(), g = ac.createGain(); o.frequency.value = f; env(g, t, v, .005, .4); o.connect(g).connect(SFX.master); o.start(t); o.stop(t + .5); } },
    whoosh(ac, t) { const n = noise(ac, .6), f = ac.createBiquadFilter(), g = ac.createGain(); f.type = "bandpass"; f.Q.value = 1.2;
      f.frequency.setValueAtTime(500, t); f.frequency.exponentialRampToValueAtTime(2600, t + .5); env(g, t, .16, .25, .3); n.connect(f).connect(g).connect(SFX.master); n.start(t); n.stop(t + .6); },
    swish(ac, t) { const n = noise(ac, .35), f = ac.createBiquadFilter(), g = ac.createGain(); f.type = "bandpass"; f.Q.value = 2;
      f.frequency.setValueAtTime(1500, t); f.frequency.exponentialRampToValueAtTime(4200, t + .3); env(g, t, .07, .08, .25); n.connect(f).connect(g).connect(SFX.master); n.start(t); n.stop(t + .35); },
    pen(ac, t) { const n = noise(ac, .95), f = ac.createBiquadFilter(), g = ac.createGain(), l = ac.createOscillator(), lg = ac.createGain();
      f.type = "bandpass"; f.frequency.value = 3200; f.Q.value = .9; l.frequency.value = 16; lg.gain.value = .05; l.connect(lg).connect(g.gain);
      env(g, t, .09, .05, .85); n.connect(f).connect(g).connect(SFX.master); n.start(t); n.stop(t + .95); l.start(t); l.stop(t + .95); },
    buzz(ac, t) { const f = ac.createBiquadFilter(), g = ac.createGain(); f.type = "lowpass"; f.frequency.value = 700;
      for (const hz of [110, 113.5]) { const o = ac.createOscillator(); o.type = "sawtooth"; o.frequency.value = hz; o.connect(f); o.start(t); o.stop(t + .3); }
      env(g, t, .22, .01, .26); f.connect(g).connect(SFX.master); },
    tick(ac, t) { const o = ac.createOscillator(), g = ac.createGain(); o.type = "square"; o.frequency.value = 2200; env(g, t, .05, .002, .025); o.connect(g).connect(SFX.master); o.start(t); o.stop(t + .04); },
    tap(ac, t) { const o = ac.createOscillator(), g = ac.createGain(); o.frequency.value = 1150; env(g, t, .045, .003, .05); o.connect(g).connect(SFX.master); o.start(t); o.stop(t + .07); },
  };
  function sfx(name, gap = 60) {
    if (!SFX.on) return; const ac = audio(); if (!ac) return;
    const now = performance.now(); if (now - (SFX.last[name] || 0) < gap) return; SFX.last[name] = now;
    try { SOUNDS[name](ac, ac.currentTime + .01); SFX.log.push({ n: name, t: performance.now() }); } catch (e) { /* 音が出せない環境では黙る */ }
  }

  // ── 位置（舞台の座標。拡大縮小を戻す） ──
  function rel(el) {
    const r = el.getBoundingClientRect(), s = stage.getBoundingClientRect();
    return { x: (r.left - s.left) / K, y: (r.top - s.top) / K, w: r.width / K, h: r.height / K };
  }
  const svgEl = (tag, attrs) => { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  // a の右端から b の左端へ曲線を引く。opt: fromLeft / toRight / dash / color
  function wire(svgId, a, b, opt = {}) {
    if (!a || !b) return;
    const svg = document.getElementById(svgId), o = rel(svg), off = r => ({ x: r.x - o.x, y: r.y - o.y, w: r.w, h: r.h });   // SVG の置き場からの座標にする
    const A = off(rel(a)), Bb = off(rel(b));
    const x1 = opt.fromLeft ? A.x : A.x + A.w, y1 = A.y + A.h / 2, x2 = opt.toRight ? Bb.x + Bb.w : Bb.x, y2 = Bb.y + Bb.h / 2;
    const dx = Math.max(60, Math.abs(x2 - x1) / 2) * (opt.fromLeft ? -1 : 1);
    const p = svgEl("path", { d: `M${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx * (opt.toRight ? -1 : 1)} ${y2}, ${x2} ${y2}`, class: "w" });
    if (opt.color) p.style.stroke = opt.color;
    if (opt.dash) { p.style.strokeDasharray = "4 8"; p.style.animation = "none"; }
    svg.appendChild(p); sfx("swish", 120);
    if (!opt.dash) p.style.setProperty("--len", p.getTotalLength());
    svg.appendChild(svgEl("circle", { cx: x2, cy: y2, r: 5, class: "dot" }));
  }
  // 要素を手書きの丸で囲む
  function hand(svgId, el, color = "var(--pri)") {
    const svg = document.getElementById(svgId), o = rel(svg), r = rel(el), cx = r.x - o.x + r.w / 2, cy = r.y - o.y + r.h / 2;
    const rx = r.w / 2 + 34, ry = r.h / 2 + 30; let d = "";
    for (let i = 0; i <= 64; i++) {
      const t = -0.6 + i / 64 * Math.PI * 2.12, wob = 1 + Math.sin(i * 1.7) * 0.02 + (i / 64) * 0.05;
      d += (i ? " L" : "M") + (cx + Math.cos(t) * rx * wob).toFixed(1) + " " + (cy + Math.sin(t) * ry * wob).toFixed(1);
    }
    const p = svgEl("path", { d, class: "hand" }); p.style.stroke = color; svg.appendChild(p); sfx("pen");
    p.style.setProperty("--len", p.getTotalLength());
  }
  // src の位置から dst の位置へ、文字（または複製した見た目）を飛ばす。着いたら onLand(dst)
  function fly(src, dst, opt = {}) {
    if (!src || !dst) return;
    const scene = src.closest(".scene") || stage, a = rel(src), d = rel(dst), f = document.createElement("div"), cs = getComputedStyle(src);
    f.className = "flyer" + (opt.cls ? " " + opt.cls : "");
    if (opt.html != null) f.innerHTML = opt.html; else f.textContent = opt.text ?? src.textContent.trim();
    Object.assign(f.style, { left: a.x + "px", top: a.y + "px", fontSize: cs.fontSize, fontWeight: cs.fontWeight, fontFamily: cs.fontFamily });
    if (opt.box) Object.assign(f.style, { width: a.w + "px", height: a.h + "px" });
    scene.appendChild(f); sfx("whoosh", 120);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      Object.assign(f.style, { left: d.x + (opt.dx ?? 0) + "px", top: d.y + (opt.dy ?? 0) + "px" });
      if (opt.fs) f.style.fontSize = opt.fs;
      if (opt.box) Object.assign(f.style, { width: d.w + "px", height: d.h + "px" });
    }));
    later(() => { if (opt.onLand) opt.onLand(dst); sfx("pop", 30); f.style.opacity = 0; later(() => f.remove(), 350); }, 980);
  }
  // 数を数え上げる（拍が変わると止まる）
  function countUp(el, to, ms = 1200, opt = {}) {
    const t0 = performance.now(), from = opt.from ?? 0, fmt = opt.fmt || (v => Math.round(v).toLocaleString());
    const id = every(() => {
      const p = Math.min(1, (performance.now() - t0) / ms), v = from + (to - from) * (1 - Math.pow(1 - p, 3));
      el.textContent = fmt(v); if (opt.tick) sfx("tick", 70);
      if (p >= 1) { clearInterval(id); timers.delete(id); }
    }, 40);
  }

  // ── キー付きの表（同じ鍵のマスは動いて変わる。増えたマスは出てきて、消えたマスは消える） ──
  // spec: {name, at:[x,y], cols:[{k,label?,w}], rows:[{k, <列>:値}], rh?, hlCol, hlRow, headHl:[k], mark:{"行:列":cls}}
  const TB = {};
  function table(id, spec) {
    let t = TB[id];
    if (!t) { const el = document.getElementById(id); el.classList.add("mstable"); el.innerHTML = '<div class="tname"></div><div class="tcells"></div>';
      t = TB[id] = { el, name: el.firstChild, box: el.lastChild, cells: new Map() }; }
    t.name.textContent = spec.name || "";
    const HH = spec.hh || 52, RH = spec.rh || 56, xs = {}; let x = 0;
    for (const c of spec.cols) { xs[c.k] = x; x += c.w; }
    const want = new Map();
    spec.cols.forEach(c => want.set("H:" + c.k, { x: xs[c.k], y: 0, w: c.w, h: HH, html: c.label ?? c.k, cls: "head" + ((spec.headHl || []).includes(c.k) ? " hl" : "") }));
    spec.rows.forEach((r, i) => spec.cols.forEach(c => {
      const key = r.k + ":" + c.k; let cls = "cell";
      if (spec.hlCol === c.k || spec.hlRow === r.k) cls += " hl";
      if (spec.mark && spec.mark[key]) cls += " " + spec.mark[key];
      want.set(key, { x: xs[c.k], y: HH + i * RH, w: c.w, h: RH, html: String(r[c.k] ?? ""), cls });
    }));
    for (const [k, v] of want) {
      let d = t.cells.get(k), fresh = false;
      if (!d) { d = document.createElement("div"); t.box.appendChild(d); t.cells.set(k, d); fresh = true; d.innerHTML = v.html; }
      clearTimeout(d._rm);
      Object.assign(d.style, { left: v.x + "px", top: v.y + "px", width: v.w + "px", height: v.h + "px" });
      const changed = !fresh && d.innerHTML !== v.html;
      if (changed) d.innerHTML = v.html;
      d.className = "tc " + v.cls + (fresh ? " enter" : "");
      if (fresh) requestAnimationFrame(() => requestAnimationFrame(() => d.classList.remove("enter")));
      if (changed) { void d.offsetWidth; d.classList.add("flash"); }
    }
    for (const [k, d] of t.cells) if (!want.has(k)) {
      d.classList.add("leave"); clearTimeout(d._rm);
      d._rm = setTimeout(() => { d.remove(); t.cells.delete(k); }, 450);
    }
    Object.assign(t.box.style, { width: x + "px", height: HH + spec.rows.length * RH + "px" });
    if (spec.at) Object.assign(t.el.style, { left: spec.at[0] + "px", top: spec.at[1] + "px" });
    return t;
  }
  const cell = (id, key) => TB[id] && TB[id].cells.get(key);

  // ── 道しるべ ──
  const $ = id => document.getElementById(id);
  function buildChrome() {
    const add = html => stage.insertAdjacentHTML("beforeend", html);
    if (CFG.ambient) stage.insertAdjacentHTML("afterbegin", `<svg id="ms-ambient" viewBox="0 0 1920 1080" preserveAspectRatio="none">
      <path d="M-100 820 C 300 640, 620 980, 1000 760 S 1700 520, 2050 640"/><path d="M-100 300 C 400 460, 700 120, 1150 300 S 1700 520, 2050 260"/>
      <path d="M1500 -60 C 1380 300, 1700 520, 1520 800 S 1600 1080, 1700 1160"/></svg>`);
    add(`<div id="ms-crumb"></div><div id="ms-dots"></div><div id="ms-rail"><div class="ch" id="ms-railch"></div><ol id="ms-raillist"></ol></div>
      <div id="ms-terms"></div><div id="ms-bar"></div>
      <div id="ms-ctrl"><span class="pos" id="ms-pos"></span><button id="ms-play">▶ 自動再生</button><button id="ms-sfx"></button></div>
      <div id="ms-notes"></div><div id="ms-ov"><h2>${esc(document.title)}</h2><div class="meta" id="ms-ovmeta"></div><div class="chs" id="ms-ovchs"></div></div>`);
    if (LOGO) {
      const [cx, cy, cw, ch] = LOGO.crop, [nw, nh] = LOGO.size, h = LOGO.height || 34, k = h / ch;
      add(`<div id="ms-logo" role="img" aria-label="${esc(LOGO.alt || "")}" style="width:${cw * k}px;height:${h}px;background-image:url(${LOGO.src});background-size:${nw * k}px ${nh * k}px;background-position:${-cx * k}px ${-cy * k}px"></div>`);
    }
    $("ms-play").addEventListener("click", e => { e.stopPropagation(); if (!playing && S === scenes.length - 1 && B === scenes[S].beats - 1) go(0, 0); setPlay(!playing); });
    $("ms-sfx").addEventListener("click", e => { e.stopPropagation(); setSfx(!SFX.on); });
    $("ms-ov").addEventListener("click", e => { e.stopPropagation(); const t = e.target.closest("[data-s]"); if (!t) return; go(+t.dataset.s, t.dataset.b != null ? +t.dataset.b : 0); toggleOv(false); });
    setSfx(SFX.on, true);
  }
  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const chName = c => `${String.fromCharCode(65 + c)}　${CFG.chapters[c]}`;
  function chrome() {
    const sc = scenes[S], hasCh = CFG.chapters.length && !sc.norail;
    $("ms-crumb").innerHTML = `${esc(CFG.crumb)} › ${hasCh ? esc(CFG.chapters[sc.ch]) + " › " : ""}<b>${esc(sc.title)}</b><i>●</i>`;
    $("ms-dots").innerHTML = Array.from({ length: sc.beats }, (_, i) => `<span class="${i < B ? "done" : i === B ? "now" : ""}"></span>`).join("");
    $("ms-rail").style.opacity = hasCh ? 1 : 0;
    if (hasCh) {
      $("ms-railch").textContent = chName(sc.ch);
      $("ms-raillist").innerHTML = scenes.filter(x => x.ch === sc.ch && !x.norail).map((x, k) =>
        `<li class="${x.i === S ? "now" : x.i < S ? "done" : ""}"><span class="n">${String(k + 1).padStart(2, "0")}</span>${esc(x.title)}</li>`).join("");
    }
    const terms = scenes.filter(x => x.term && x.i <= S), keep = CFG.terms ?? 6, hid = Math.max(0, terms.length - keep);   // 増えすぎたら直近だけ
    $("ms-terms").innerHTML = (terms.length ? '<span class="lbl">ここまでの言葉</span>' : "") + (hid ? `<span class="more">+${hid}</span>` : "") +
      terms.slice(hid).map(x => `<span class="t ${x.i === S ? "new" : ""}">${esc(x.term)}</span>`).join("");
    const total = scenes.reduce((a, x) => a + x.beats, 0), done = scenes.slice(0, S).reduce((a, x) => a + x.beats, 0) + B + 1;
    $("ms-bar").style.width = (done / total * 100) + "%";
    $("ms-pos").textContent = `${done} / ${total}`;
    $("ms-notes").textContent = sc.notes[B] ?? sc.notes[0] ?? "";
  }
  function toggleOv(on) {
    if (on) {
      const total = scenes.reduce((a, x) => a + x.beats, 0);
      $("ms-ovmeta").textContent = `${scenes.length} 場面 ・ ${total} 拍 ・ クリックでその拍へ ・ Esc で閉じる`;
      // 話の順に1つの格子で並べる。章は札（A・B…）で見分け、章の頭のカードにだけ章の名前を出す
      const seq = {};
      $("ms-ovchs").innerHTML = `<div class="row">${scenes.map(x => {
        const hasCh = CFG.chapters.length && !x.norail, first = hasCh && !scenes.some(y => y.i < x.i && y.ch === x.ch && !y.norail);
        const n = hasCh ? (seq[x.ch] = (seq[x.ch] || 0) + 1) : 0;
        const tag = hasCh ? `<span class="cht">${String.fromCharCode(65 + x.ch)}-${String(n).padStart(2, "0")}</span>` : "";
        return `<div class="sc ${x.i === S ? "now" : ""} ${first ? "first" : ""}" data-s="${x.i}">` +
          `<div class="chn">${first ? esc(chName(x.ch)) : "&nbsp;"}</div>` +
          (THUMBS[x.el.id] ? `<img class="th" alt="" src="${THUMBS[x.el.id]}">` : "") +
          `<div class="tt">${tag}${esc(x.title)}</div><div class="bt">${Array.from({ length: x.beats }, (_, k) => `<span data-s="${x.i}" data-b="${k}">${k + 1}</span>`).join("")}</div></div>`;
      }).join("")}</div>`;
    }
    $("ms-ov").classList.toggle("show", on);
  }

  // ── 拍を当てる（その拍の見た目を丸ごと決める。どこから来ても同じになる） ──
  const range = (x, a, b) => { const i = +x.dataset[a], o = x.dataset[b] != null ? +x.dataset[b] : 1e9; return [i, o]; };
  function apply() {
    clearTimers();
    document.querySelectorAll("svg.wires").forEach(s => s.innerHTML = "");
    document.querySelectorAll(".flyer").forEach(f => f.remove());
    const prev = last; last = { S, B };
    scenes.forEach(sc => sc.el.classList.toggle("active", sc.i === S));
    const sc = scenes[S], el = sc.el, b = B, appeared = [];
    el.dataset.beat = b;
    const fwd = S > prev.S || (S === prev.S && B > prev.B);
    el.querySelectorAll("[data-in]").forEach(x => { const [i, o] = range(x, "in", "out"), on = b >= i && b < o;
      if (on && !x.classList.contains("on")) appeared.push(x); x.classList.toggle("on", on); });
    el.querySelectorAll("[data-hl]").forEach(x => { const [i, o] = range(x, "hl", "hlout"); x.classList.toggle("hl", b >= i && b < o); });
    el.querySelectorAll("[data-dim]").forEach(x => { const [i, o] = range(x, "dim", "dimout"); x.classList.toggle("dim", b >= i && b < o); });
    el.querySelectorAll("[data-st]").forEach(x => x.classList.toggle("st", b >= +x.dataset.st));
    el.querySelectorAll("[data-count]").forEach(x => {
      const at = +(x.dataset.cin ?? x.dataset.in ?? 0), to = +x.dataset.count, fmt = v => Math.round(v).toLocaleString() ;
      if (b < at) x.textContent = fmt(0); else if (b === at && fwd) countUp(x, to, +(x.dataset.cms || 1200), { tick: x.hasAttribute("data-tick") }); else x.textContent = fmt(to);
    });
    if (fwd && prev.S !== -1) {   // 進んだときだけ鳴らす（戻るときは静か）
      if (S !== prev.S) sfx("whoosh"); else sfx("tap");
      if (appeared.some(x => x.classList.contains("cross"))) later(() => sfx("buzz"), 250);
      else if (appeared.some(x => x.classList.contains("stamp"))) later(() => sfx("pop"), 200);
      else if (appeared.some(x => x.classList.contains("p"))) later(() => sfx("pop"), 120);
      if (S === prev.S && el.querySelector("[data-hl].hl")) later(() => sfx("ping", 200), 80);
    }
    const ctx = { prev, fwd, step: S === prev.S && B === prev.B + 1, later, every, sfx, rel, wire, hand, fly, countUp, table, cell, scene: el };
    if (HOOKS[el.id]) try { HOOKS[el.id](b, ctx); } catch (e) { console.error(`hook ${el.id}`, e); }
    chrome();
    history.replaceState(null, "", `#${S + 1}.${B + 1}`);
    if (playing) { clearTimeout(playT); playT = setTimeout(next, sc.dur[B] || CFG.dur); bgmSync(); }
  }
  function go(s, b) { S = Math.max(0, Math.min(scenes.length - 1, s)); B = Math.max(0, Math.min(scenes[S].beats - 1, b)); apply(); }
  function next() { if (B < scenes[S].beats - 1) go(S, B + 1); else if (S < scenes.length - 1) go(S + 1, 0); else setPlay(false, true); }
  function prev() { if (B > 0) go(S, B - 1); else if (S > 0) go(S - 1, scenes[S - 1].beats - 1); }

  // ── 自動再生と BGM（<audio id="bgm"> があれば、自動再生の時刻表に合わせて流す） ──
  let bgm = null, fadeT = null; const BGM_VOL = () => CFG.bgmVolume ?? 0.55;
  const sum = a => a.reduce((p, q) => p + (q || CFG.dur), 0);
  const tAt = (s, b) => (scenes.slice(0, s).reduce((a, x) => a + sum(x.dur), 0) + sum(scenes[s].dur.slice(0, b))) / 1000;
  function fadeTo(v, ms, then) {
    clearInterval(fadeT); const v0 = bgm.volume, n = Math.max(1, Math.round(ms / 30)); let i = 0;
    fadeT = setInterval(() => { i++; bgm.volume = Math.max(0, Math.min(1, v0 + (v - v0) * i / n)); if (i >= n) { clearInterval(fadeT); if (then) then(); } }, 30);
  }
  function bgmSync() { if (!bgm || !playing || bgm.paused) return; const t = tAt(S, B); if (Math.abs(bgm.currentTime - t) > 1.2) bgm.currentTime = t; }
  function setPlay(on, ended) {
    const was = playing; playing = on; clearTimeout(playT);
    if (bgm) { if (on && !was) { bgm.muted = !SFX.on; bgm.currentTime = tAt(S, B); bgm.volume = 0; bgm.play()?.catch(() => {}); fadeTo(BGM_VOL(), 700); }
      else if (!on && was && !ended) fadeTo(0, 500, () => bgm.pause()); }   // 最後まで来たときは曲の終わりを聞かせる
    $("ms-play").textContent = on ? "❚❚ 一時停止" : "▶ 自動再生"; $("ms-play").classList.toggle("on", on);
    if (on) { audio(); playT = setTimeout(next, scenes[S].dur[B] || CFG.dur); }
  }
  function setSfx(on, quiet) { SFX.on = on; if (bgm) bgm.muted = !on; const b = $("ms-sfx"); b.classList.toggle("on", on); b.textContent = on ? "♪ 音あり" : "♪ 消音"; if (on && !quiet) { audio(); sfx("ping"); } }

  function fit() {
    K = Math.min(innerWidth / 1920, innerHeight / 1080);
    stage.style.transform = `translate(${-960 * K}px,${-540 * K}px) scale(${K})`;
  }
  // 場面ごとの script・hook（台本 JSON から組んだとき、<script type="application/json" id="ms-scenes"> に入っている）。
  // どちらも本体だけを new Function で文法検査してから、場面ごとに1つの関数にまとめる。
  // 検査で通った本体は括弧が釣り合っているので、本体の外へ出る書き方（} で閉じて続きを書く）は文法の誤りで落ち、その場面だけが動かない
  function loadSceneCode() {
    // 共通の script（id="ms-shared"・JSON の文字列）。<script> 要素にして実行するので、const・function はこれまでどおりグローバルに置かれる
    try {
      const shared = JSON.parse($("ms-shared")?.textContent || "null");
      if (typeof shared === "string" && shared.trim()) { const el = document.createElement("script"); el.textContent = shared; document.head.appendChild(el); }
    } catch (e) { console.error("共通の script を読めない", e); }
    let code = {};
    try { code = JSON.parse($("ms-scenes")?.textContent || "{}"); } catch (e) { console.error("ms-scenes が読めない", e); return; }
    for (const [id, c] of Object.entries(code)) {
      try {
        const script = typeof c.script === "string" ? c.script : "", hook = typeof c.hook === "string" ? c.hook : null;
        if (script) new Function("MS", script);
        if (hook !== null) new Function("b", "x", hook);
        const fn = new Function("MS", script + "\n;return " + (hook !== null ? "(b, x) => {\n" + hook + "\n}" : "null") + ";")(window.MS);
        if (hook !== null) { if (typeof fn === "function") HOOKS[id] = fn; else console.error(`場面 ${id} の script が途中で return している（hook が登録できない）`); }
      } catch (e) { console.error(`場面 ${id} の script・hook を読めない: ${e.name}: ${e.message}`); }
    }
  }
  function init() {
    Object.assign(CFG, JSON.parse($("ms-config")?.textContent || "{}"));
    LOGO = JSON.parse($("ms-logo")?.textContent || "null");
    THUMBS = JSON.parse($("ms-thumbs")?.textContent || "{}");   // build が場面ごとに撮った一覧の絵
    SFX.on = CFG.sfx;
    stage = $("stage");
    scenes = [...document.querySelectorAll(".scene")].map((el, i) => ({
      el, i, beats: +el.dataset.beats || 1, ch: +(el.dataset.ch || 0), title: el.dataset.title || "", term: el.dataset.term || null,
      norail: el.hasAttribute("data-norail"), notes: [...el.querySelectorAll(":scope > aside.notes > p")].map(p => p.textContent.trim()),
      dur: (el.dataset.dur || "").split(",").filter(Boolean).map(Number), thumb: el.dataset.thumb != null ? +el.dataset.thumb : null,
    }));
    bgm = $("bgm");
    buildChrome(); addEventListener("resize", fit); fit();
    loadSceneCode();
    READY.forEach(f => f());
    addEventListener("keydown", e => {
      if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      const k = e.key.toLowerCase();
      if (["arrowright", " ", "pagedown", "enter"].includes(k)) { e.preventDefault(); next(); }
      else if (["arrowleft", "pageup", "backspace"].includes(k)) { e.preventDefault(); prev(); }
      else if (k === "escape" || k === "o") toggleOv(!$("ms-ov").classList.contains("show"));
      else if (k === "n") $("ms-notes").classList.toggle("show");
      else if (k === "p" || k === "a") setPlay(!playing);
      else if (k === "m") setSfx(!SFX.on);
      else if (k === "f") document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
      else if (k === "home") go(0, 0);
    });
    ["pointerdown", "keydown"].forEach(ev => addEventListener(ev, () => audio(), { once: true }));
    stage.addEventListener("click", e => { if (e.target.closest("button,input,select,textarea,label,a,.live-zone,#ms-ov")) return; next(); });
    const m = location.hash.match(/^#(\d+)\.(\d+)$/);
    go(m ? +m[1] - 1 : 0, m ? +m[2] - 1 : 0);
  }

  // 効果音を1つ、音のデータ（44.1kHz・モノラル）にする。動画に音を重ねるときに使う（SFX.log の t の時刻に置く）
  async function renderSfx(name, sec = 1.2) {
    const oc = new OfflineAudioContext(1, Math.ceil(44100 * sec), 44100), keep = SFX.master;
    SFX.master = oc.createGain(); SFX.master.gain.value = 0.5; SFX.master.connect(oc.destination);
    try { SOUNDS[name](oc, 0.01); } finally { SFX.master = keep; }
    return Array.from((await oc.startRendering()).getChannelData(0));
  }

  // 台本から使う口
  window.MS = {
    hook(id, fn) { HOOKS[id] = fn; },          // 場面 id の拍ごとの状態。fn(b, ctx) は何度呼ばれても同じ見た目にする
    ready(fn) { READY.push(fn); },             // 場面の部品を組む（1回だけ）。入力欄のイベントなど
    go, next, prev, sfx, rel, wire, hand, fly, countUp, table, cell, renderSfx, sounds: () => Object.keys(SOUNDS),
    get state() { return { S, B, scenes: scenes.map(x => ({ id: x.el.id, beats: x.beats, title: x.title, dur: x.dur, thumb: x.thumb })), sfx: SFX.log }; },
  };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
