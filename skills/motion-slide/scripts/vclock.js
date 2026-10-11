// 撮るときだけ差し込む仮想の時計（record.py が使う）。タイマー・rAF・performance.now を差し替え、CSS の動き（Web Animations）を止めて時刻を手で合わせる
(() => {
  let now = 0, seq = 0; const timers = new Map(), rafs = new Map(), seen = new WeakMap();
  window.setTimeout = (fn, ms = 0, ...a) => { const id = ++seq; timers.set(id, { at: now + Math.max(0, +ms || 0), fn: () => fn(...a) }); return id; };
  window.setInterval = (fn, ms = 0, ...a) => { const id = ++seq; const tick = () => { fn(...a); if (timers.has(id)) timers.set(id, { at: now + Math.max(1, +ms || 1), fn: tick }); };
    timers.set(id, { at: now + Math.max(1, +ms || 1), fn: tick }); return id; };
  window.clearTimeout = window.clearInterval = id => timers.delete(id);
  window.requestAnimationFrame = fn => { const id = ++seq; rafs.set(id, fn); return id; };
  window.cancelAnimationFrame = id => rafs.delete(id);
  performance.now = () => now; Date.now = () => 1700000000000 + now;
  // CSS の動きを全部止め、見つけた時刻からの経過で currentTime を合わせる
  function syncAnims() {
    for (const an of document.getAnimations()) {
      if (!seen.has(an)) { seen.set(an, now); an.pause(); }
      an.currentTime = now - seen.get(an);
    }
  }
  window.__vt = {
    get now() { return now; },
    advance(ms, step = 4) {                     // ms だけ時計を進める（細かく刻んでタイマーと rAF を順に起こす）
      syncAnims(); const end = now + ms;
      while (now < end) {
        const nxt = Math.min(end, now + step);
        for (;;) { let id = null, at = Infinity; for (const [k, t] of timers) if (t.at <= nxt && t.at < at) { at = t.at; id = k; }
          if (id == null) break; const t = timers.get(id); timers.delete(id); now = Math.max(now, t.at); t.fn(); syncAnims(); }
        now = nxt;
        const fs = [...rafs.values()]; rafs.clear(); fs.forEach(f => f(now));
        syncAnims();
      }
    },
  };
})();
