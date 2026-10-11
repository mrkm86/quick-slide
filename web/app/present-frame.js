// 発表の画面の「仕切りの中」の再生スクリプト（html-slide の回の HTML に、サーバが 1 本だけ足す）。
//
// 動く場所: <iframe sandbox="allow-scripts">（allow-same-origin なし）。API・セッションには触れず、外へは何も取りに行かない
// （サーバが付ける CSP は default-src 'none'）。外枠（present.js）とは postMessage だけでつながる。
//
// html-slide の出力の決まり（skills/html-slide/references/contract.md）を再生する:
//   section[data-slide]（1920×1080）を 1 枚ずつ出す・data-step の段ごとに要素を出す（0 は入ったら自動）・
//   data-fx（rise・fade・pop・wipe-*・wheel）と data-delay・data-transition（fade・none。morph は fade として扱う）・
//   data-keep（ページをまたいで残る要素。fade は新しいページを前のページの上に重ねるので、同じ位置の要素は動かず残る）・aside.notes は出さない。
// 「transform を使わない」の禁止はエンジンの HTML の中身のことで、ここ（再生側）が section を縮めるのは別の話。
//
// 合図（外枠 ⇄ 仕切りの中）: { qs: "present", v: 1, type, ... }
//   中 → 外枠  ready  { sections: [{ steps, transition }], reduced }   字体を待ち終えて、いつでも出せる
//              shown  { page, step }                                   出した（切り替えの動きはこのあとも続く）
//   外枠 → 中  show   { page, step, dir }   page は section の番号（0 から）・step は出す段（0 以上）・
//                                           dir は 1（進む）・-1（戻る・全部出した姿で）・0（飛ぶ）
(function () {
  "use strict";

  var TAG = "present";
  var VERSION = 1;
  var STAGE_W = 1920, STAGE_H = 1080;
  var TRANSITIONS = { fade: 1, none: 1 };   // 切り替えは fade と none だけ（morph は HTML で表せないので使わない。古い台本の morph は fade）
  var FX_NAMES = { rise: 1, fade: 1, pop: 1, "wipe-right": 1, "wipe-left": 1, "wipe-up": 1, "wipe-down": 1, wheel: 1 };
  // 外枠と同じサーバのオリジン（この文書のオリジンは不透明なので、URL から求める）。外枠への返信の宛先にする
  var parentOrigin = new URL(location.href).origin;
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

  var sections = [];
  var current = -1;
  var scale = 1;
  var pending = null;     // 動いている切り替え { anims, cleanup }

  function reduced() { return !!mq.matches; }

  // ---- 準備: section ごとに「段の要素・切り替え・残す要素」を集める ----
  function build(el) {
    var items = [];
    var raw = [];
    el.querySelectorAll("[data-step]").forEach(function (node) {
      // 入れ子の data-step は外側が優先（内側は無視）
      var outer = node.parentElement && node.parentElement.closest("[data-step]");
      if (outer && el.contains(outer)) { return; }
      var n = parseInt(node.getAttribute("data-step"), 10);
      if (isNaN(n) || n < 0) { return; }
      var d = parseInt(node.getAttribute("data-delay"), 10);
      raw.push({ el: node, n: n, fx: node.getAttribute("data-fx") || "rise", delay: isNaN(d) ? 0 : Math.max(0, d) });
    });
    // 段の数 = 1 以上の値の種類の数。値は小さい順に 1, 2, 3… の段に当てる
    var kinds = [];
    raw.forEach(function (r) { if (r.n >= 1 && kinds.indexOf(r.n) < 0) { kinds.push(r.n); } });
    kinds.sort(function (a, b) { return a - b; });
    raw.forEach(function (r) {
      items.push({ el: r.el, step: r.n === 0 ? 0 : kinds.indexOf(r.n) + 1, fx: FX_NAMES[r.fx] ? r.fx : "rise", delay: r.delay });
    });
    items.sort(function (a, b) { return a.delay - b.delay; });   // 同じ段の中は data-delay の小さい順
    var tr = el.getAttribute("data-transition");
    var keeps = {};
    el.querySelectorAll("[data-keep]").forEach(function (node) {
      var name = node.getAttribute("data-keep");
      if (name && !(name in keeps) && !node.closest("[data-step]")) { keeps[name] = node; }
    });
    return { el: el, items: items, steps: kinds.length, transition: TRANSITIONS[tr] ? tr : "fade", keeps: keeps, step: 0 };
  }

  function injectStyle() {
    var css = [
      "html.qs, html.qs body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #000; }",
      "html.qs section[data-slide] { position: absolute; left: 0; top: 0; transform-origin: 0 0; visibility: hidden; margin: 0; }",
      "html.qs section[data-slide].qs-on { visibility: visible; }",
      "html.qs aside.notes { display: none; }",
      ".qs-hidden { opacity: 0; }"
    ].join("\n");
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  function layout() {
    var w = window.innerWidth, h = window.innerHeight;
    scale = Math.min(w / STAGE_W, h / STAGE_H);
    var ox = (w - STAGE_W * scale) / 2, oy = (h - STAGE_H * scale) / 2;
    sections.forEach(function (s) {
      s.el.style.transform = "translate(" + ox + "px," + oy + "px) scale(" + scale + ")";
    });
  }

  // ---- 効果（data-fx） ----
  function wheelFrames() {
    // 時計回りに描く: 真上から回る扇形。頂点の数をそろえた polygon で補間する（通り過ぎた角は前の点に重ねる）
    var frames = [];
    var N = 24;
    for (var i = 0; i <= N; i++) {
      var t = i / N, a = t * 360;
      var corners = [45, 135, 225, 315];
      var pts = ["50% 50%", "50% -10%"];
      var cx = [110, 110, -10, -10], cy = [-10, 110, 110, -10];
      for (var c = 0; c < 4; c++) {
        if (a >= corners[c]) { pts.push(cx[c] + "% " + cy[c] + "%"); }
        else { pts.push(pts[pts.length - 1]); }
      }
      var rad = a * Math.PI / 180;
      var dx = Math.sin(rad), dy = -Math.cos(rad);
      var k = 0.6 / Math.max(Math.abs(dx), Math.abs(dy), 1e-6);
      pts.push((50 + dx * k * 100).toFixed(2) + "% " + (50 + dy * k * 100).toFixed(2) + "%");
      frames.push({ clipPath: "polygon(" + pts.join(",") + ")" });
    }
    return frames;
  }
  var WHEEL = null;

  function fxSpec(fx) {
    switch (fx) {
      case "fade": return { frames: [{ opacity: 0 }, { opacity: 1 }], dur: 500, easing: "ease-out" };
      case "pop": return { frames: [{ opacity: 0, transform: "scale(0.6)" }, { opacity: 1, transform: "scale(1.08)", offset: 0.7 },
                                    { opacity: 1, transform: "scale(1)" }], dur: 520, easing: "ease-out" };
      case "wipe-right": return { frames: [{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0 0 0)" }], dur: 600, easing: "cubic-bezier(0.3, 0.6, 0.2, 1)" };
      case "wipe-left": return { frames: [{ clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0)" }], dur: 600, easing: "cubic-bezier(0.3, 0.6, 0.2, 1)" };
      case "wipe-down": return { frames: [{ clipPath: "inset(0 0 100% 0)" }, { clipPath: "inset(0 0 0 0)" }], dur: 600, easing: "cubic-bezier(0.3, 0.6, 0.2, 1)" };
      case "wipe-up": return { frames: [{ clipPath: "inset(100% 0 0 0)" }, { clipPath: "inset(0 0 0 0)" }], dur: 600, easing: "cubic-bezier(0.3, 0.6, 0.2, 1)" };
      case "wheel":
        WHEEL = WHEEL || wheelFrames();
        return { frames: WHEEL, dur: 900, easing: "linear" };
      default: return { frames: [{ opacity: 0, transform: "translateY(48px)" }, { opacity: 1, transform: "none" }], dur: 650, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" };
    }
  }

  function reveal(item, extraDelay, track) {
    item.el.classList.remove("qs-hidden");
    if (reduced() || !item.el.animate) { return; }
    var spec = fxSpec(item.fx);
    var a = item.el.animate(spec.frames, { duration: spec.dur, delay: item.delay + (extraDelay || 0), easing: spec.easing, fill: "backwards" });
    if (track) { track.push(a); }
  }

  function conceal(item) {
    item.el.getAnimations().forEach(function (a) { a.cancel(); });
    item.el.classList.add("qs-hidden");
  }

  // ---- 切り替え ----
  function settle() {
    if (!pending) { return; }
    var p = pending;
    pending = null;
    p.anims.forEach(function (a) { try { a.finish(); } catch (e) { /* 終わっているもの */ } });
    p.cleanup();
  }

  function rectOf(el) { return el.getBoundingClientRect(); }

  function show(page, step, dir) {
    var next = sections[page];
    if (!next) { return; }
    step = Math.max(0, Math.min(step | 0, next.steps));
    settle();
    if (page === current) {
      setStep(next, step, dir);
      done(page, step);
      return;
    }
    var prev = current >= 0 ? sections[current] : null;
    current = page;
    var kind = reduced() || !prev || dir === 0 ? "none" : next.transition;
    if (!prev && !reduced() && dir !== 0) { kind = "fade"; }       // 最初のページは、地からゆっくり出す
    var enter0 = step === 0 && dir >= 0;                            // 入ったときに自動で出す（段 0）要素の効果を見せる
    // 並べ方: 要素ごとに「隠す・見せる」を決める
    next.items.forEach(function (it) {
      var hide = it.step > step || (it.step === 0 && enter0 && !reduced());
      it.el.getAnimations().forEach(function (a) { a.cancel(); });
      it.el.classList.toggle("qs-hidden", hide);
    });
    next.step = step;
    next.el.classList.add("qs-on");
    next.el.style.zIndex = "2";
    if (prev) { prev.el.style.zIndex = "1"; }

    var anims = [];
    var restore = [];
    var delay0 = 0;
    if (kind === "fade") {
      anims.push(next.el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 400, easing: "ease-out" }));
      delay0 = 250;
    }
    if (kind === "none") {
      if (prev) { prev.el.classList.remove("qs-on"); prev.el.style.zIndex = ""; }
      next.el.style.zIndex = "";
    } else {
      pending = {
        anims: anims,
        cleanup: function () {
          if (prev) {
            prev.el.getAnimations({ subtree: true }).forEach(function (a) { a.cancel(); });
            prev.el.classList.remove("qs-on");
            prev.el.style.zIndex = "";
          }
          next.el.style.zIndex = "";
          restore.forEach(function (f) { f(); });
        }
      };
      var all = anims.map(function (a) { return a.finished.catch(function () { /* 取り消された */ }); });
      var mine = pending;
      Promise.all(all).then(function () { if (pending === mine) { pending = null; mine.cleanup(); } });
    }
    // 段 0 の要素（入ったら自動で出る）。切り替えの動きの途中から始める
    if (enter0) {
      next.items.forEach(function (it) { if (it.step === 0) { reveal(it, delay0, anims); } });
    }
    done(page, step);
  }

  function setStep(sec, step, dir) {
    var from = sec.step;
    if (step === from) { return; }
    var forward = step > from;
    sec.items.forEach(function (it) {
      if (it.step === 0) { return; }
      if (it.step > step) { if (it.step <= from) { conceal(it); } }
      else if (it.step > from) {
        // 1 段だけ進んだとき（クリック）は効果つき。何段も飛ぶとき・戻るときは出すだけ
        if (forward && it.step === step && dir > 0) { reveal(it, 0, null); }
        else { it.el.classList.remove("qs-hidden"); }
      }
    });
    sec.step = step;
  }

  function done(page, step) {
    document.documentElement.setAttribute("data-qs-page", String(page));
    document.documentElement.setAttribute("data-qs-step", String(step));
    post({ type: "shown", page: page, step: step });
  }

  // ---- 外枠との合図 ----
  function post(msg) {
    msg.qs = TAG;
    msg.v = VERSION;
    try { window.parent.postMessage(msg, parentOrigin); } catch (e) { /* 親が無い（直接開かれた） */ }
  }

  function valid(d) {
    if (!d || typeof d !== "object" || d.qs !== TAG || d.v !== VERSION || d.type !== "show") { return false; }
    return Number.isInteger(d.page) && d.page >= 0 && Number.isInteger(d.step) && d.step >= 0 &&
      (d.dir === 1 || d.dir === -1 || d.dir === 0);
  }

  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || e.origin !== parentOrigin) { return; }
    if (!valid(e.data)) { return; }
    show(e.data.page, e.data.step, e.data.dir);
  });
  window.addEventListener("resize", layout);

  function start() {
    document.documentElement.classList.add("qs");
    injectStyle();
    sections = Array.prototype.map.call(document.querySelectorAll("section[data-slide]"), build);
    layout();
    var fonts = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    var timeout = new Promise(function (resolve) { setTimeout(resolve, 4000); });
    Promise.race([fonts, timeout]).then(function () {
      post({ type: "ready", reduced: reduced(), sections: sections.map(function (s) { return { steps: s.steps, transition: s.transition }; }) });
    });
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", start); }
  else { start(); }
})();
