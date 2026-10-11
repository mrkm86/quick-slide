// 発表の画面（見せる用）の外枠。ページ数・いまのページ・段・次へ・前へ だけを知り、中身はエンジンの部品（adapter）に任せる。
//
//   adapter = {
//     mount(slideEl, data) -> Promise   出す場所を作る（準備ができたら解決・出せなければ拒否）
//     steps(i) -> number                そのページの段の数（0 なら 1 ページ 1 段）
//     show(i, step, dir)                i ページ目（0 から）の step 段目まで出す。dir は 1 進む・-1 戻る・0 飛ぶ
//     dispose()
//   }
//   - image-slide: 見せる版のページの PNG を順に出す（段は無い）。html-slide の回の HTML が無い古い回もこれで出す
//   - html-slide:  回の HTML を仕切りの中（<iframe sandbox="allow-scripts">・同一オリジンの許可なし）で開き、
//                  postMessage で「このページの何段目まで」を送る（再生は present-frame.js）
//   - motion-slide を足すときは、同じ形の部品を ADAPTERS に 1 つ足す（外枠は変えない）
//
// 画面の操作: 進む（クリックの右側・→・Space・Enter・PageDown・左スワイプ）／戻る（左側・←・PageUp・右スワイプ）／
// Home・End／O か一覧ボタンで一覧／F で全画面／Esc で編集へ戻る（一覧が開いていれば一覧を閉じる）。
// 触らないと操作とカーソルを隠す。画面を消さない（Wake Lock。使えなければ何もしない）。
// 新しいウィンドウ・タブは開かない。画面の文言のうちサーバが持つ語（可否の理由など）はここでは作らない。
(function () {
  "use strict";

  var IDLE_MS = 3000;
  var READY_TIMEOUT_MS = 10000;
  var SWIPE_PX = 48;
  var TAP_PX = 12;
  var PREV_ZONE = 0.3;            // 画面の左 3 割のクリック・タップは「戻る」。それ以外は「進む」

  // ---- アイコン（Material Symbols Outlined・太さ 400。出典 Google・Apache License 2.0。必ずラベルと組む） ----
  var ICONS = {
    arrow_back: "m313-440 224 224-57 56-320-320 320-320 57 56-224 224h487v80H313Z",
    arrow_forward: "M647-440H160v-80h487L423-744l57-56 320 320-320 320-57-56 224-224Z",
    grid_view: "M120-520v-320h320v320H120Zm0 400v-320h320v320H120Zm400-400v-320h320v320H520Zm0 400v-320h320v320H520ZM200-600h160v-160H200v160Zm400 0h160v-160H600v160Zm0 400h160v-160H600v160Zm-400 0h160v-160H200v160Zm400-400Zm0 240Zm-240 0Zm0-240Z",
    fullscreen: "M120-120v-200h80v120h120v80H120Zm520 0v-80h120v-120h80v200H640ZM120-640v-200h200v80H200v120h-80Zm640 0v-120H640v-80h200v200h-80Z",
    fullscreen_exit: "M240-120v-120H120v-80h200v200h-80Zm400 0v-200h200v80H720v120h-80ZM120-640v-80h120v-120h80v200H120Zm520 0v-200h80v120h120v80H640Z",
    close: "m256-200-56-56 224-224-224-224 56-56 224 224 224-224 56 56-224 224 224 224-56 56-224-224-224 224Z",
    edit: "M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q12-11 26.5-17t30.5-6q16 0 31 6t26 18l55 56q12 11 17.5 26t5.5 30q0 16-5.5 30.5T817-647L290-120H120Zm640-584-56-56 56 56Zm-141 85-28-29 57 57-29-28Z"
  };
  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 -960 960 960");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICONS[name]);
    svg.appendChild(path);
    return svg;
  }
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }
  function setButton(btn, iconName, label, extra) {
    btn.textContent = "";
    btn.appendChild(icon(iconName));
    btn.appendChild(el("span", "pr-btn__label", label));
    if (extra) { btn.classList.add(extra); }
  }

  // ---- 部品 1: ページの PNG（image-slide。html-slide の回の HTML が無いときの代わりも） ----
  function imageAdapter() {
    var layers = [], pages = [], active = 0, token = 0;
    function preload(i) {
      if (pages[i]) { var im = new Image(); im.src = pages[i].image_url; }
    }
    return {
      name: "image",
      mount: function (slideEl, data) {
        pages = data.pages;
        layers = [el("img"), el("img")];
        layers.forEach(function (im) { im.decoding = "async"; im.alt = ""; slideEl.appendChild(im); });
        return Promise.resolve();
      },
      steps: function () { return 0; },
      show: function (i) {
        var page = pages[i];
        if (!page) { return; }
        var mine = ++token;
        var incoming = layers[1 - active], outgoing = layers[active];
        active = 1 - active;
        incoming.alt = "ページ " + (i + 1) + (page.title ? ": " + page.title : "");
        var swap = function () {
          if (mine !== token) { return; }
          incoming.classList.add("is-on");
          outgoing.classList.remove("is-on");
        };
        incoming.onload = swap;
        incoming.src = page.image_url;
        if (incoming.complete && incoming.naturalWidth) { swap(); }
        preload(i + 1);
        preload(i - 1);
      },
      dispose: function () { layers.forEach(function (im) { im.remove(); }); layers = []; }
    };
  }

  // ---- 部品 2: html-slide の回の HTML を仕切りの中で段ごとに再生する ----
  function frameAdapter() {
    var frame = null, pages = [], info = [], readyResolve = null, readyReject = null, timer = null, onMessage = null;
    function send(msg) {
      msg.qs = "present";
      msg.v = 1;
      // 仕切りの中のオリジンは不透明（null）で宛先に書けない。送る中身は「何ページ目の何段目か」だけなので "*" でよい。
      // 受ける側（present-frame.js）が差出人（親の window）とオリジンを確かめる
      if (frame && frame.contentWindow) { frame.contentWindow.postMessage(msg, "*"); }
    }
    function isReady(d) {
      return d && typeof d === "object" && d.qs === "present" && d.v === 1 && d.type === "ready" && Array.isArray(d.sections) &&
        d.sections.every(function (s) { return s && Number.isInteger(s.steps) && s.steps >= 0; });
    }
    return {
      name: "frame",
      mount: function (slideEl, data) {
        pages = data.pages;
        return new Promise(function (resolve, reject) {
          readyResolve = resolve;
          readyReject = reject;
          onMessage = function (e) {
            // 差出人はこの iframe だけ・オリジンは不透明（"null"）・合図の形が合うものだけ
            if (!frame || e.source !== frame.contentWindow || e.origin !== "null") { return; }
            if (isReady(e.data)) {
              info = e.data.sections;
              clearTimeout(timer);
              if (readyResolve) { readyResolve(); readyResolve = readyReject = null; }
            }
          };
          window.addEventListener("message", onMessage);
          frame = document.createElement("iframe");
          frame.setAttribute("sandbox", "allow-scripts");     // 同一オリジンの許可は付けない（API・セッションに触れさせない）
          frame.setAttribute("title", "スライド");
          frame.setAttribute("tabindex", "-1");
          frame.src = data.html.url;
          slideEl.appendChild(frame);
          timer = setTimeout(function () {
            if (readyReject) { readyReject(new Error("timeout")); readyResolve = readyReject = null; }
          }, READY_TIMEOUT_MS);
        });
      },
      steps: function (i) {
        var page = pages[i];
        if (!page) { return 0; }
        var s = info[page.section];
        return s ? s.steps : page.steps;
      },
      show: function (i, step, dir) {
        var page = pages[i];
        if (page) { send({ type: "show", page: page.section, step: step, dir: dir }); }
      },
      dispose: function () {
        clearTimeout(timer);
        if (onMessage) { window.removeEventListener("message", onMessage); }
        if (frame) { frame.remove(); frame = null; }
      }
    };
  }

  // エンジンごとの部品。外枠は名前でしか選ばない。html-slide は回の HTML があれば仕切りの中で、無ければ PNG で出す
  var ADAPTERS = {
    "image-slide": function () { return imageAdapter(); },
    "html-slide": function (data) { return data.html ? frameAdapter() : imageAdapter(); }
  };

  // ---- 外枠 ----
  var root = $("present");
  var stage = $("stage"), slideEl = $("slide"), hit = $("hit"), bar = $("bar"), notice = $("notice");
  var prevBtn = $("prev"), nextBtn = $("next"), countEl = $("count"), listBtn = $("list-btn"), fsBtn = $("fs-btn"), exitLink = $("exit");
  var fill = $("progress-fill"), live = $("live"), listEl = $("list"), listItems = $("list-items"), listClose = $("list-close");

  var data = null, adapter = null, pages = [], cur = 0, step = 0, mounted = false, rendered = false, idleTimer = null, listOpener = null;

  setButton(prevBtn, "arrow_back", "前へ", "pr-btn--wide");
  setButton(nextBtn, "arrow_forward", "次へ", "pr-btn--primary");
  nextBtn.classList.add("pr-btn--wide");
  setButton(listBtn, "grid_view", "一覧");
  setButton(fsBtn, "fullscreen", "全画面");
  setButton(exitLink, "edit", "編集へ戻る");
  setButton(listClose, "close", "閉じる");

  function deckIdFromPath() {
    var m = /^\/slides\/([0-9a-f-]{36})\/present\/?$/.exec(location.pathname);
    return m ? m[1] : null;
  }

  function showMessage(text, link) {
    root.setAttribute("data-state", link ? "empty" : "error");
    slideEl.textContent = "";
    var box = el("div", "pr-message");
    box.appendChild(el("p", "", text));
    if (link) {
      var a = el("a", "pr-btn");
      a.href = link;
      setButton(a, "edit", "編集へ戻る");
      box.appendChild(a);
    }
    slideEl.appendChild(box);
    hit.hidden = true;
    prevBtn.hidden = nextBtn.hidden = listBtn.hidden = fsBtn.hidden = true;
    countEl.textContent = "";
  }

  function say(text) { live.textContent = ""; live.textContent = text; }

  function flash(text) {
    notice.textContent = text;
    notice.hidden = false;
    clearTimeout(flash.t);
    flash.t = setTimeout(function () { notice.hidden = true; }, 6000);
  }

  // ---- 進む・戻る ----
  function stepsHere() { return adapter ? adapter.steps(cur) : 0; }

  function render(dir, announce) {
    adapter.show(cur, step, dir);
    var total = pages.length, n = stepsHere();
    countEl.textContent = "";
    countEl.appendChild(document.createTextNode((cur + 1) + " / " + total));
    if (n > 0) { countEl.appendChild(el("small", "", "段 " + step + " / " + n)); }
    fill.style.width = (total > 1 ? (cur / (total - 1)) * 100 : 100) + "%";
    var atEnd = cur === total - 1 && step >= n;
    var atStart = cur === 0 && step === 0;
    nextBtn.hidden = atEnd;           // 前後が無いときは無効にせず出さない（DADS のページナビゲーション）
    prevBtn.hidden = atStart;
    var title = pages[cur].title;
    if (announce !== false) {
      say("ページ " + (cur + 1) + " / " + total + (title ? ": " + title : "") + (n > 0 ? "（段 " + step + " / " + n + "）" : ""));
    }
    document.title = (title ? title + " - " : "") + (data ? data.deck.title : "発表") + "（" + (cur + 1) + " / " + total + "）";
    var url = new URL(location.href);
    url.searchParams.delete("s");
    url.searchParams.set("p", String(cur + 1));
    try { history.replaceState(null, "", url.pathname + url.search); } catch (e) { /* 履歴を書けない環境 */ }
    markList();
  }

  function go(i, s, dir) {
    if (!mounted) { return; }
    i = Math.max(0, Math.min(i, pages.length - 1));
    s = Math.max(0, Math.min(s, adapter.steps(i)));
    if (rendered && i === cur && s === step) { return; }
    var d = dir === undefined ? (i === cur ? (s > step ? 1 : -1) : (i > cur ? 1 : -1)) : dir;
    cur = i;
    step = s;
    rendered = true;
    render(d);
  }
  function next() {
    if (step < stepsHere()) { go(cur, step + 1, 1); }
    else if (cur < pages.length - 1) { go(cur + 1, 0, 1); }
  }
  function prev() {
    if (step > 0) { go(cur, step - 1, -1); }
    else if (cur > 0) { go(cur - 1, adapter.steps(cur - 1), -1); }   // 戻ると、前のページは全部出した姿
  }
  function jump(i) { go(i, 0, 0); }

  // ---- 一覧 ----
  function buildList() {
    listItems.textContent = "";
    pages.forEach(function (p, i) {
      var li = el("li");
      var b = el("button", "pr-list__item");
      b.type = "button";
      b.setAttribute("data-index", String(i));
      b.setAttribute("aria-label", "ページ " + (i + 1) + (p.title ? ": " + p.title : "") + " へ");
      var img = el("img", "pr-list__thumb");
      img.src = p.image_url;
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      b.appendChild(img);
      var label = el("span", "pr-list__label");
      label.appendChild(el("span", "pr-list__no", String(i + 1)));
      label.appendChild(el("span", "pr-list__name", p.title || ""));
      b.appendChild(label);
      b.addEventListener("click", function () { closeList(false); jump(i); stage.focus({ preventScroll: true }); });
      li.appendChild(b);
      listItems.appendChild(li);
    });
  }
  function markList() {
    Array.prototype.forEach.call(listItems.children, function (li, i) {
      var b = li.firstChild;
      if (i === cur) { b.setAttribute("aria-current", "true"); } else { b.removeAttribute("aria-current"); }
    });
  }
  function listIsOpen() { return !listEl.hidden; }
  function openList() {
    if (!pages.length) { return; }
    listOpener = document.activeElement;
    listEl.hidden = false;
    markList();
    var target = listItems.children[cur] && listItems.children[cur].firstChild;
    if (target) { target.focus(); target.scrollIntoView({ block: "center" }); }
    wake();
  }
  function closeList(restoreFocus) {
    listEl.hidden = true;
    if (restoreFocus !== false) {
      var back = listOpener && document.contains(listOpener) ? listOpener : stage;
      if (back.focus) { back.focus({ preventScroll: true }); }
    }
    listOpener = null;
  }
  function trapTab(e) {
    var focusables = listEl.querySelectorAll("button");
    if (!focusables.length) { return; }
    var first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  function toggleList() { if (listIsOpen()) { closeList(); } else { openList(); } }

  // ---- 全画面 ----
  var fsRoot = document.documentElement;
  function fsActive() { return !!(document.fullscreenElement || document.webkitFullscreenElement); }
  function fsSupported() { return !!(fsRoot.requestFullscreen || fsRoot.webkitRequestFullscreen); }
  function toggleFullscreen() {
    if (!fsSupported()) { return; }
    var p;
    if (fsActive()) { p = (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
    else { p = (fsRoot.requestFullscreen || fsRoot.webkitRequestFullscreen).call(fsRoot); }
    if (p && p.catch) { p.catch(function () { /* 断られた（ユーザー操作でない等）。何もしない */ }); }
  }
  function paintFullscreen() {
    var on = fsActive();
    setButton(fsBtn, on ? "fullscreen_exit" : "fullscreen", on ? "全画面を終える" : "全画面");
    fsBtn.setAttribute("aria-label", (on ? "全画面を終える" : "全画面") + "（F）");
    fsBtn.setAttribute("title", (on ? "全画面を終える" : "全画面") + "（F）");
    fsBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  fsBtn.hidden = !fsSupported();   // iPhone の Safari などは全画面にできない。そのときはボタンを出さない
  document.addEventListener("fullscreenchange", paintFullscreen);
  document.addEventListener("webkitfullscreenchange", paintFullscreen);

  // ---- 触らないと隠す ----
  function wake() {
    root.classList.remove("is-idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(function () {
      if (listIsOpen() || root.getAttribute("data-state") !== "ready") { return; }
      root.classList.add("is-idle");
    }, IDLE_MS);
  }
  ["pointermove", "pointerdown", "keydown", "focusin", "touchstart"].forEach(function (t) {
    document.addEventListener(t, wake, { passive: true });
  });

  // ---- キー ----
  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var key = e.key;
    if (listIsOpen()) {
      if (key === "Escape") { e.preventDefault(); closeList(); }
      else if (key === "Tab") { trapTab(e); }
      else if (key === "o" || key === "O") { e.preventDefault(); closeList(); }
      return;
    }
    if (!mounted) {
      if (key === "Escape" && data) { location.assign(data.edit_url); }
      return;
    }
    // ボタン・リンクにフォーカスがあるときの Enter / Space は、そのボタンの操作（ページ送りに奪わない）
    var onControl = e.target && e.target.closest && e.target.closest("button, a");
    switch (key) {
      case "ArrowRight": case "PageDown": e.preventDefault(); next(); break;
      case " ": case "Enter": if (onControl) { return; } e.preventDefault(); next(); break;
      case "ArrowLeft": case "PageUp": e.preventDefault(); prev(); break;
      case "Home": e.preventDefault(); jump(0); break;
      case "End": e.preventDefault(); jump(pages.length - 1); break;
      case "o": case "O": e.preventDefault(); openList(); break;
      case "f": case "F": e.preventDefault(); toggleFullscreen(); break;
      case "Escape": e.preventDefault(); location.assign(data.edit_url); break;
      default: break;
    }
  });

  // ---- ポインタ: クリックの左右・スワイプ（マウスもタッチも同じ道） ----
  var down = null;
  hit.addEventListener("pointerdown", function (e) {
    if (e.button !== undefined && e.button !== 0) { return; }
    down = { x: e.clientX, y: e.clientY, id: e.pointerId };
    try { hit.setPointerCapture(e.pointerId); } catch (err) { /* 捕まえられなくても続ける */ }
  });
  hit.addEventListener("pointerup", function (e) {
    if (!down || down.id !== e.pointerId) { return; }
    var dx = e.clientX - down.x, dy = e.clientY - down.y;
    down = null;
    if (Math.abs(dx) >= SWIPE_PX && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) { next(); } else { prev(); }    // 左へなぞる = 進む（めくる向き）
      return;
    }
    if (Math.abs(dx) <= TAP_PX && Math.abs(dy) <= TAP_PX) {
      var box = hit.getBoundingClientRect();
      if ((e.clientX - box.left) / box.width < PREV_ZONE) { prev(); } else { next(); }
    }
  });
  hit.addEventListener("pointercancel", function () { down = null; });
  hit.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  prevBtn.addEventListener("click", prev);
  nextBtn.addEventListener("click", next);
  listBtn.addEventListener("click", toggleList);
  fsBtn.addEventListener("click", toggleFullscreen);
  listClose.addEventListener("click", function () { closeList(); });
  listEl.addEventListener("click", function (e) { if (e.target === listEl) { closeList(); } });

  // ---- 画面を消さない（Wake Lock。使えなければ何もしない） ----
  var lock = null;
  function keepAwake() {
    if (!navigator.wakeLock || document.visibilityState !== "visible") { return; }
    navigator.wakeLock.request("screen").then(function (l) {
      lock = l;
      l.addEventListener("release", function () { if (lock === l) { lock = null; } });
    }).catch(function () { /* 断られた（省電力など）。何もしない */ });
  }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible" && !lock) { keepAwake(); } });

  // ---- 起動 ----
  function startPage() {
    var q = new URLSearchParams(location.search);
    var sid = q.get("s");                       // ページの id（編集の画面の「ここから発表」）。見つからなければ ?p、それも無ければ 1 ページ目
    if (sid) {
      for (var k = 0; k < pages.length; k++) { if (pages[k].slide_id === sid) { return k; } }
    }
    var p = parseInt(q.get("p"), 10);
    return isNaN(p) ? 0 : Math.max(0, Math.min(p - 1, pages.length - 1));
  }

  function boot(d) {
    data = d;
    exitLink.href = d.edit_url;
    pages = d.pages;
    if (!pages.length) { showMessage(d.message || "見せられるページがまだありません。", d.edit_url); return; }
    var make = ADAPTERS[d.engine] || ADAPTERS["image-slide"];
    adapter = make(d);
    buildList();
    root.setAttribute("data-state", "loading");
    slideEl.textContent = "";
    var begin = function () {
      mounted = true;
      root.setAttribute("data-state", "ready");
      cur = startPage();
      step = 0;
      rendered = true;
      render(cur === 0 ? 1 : 0);   // 最初のページは地から出す。途中のページから始めるときは飛ぶ
      keepAwake();
      wake();
      stage.focus({ preventScroll: true });
    };
    adapter.mount(slideEl, d).then(begin, function () {
      // 仕切りの中が立ち上がらない: ページの PNG に切り替える（段ごとの再生は諦める）
      adapter.dispose();
      adapter = imageAdapter();
      slideEl.textContent = "";
      flash("動きの再生を始められなかったので、ページの画像で見せます。");
      adapter.mount(slideEl, d).then(begin);
    });
  }

  var deckId = deckIdFromPath();
  if (!deckId) { showMessage("この画面を開けませんでした。スライドの画面から「発表」を押してください。", "/slides"); }
  else {
    fetch("/api/decks/" + deckId + "/present", { headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) { throw new Error(j && j.error && j.error.message ? j.error.message : "読み込めませんでした。"); }
        return j;
      });
    }).then(boot).catch(function (e) {
      showMessage((e && e.message) || "読み込めませんでした。", "/slides");
    });
  }
})();
