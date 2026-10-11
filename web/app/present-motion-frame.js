// 発表の画面の「仕切りの中」の橋渡し（motion-slide の HTML 1枚に、サーバが 1 本だけ足す）。
//
// 動く場所: <iframe sandbox="allow-scripts" allow="autoplay">（allow-same-origin なし）。API・セッションには触れず、外へは何も取りに行かない
// （サーバが付ける CSP は default-src 'none'・script-src は 'unsafe-inline' 'unsafe-eval' とこの 1 本だけ）。
// 外枠（present.js）とは postMessage だけでつながる。
//
// motion-slide の HTML は自前のエンジン（window.MS）を持つ。この橋渡しはエンジンを書き換えず、次のことだけをする:
//   - 外枠のページ＝場面・段＝拍としてつなぐ（外枠の進む・戻る・一覧・「ここから発表」→ MS.go(場面, 拍)）
//   - 中の状態の変化（中でのクリック・キー・自動再生）を外枠へ返す（#ms-pos の書き換えを見る。エンジンの内側は触らない）
//   - 一覧・全画面・Esc・End は外枠のものを使う（中のエンジンの一覧は出さない。中でこれらのキーを押したら外枠へ渡す）
//   - 中でもタップとスワイプを html-slide と同じにする（左 3 割のタップは戻る・左へなぞると進む・右へなぞると戻る）。
//     ボタン・入力欄・LIVE の部品（.live-zone）の上は触らない（エンジンが受ける）
//   - 触らない間はマウスの矢印を隠す・動きを減らす設定では動きを省く
//
// キーが二重に進まないこと: キーボードのフォーカスが中にあるときは中のエンジンが受け（キーは外枠の文書へ届かない）、
// 外枠にあるときは外枠が受けて中へ MS.go で送る。どちらか一方だけが進め、進んだ結果は外枠が「中の状態」として合わせる
// （同じ位置の show は中で何もしない）。
//
// 効果音・BGM は、利用者の操作の後だけ鳴る（エンジンの作りのまま。最初のクリックかキーで音を使えるようにする）。
// 外枠での最初の操作は gesture の合図で中へ伝え、同じ「最初の操作」として扱う（iframe に allow="autoplay"）。
//
// 合図（外枠 ⇄ 仕切りの中）: { qs: "present-motion", v: 1, type, ... }
//   中 → 外枠  ready  { scenes: [{ steps, title }], page, step }   エンジンが立ち上がった（steps は拍の数 - 1）
//              state  { page, step }                                 中で場面・拍が変わった
//              key    { key }                                        中で押された、外枠が持つキー（o・Escape・f・End）
//              activity {}                                           中で操作があった（外枠の操作の表示を出す）
//   外枠 → 中  show   { page, step }   その場面の拍へ（同じ位置なら何もしない）
//              idle   { on }           触らない間（マウスの矢印を隠す）
//              gesture {}              外枠での最初の操作（音を使えるようにする）
(function () {
  "use strict";

  var TAG = "present-motion";
  var VERSION = 1;
  var SWIPE_PX = 48;
  var TAP_PX = 12;
  var PREV_ZONE = 0.3;
  var ACTIVITY_MS = 400;
  var FORWARD = { "o": 1, "O": 1, "Escape": 1, "f": 1, "F": 1, "End": 1 };
  var INTERACTIVE = "button, input, select, textarea, label, a, .live-zone, .live-zone *, [contenteditable=\"\"], [contenteditable=true], [contenteditable=true] *, [role=button]";
  // 外枠と同じサーバのオリジン（この文書のオリジンは不透明なので、URL から求める）
  var parentOrigin = new URL(location.href).origin;
  var started = false;
  var lastSent = null;
  var lastActivity = 0;
  var suppressClick = false;

  function MS() { return window.MS; }
  function send(msg) {
    msg.qs = TAG;
    msg.v = VERSION;
    // 外枠のオリジンはこの文書の URL のオリジン。送るのは場面と拍の番号・キーの名前だけ
    window.parent.postMessage(msg, parentOrigin);
  }

  function injectStyle() {
    var css = [
      "#ms-ov { display: none !important; }",                              // 中の一覧は出さない（外枠の一覧を使う）
      "html { touch-action: none; }",
      "button, input, select, textarea, label, a, .live-zone, .live-zone * { touch-action: auto; }",
      "html.qs-idle, html.qs-idle * { cursor: none !important; }",
      "@media (prefers-reduced-motion: reduce) {",
      "  *, *::before, *::after { animation-duration: 0.01ms !important; animation-iteration-count: 1 !important;",
      "    transition-duration: 0.01ms !important; transition-delay: 0s !important; scroll-behavior: auto !important; }",
      "}"
    ].join("\n");
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  function scenes() {
    var st = MS().state;
    return st.scenes.map(function (s) { return { steps: Math.max(0, s.beats - 1), title: s.title || "" }; });
  }

  // 中で場面・拍が変わったら外枠へ（同じ位置は 2 度送らない）
  function report() {
    var st = MS().state;
    var key = st.S + "." + st.B;
    if (key === lastSent) { return; }
    lastSent = key;
    send({ type: "state", page: st.S, step: st.B });
  }

  function watch() {
    // エンジンは拍を当てるたびに #ms-pos の文字を書き直す（chrome()）。それを見て状態の変化を知る
    var pos = document.getElementById("ms-pos");
    if (!pos) { return; }
    new MutationObserver(report).observe(pos, { childList: true, characterData: true, subtree: true });
  }

  function activity() {
    var now = Date.now();
    if (now - lastActivity < ACTIVITY_MS) { return; }
    lastActivity = now;
    send({ type: "activity" });
  }

  // ---- キー: 外枠が持つキーは外枠へ渡し、中のエンジンには渡さない（二重に動かない） ----
  // 編集できる要素（input・textarea・select・contenteditable）では、どのキーもエンジンへ流さない（入力・カーソル移動のため）。
  // ボタン・リンクなどの操作の部品では、Enter・Space はエンジンへ流さず、既定の動き（押す）を残す。矢印などは今までどおり
  var CONTROL = "button, a[href], label, summary, [role=button], [role=link], [role=menuitem], [role=checkbox], [role=switch], [role=tab], [role=option]";
  function isEditable(t) {
    return !!t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || "") || t.isContentEditable === true);
  }
  function isControl(t) { return !!t && !!t.closest && !!t.closest(CONTROL); }
  window.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var t = e.target;
    activity();
    if (isEditable(t)) { e.stopImmediatePropagation(); return; }                       // 既定の動きも残す（preventDefault しない）
    if (isControl(t) && (e.key === "Enter" || e.key === " ")) { e.stopImmediatePropagation(); return; }
    if (FORWARD[e.key]) {
      e.preventDefault();
      e.stopImmediatePropagation();
      send({ type: "key", key: e.key });
    }
  }, true);

  // ---- ポインタ: タップの左右・スワイプ（外枠の面と同じ決まり） ----
  var down = null;
  window.addEventListener("pointerdown", function (e) {
    if (!e.isTrusted) { return; }    // gesture の合図で自分が投げた擬似の pointerdown は数えない
    suppressClick = false;           // 前のなぞり（タッチは click が来ない）の印を、次の操作に持ち越さない
    activity();
    if (e.button !== undefined && e.button !== 0) { down = null; return; }
    if (e.target && e.target.closest && e.target.closest(INTERACTIVE)) { down = null; return; }
    down = { x: e.clientX, y: e.clientY, id: e.pointerId };
  }, true);
  window.addEventListener("pointermove", activity, true);
  window.addEventListener("pointerup", function (e) {
    if (!down || down.id !== e.pointerId) { return; }
    var dx = e.clientX - down.x, dy = e.clientY - down.y;
    down = null;
    if (Math.abs(dx) >= SWIPE_PX && Math.abs(dx) > Math.abs(dy) * 1.5) {
      suppressClick = true;
      if (dx < 0) { MS().next(); } else { MS().prev(); }    // 左へなぞる = 進む（めくる向き）
      return;
    }
    if (Math.abs(dx) <= TAP_PX && Math.abs(dy) <= TAP_PX) {
      if (e.clientX / window.innerWidth < PREV_ZONE) { suppressClick = true; MS().prev(); }
      // 右側のタップは、エンジンの click（舞台のクリック = 次へ）がそのまま進める
    } else {
      suppressClick = true;    // 動かしてから離した（なぞり足りない）。そのあとの click で進めない
    }
  }, true);
  window.addEventListener("pointercancel", function () { down = null; }, true);
  // クリックは window の capture では止めない（止めると、場面の中の部品のクリックのハンドラーまで届かなくなる）。
  // エンジンの「舞台のクリック = 次へ」は #stage の bubble のリスナーなので、それより前に同じ #stage に bubble のリスナーを
  // 足し、ここで stopImmediatePropagation する（部品のハンドラーは先に動き終えている。エンジンのリスナーだけが動かない）。
  // 止めるのは、(a) なぞり・左タップで自分が動かしたあとの click、(b) 編集できる要素・[role=button] の click。
  // .live-zone の中はエンジンが元から次へに使わないので、ここでは何もしない。
  function guardStageClick() {
    var stage = document.getElementById("stage");
    if (!stage) { return; }
    stage.addEventListener("click", function (e) {
      var t = e.target;
      var own = t && t.closest && t.closest(".live-zone");
      if (suppressClick) {
        suppressClick = false;
        if (!own) { e.stopImmediatePropagation(); }
        return;
      }
      if (!own && t && t.closest && t.closest("[contenteditable=\"\"], [contenteditable=true], [role=button]")) {
        e.stopImmediatePropagation();
      }
    });
  }

  // ---- 外枠から ----
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (e.source !== window.parent || e.origin !== parentOrigin) { return; }
    if (!d || typeof d !== "object" || d.qs !== TAG || d.v !== VERSION) { return; }
    if (d.type === "show") {
      var st = MS().state;
      if (Number.isInteger(d.page) && Number.isInteger(d.step) && (st.S !== d.page || st.B !== d.step)) {
        MS().go(d.page, d.step);
        // 外枠が送った位置は外枠が知っている。中の状態の変化として外枠へ送り返さない（続けて送った次の位置を巻き戻さない）
        var now = MS().state;
        lastSent = now.S + "." + now.B;
      }
    } else if (d.type === "idle") {
      document.documentElement.classList.toggle("qs-idle", !!d.on);
    } else if (d.type === "gesture") {
      // エンジンは最初の pointerdown / keydown で音を使えるようにする。外枠での最初の操作を、その代わりに伝える
      window.dispatchEvent(new Event("pointerdown"));
    }
  });

  function start() {
    if (started) { return; }
    started = true;
    injectStyle();
    guardStageClick();   // エンジンの舞台のクリックのリスナーより前（MS.ready はエンジンがそれを付ける前に呼ぶ）
    watch();
    var st = MS().state;
    lastSent = st.S + "." + st.B;
    send({ type: "ready", scenes: scenes(), page: st.S, step: st.B });
  }

  // エンジンは DOMContentLoaded で立ち上がり、場面を集めてから MS.ready の関数を呼ぶ（そのあと最初の拍を当てる）
  function boot() {
    if (!MS()) { return; }
    if (MS().state.scenes.length) { start(); } else { MS().ready(start); }
  }
  boot();
})();
