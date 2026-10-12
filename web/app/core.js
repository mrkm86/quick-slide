// Quick Slide 画面の共通部品（P6）: API 呼び出し・DOM 補助・通知・メニュー・画面遷移。
// 方針: 表示する状態は必ずサーバから読んだものを使う（U1）。状態の日本語名はここでは作らない（U9）。
// 新しいウィンドウ・タブを開く API は使わない（DESIGN §1.2）。
(function () {
  "use strict";

  var SS = (window.SS = window.SS || {});
  SS.views = {};
  SS.vocab = null;          // GET /api/vocabulary の結果（起動時に 1 度読む）
  SS.launchId = null;       // URL の ?launch=（クリップボード操作の記録に使う。ADR 0014）
  SS.reducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  // ---- API ----
  var sessionPromise = null;
  function session() {
    if (!sessionPromise) {
      sessionPromise = fetch("/api/session").then(function (r) {
        if (!r.ok) { throw new SS.ApiError("セッションを取得できませんでした。画面を開き直してください。", "session_unavailable", r.status); }
        return r.json();
      }).then(function (j) { return j.session_token; }).catch(function (e) { sessionPromise = null; throw e; });
    }
    return sessionPromise;
  }

  SS.ApiError = function (message, code, status, details) {
    this.name = "ApiError";
    this.message = message;
    this.code = code || "unknown";
    this.status = status || 0;
    this.details = details || null;
  };
  SS.ApiError.prototype = Object.create(Error.prototype);

  function parse(r) {
    return r.text().then(function (text) {
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
      if (!r.ok) {
        var err = data && data.error ? data.error : null;
        // サーバは日本語の本文と機械可読 code を返す（server/errors.py）。無ければ一般文（英語の例外は出さない）
        throw new SS.ApiError(err && err.message ? err.message : "要求を処理できませんでした（" + r.status + "）。",
          err && err.code ? err.code : "http_" + r.status, r.status, err && err.details);
      }
      return data;
    });
  }

  // GET は Host 検査のみ。状態を変える要求はセッションヘッダ付き（ADR 0019 §1）
  SS.api = function (path, options) {
    options = options || {};
    var method = options.method || "GET";
    var init = { method: method, headers: {} };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    var start = method === "GET" ? Promise.resolve(null) : session();
    return start.then(function (token) {
      if (token) { init.headers["X-Quick-Slide-Session"] = token; }
      if (method !== "GET" && init.body === undefined) { init.headers["Content-Type"] = "application/json"; init.body = "{}"; }
      return fetch(path, init);
    }).then(parse, function (e) {
      if (e instanceof SS.ApiError) { throw e; }
      throw new SS.ApiError("サーバに接続できません。Quick Slide が起動しているか確認してください。", "network", 0);
    });
  };

  SS.uploadImage = function (file) {
    return session().then(function (token) {
      return fetch("/api/settings/images", { method: "POST", headers: {
        "Content-Type": "application/octet-stream", "X-Quick-Slide-Session": token
      }, body: file });
    }).then(parse, function () {
      throw new SS.ApiError("画像を送信できませんでした。接続を確認して選び直してください。", "upload_network", 0);
    });
  };

  // ---- DOM ----
  SS.h = function (tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) { return; }
        if (k === "class") { el.className = v; }
        else if (k === "text") { el.textContent = v; }
        else if (k === "html") { el.innerHTML = v; }
        else if (k.indexOf("on") === 0 && typeof v === "function") { el.addEventListener(k.slice(2), v); }
        else if (k === "dataset") { Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; }); }
        else if (v === true) { el.setAttribute(k, ""); }
        else { el.setAttribute(k, v); }
      });
    }
    for (var i = 2; i < arguments.length; i++) { SS.append(el, arguments[i]); }
    return el;
  };
  SS.append = function (el, child) {
    if (child === null || child === undefined || child === false) { return; }
    if (Array.isArray(child)) { child.forEach(function (c) { SS.append(el, c); }); return; }
    el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  };
  // アイコン（Material Symbols Outlined・太さ 400。出典 Google・Apache License 2.0。DADS の Figma が使うセット）。
  // 必ずラベルと組む: アイコンだけのボタンには呼ぶ側が aria-label を付ける。アイコン自身は読み上げから外す。
  var ICON_PATHS = {
    more_vert: "M480-160q-33 0-56.5-23.5T400-240q0-33 23.5-56.5T480-320q33 0 56.5 23.5T560-240q0 33-23.5 56.5T480-160Zm0-240q-33 0-56.5-23.5T400-480q0-33 23.5-56.5T480-560q33 0 56.5 23.5T560-480q0 33-23.5 56.5T480-400Zm0-240q-33 0-56.5-23.5T400-720q0-33 23.5-56.5T480-800q33 0 56.5 23.5T560-720q0 33-23.5 56.5T480-640Z",
    // 形式のアイコン（自作の単純な形。Material Symbols の同名の絵に近づけたもの。外周と穴は巻く向きを逆にして抜く）
    slideshow: "M120-760h720v560H120Zm80 80v400h560v-400Zm200 320v-240l200 120Z",
    photo_library: "M120-760h720v560H120Zm80 80v400h560v-400Zm40 360 140-180 100 120 80-100 120 160ZM640-660a40 40 0 1 0-80 0 40 40 0 1 0 80 0Z",
    animation: "M440-480a140 140 0 1 1-280 0 140 140 0 1 1 280 0ZM710-480a90 90 0 1 1-180 0 90 90 0 1 1 180 0ZM860-480a40 40 0 1 1-80 0 40 40 0 1 1 80 0Z",
    add: "M440-440H200v-80h240v-240h80v240h240v80H520v240h-80v-240Z",
    help: "M478-240q21 0 35.5-14.5T528-290q0-21-14.5-35.5T478-340q-21 0-35.5 14.5T428-290q0 21 14.5 35.5T478-240Zm-36-154h74q0-33 7.5-52t42.5-52q26-26 41-49.5t15-56.5q0-56-41-86t-97-30q-57 0-92.5 30T342-618l66 26q5-18 22.5-39t53.5-21q32 0 48 17.5t16 38.5q0 20-12 37.5T506-526q-44 39-54 59t-10 73Zm38 314q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q134 0 227-93t93-227q0-134-93-227t-227-93q-134 0-227 93t-93 227q0 134 93 227t227 93Z",
    close: "m256-200-56-56 224-224-224-224 56-56 224 224 224-224 56 56-224 224 224 224-56 56-224-224-224 224Z",
    arrow_back: "m313-440 224 224-57 56-320-320 320-320 57 56-224 224h487v80H313Z",
    arrow_forward: "M647-440H160v-80h487L423-744l57-56 320 320-320 320-57-56 224-224Z",
    expand_more: "M480-344 240-584l56-56 184 184 184-184 56 56-240 240Z",
    expand_less: "m296-345-56-56 240-240 240 240-56 56-184-184-184 184Z",
    play_arrow: "M320-200v-560l440 280-440 280Zm80-280Zm0 134 210-134-210-134v268Z",
    replay: "M480-80q-75 0-140.500-28.500t-114-77q-48.500-48.500-77-114T120-440h80q0 117 81.500 198.500T480-160q117 0 198.500-81.500T760-440q0-117-81.500-198.500T480-720h-6l62 62-56 58-160-160 160-160 56 58-62 62h6q75 0 140.500 28.500t114 77q48.500 48.500 77 114T840-440q0 75-28.500 140.500t-77 114q-48.500 48.500-114 77T480-80Z",
    edit: "M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q12-11 26.5-17t30.5-6q16 0 31 6t26 18l55 56q12 11 17.5 26t5.5 30q0 16-5.5 30.5T817-647L290-120H120Zm640-584-56-56 56 56Zm-141 85-28-29 57 57-29-28Z",
    image: "M200-120q-33 0-56.500-23.500T120-200v-560q0-33 23.500-56.500T200-840h560q33 0 56.500 23.500T840-760v560q0 33-23.500 56.500T760-120H200Zm0-80h560v-560H200v560Zm40-80h480L570-480 450-320l-90-120-120 160Zm-40 80v-560 560Z",
    visibility_off: "m644-428-58-58q9-47-27-88t-93-32l-58-58q17-8 34.500-12t37.500-4q75 0 127.500 52.500T660-500q0 20-4 37.500T644-428Zm128 126-58-56q38-29 67.500-63.500T832-500q-50-101-143.500-160.500T480-720q-29 0-57 4t-55 12l-62-62q41-17 84-25.500t90-8.500q151 0 269 83.500T920-500q-23 59-60.500 109.500T772-302Zm20 246L624-222q-35 11-70.500 16.500T480-200q-151 0-269-83.500T40-500q21-53 53-98.500t73-81.500L56-792l56-56 736 736-56 56ZM222-624q-29 26-53 57t-41 67q50 101 143.500 160.500T480-280q20 0 39-2.500t39-5.500l-36-38q-11 3-21 4.500t-21 1.500q-75 0-127.500-52.500T300-500q0-11 1.500-21t4.500-21l-84-82Zm319 93Zm-151 75Z",
    download: "M480-320 280-520l56-58 104 104v-326h80v326l104-104 56 58-200 200ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z",
    // 取り除く（設定のロゴの画像。skills/dads/icons の delete と同じ絵）
    delete: "M261-120q-24.75 0-42.37-17.63Q201-155.25 201-180v-570h-41v-60h188v-30h264v30h188v60h-41v570q0 24-18 42t-42 18H261Zm438-630H261v570h438v-570ZM367-266h60v-399h-60v399Zm166 0h60v-399h-60v399ZM261-750v570-570Z",
    // プレビュー（設定のブランドの見本を開く。skills/dads/icons の eye と同じ絵）
    eye: "M600.5-379.5Q650-429 650-500t-49.5-120.5Q551-670 480-670t-120.5 49.5Q310-571 310-500t49.5 120.5Q409-330 480-330t120.5-49.5Zm-200-41Q368-453 368-500t32.5-79.5Q433-612 480-612t79.5 32.5Q592-547 592-500t-32.5 79.5Q527-388 480-388t-79.5-32.5ZM216-283Q98-366 40-500q58-134 176-217t264-83q146 0 264 83t176 217q-58 134-176 217t-264 83q-146 0-264-83Zm264-217Zm222.5 174.5Q804-391 857-500q-53-109-154.5-174.5T480-740q-121 0-222.5 65.5T102-500q54 109 155.5 174.5T480-260q121 0 222.5-65.5Z",
    // お知らせの種類（設定の見本の DADS の注釈ブロック。skills/dads/icons と同じ絵・同じ名前）
    info: "M453-280h60v-240h-60v240Zm50.5-323.2q9.5-9.2 9.5-22.8 0-14.45-9.48-24.22-9.48-9.78-23.5-9.78t-23.52 9.78Q447-640.45 447-626q0 13.6 9.48 22.8 9.48 9.2 23.5 9.2t23.52-9.2ZM480.27-80q-82.74 0-155.5-31.5Q252-143 197.5-197.5t-86-127.34Q80-397.68 80-480.5t31.5-155.66Q143-709 197.5-763t127.34-85.5Q397.68-880 480.5-880t155.66 31.5Q709-817 763-763t85.5 127Q880-563 880-480.27q0 82.74-31.5 155.5Q817-252 763-197.68q-54 54.31-127 86Q563-80 480.27-80Zm.23-60Q622-140 721-239.5t99-241Q820-622 721.19-721T480-820q-141 0-240.5 98.81T140-480q0 141 99.5 240.5t241 99.5Zm-.5-340Z",
    check: "m421-298 283-283-46-45-237 237-120-120-45 45 165 166Zm59 218q-82 0-155-31.5t-127.5-86Q143-252 111.5-325T80-480q0-83 31.5-156t86-127Q252-817 325-848.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 82-31.5 155T763-197.5q-54 54.5-127 86T480-80Zm0-60q142 0 241-99.5T820-480q0-142-99-241t-241-99q-141 0-240.5 99T140-480q0 141 99.5 240.5T480-140Zm0-340Z",
    alert: "m40-120 440-760 440 760H40Zm104-60h672L480-760 144-180Zm361.5-65.68q8.5-8.67 8.5-21.5 0-12.82-8.68-21.32-8.67-8.5-21.5-8.5-12.82 0-21.32 8.68-8.5 8.67-8.5 21.5 0 12.82 8.68 21.32 8.67 8.5 21.5 8.5 12.82 0 21.32-8.68ZM454-348h60v-224h-60v224Zm26-122Z",
    error: "M503.5-289.48q9.5-9.48 9.5-23.5t-9.48-23.52q-9.48-9.5-23.5-9.5t-23.52 9.48q-9.5 9.48-9.5 23.5t9.48 23.52q9.48 9.5 23.5 9.5t23.52-9.48ZM453-433h60v-253h-60v253Zm27.27 353q-82.74 0-155.5-31.5Q252-143 197.5-197.5t-86-127.34Q80-397.68 80-480.5t31.5-155.66Q143-709 197.5-763t127.34-85.5Q397.68-880 480.5-880t155.66 31.5Q709-817 763-763t85.5 127Q880-563 880-480.27q0 82.74-31.5 155.5Q817-252 763-197.68q-54 54.31-127 86Q563-80 480.27-80Zm.23-60Q622-140 721-239.5t99-241Q820-622 721.19-721T480-820q-141 0-240.5 98.81T140-480q0 141 99.5 240.5t241 99.5Zm-.5-340Z",
    settings: "m370-80-16-128q-13-5-24.500-12T307-235l-119 50L78-375l103-78q-1-7-1-13.500v-27q0-6.500 1-13.500L78-585l110-190 119 50q11-8 23-15t24-12l16-128h220l16 128q13 5 24.500 12t22.500 15l119-50 110 190-103 78q1 7 1 13.500v27q0 6.500-2 13.500l103 78-110 190-118-50q-11 8-23 15t-24 12L590-80H370Zm70-80h79l14-106q31-8 57.500-23.500T639-327l99 41 39-68-86-65q5-14 7-29.500t2-31.500q0-16-2-31.500t-7-29.500l86-65-39-68-99 42q-22-23-48.500-38.500T533-694l-13-106h-79l-14 106q-31 8-57.500 23.500T321-633l-99-41-39 68 86 64q-5 15-7 30t-2 32q0 16 2 31t7 30l-86 65 39 68 99-42q22 23 48.500 38.500T427-266l13 106Zm42-180q58 0 99-41t41-99q0-58-41-99t-99-41q-59 0-99.500 41T342-480q0 58 40.500 99t99.500 41Zm-2-140Z"
  };
  SS.icon = function (name, cls) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 -960 960 960");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICON_PATHS[name] || ICON_PATHS.image);   // 知らない名前は汎用の絵
    svg.appendChild(path);
    return svg;
  };
  // ---- スライドの形式（プレゼン・ピッチ・モーション）----
  // 名前・説明・アイコン・色・書き出せる形式の**正本はサーバの語彙**（`SS.vocab.deck_kinds`。並び順もそこ）。
  // 画面に形式の名前を書かない。語彙が古くて無いときは、モードの名前だけで出す（アイコンは汎用）。
  SS.deckKinds = function () {
    var v = SS.vocab || {};
    if (Array.isArray(v.deck_kinds) && v.deck_kinds.length) { return v.deck_kinds; }
    var modes = v.deck_modes || {};
    return Object.keys(modes).map(function (m) { return { mode: m, label: modes[m], description: "", icon: null, tone: null, outputs: [] }; });
  };
  SS.deckKind = function (mode, fallbackLabel) {
    var all = SS.deckKinds();
    for (var i = 0; i < all.length; i++) { if (all[i].mode === mode) { return all[i]; } }
    return { mode: mode, label: fallbackLabel || mode || "", description: "", icon: null, tone: null, outputs: [] };
  };
  // 形式の札: 角丸・先頭にアイコン・形式の色（色の組は CSS の `data-tone`。色だけに頼らず、アイコンと字も付ける）
  SS.kindTag = function (mode, fallbackLabel) {
    if (!mode) { return null; }
    var k = SS.deckKind(mode, fallbackLabel);
    return SS.h("span", { class: "kind-tag", "data-tone": k.tone || "neutral", "data-mode": mode },
      SS.icon(k.icon || "image"), SS.h("span", { text: k.label }));
  };
  SS.clear = function (el) { while (el.firstChild) { el.removeChild(el.firstChild); } return el; };

  // 画面に出る日付はすべてここを通る（カード・一覧・ごみ箱の行・スライドの画面の表）。
  // 区切りはハイフン（`2026-09-10 16:49`。利用者の指定）。整形をもう 1 つ作らない。
  // `deck.js` の hourMinute は日付を出さない別物（時と分だけ）なので、ここと揃える対象ではない。
  SS.fmtDate = function (iso) {
    if (!iso) { return "—"; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return iso; }
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  };

  // ---- 通知（勝手に消える画面だけで伝えない: 操作つきは残す）----
  SS.toast = function (message, opts) {
    opts = opts || {};
    var box = document.getElementById("toasts");
    var el = SS.h("div", { class: "toast" + (opts.kind === "error" ? " toast--error" : "") }, SS.h("span", { text: message }));
    var timer = null;
    var close = function () { if (el.parentNode) { el.parentNode.removeChild(el); } if (timer) { clearTimeout(timer); } };
    if (opts.actionLabel && opts.onAction) {
      el.appendChild(SS.h("button", { class: "btn btn--small", type: "button", text: opts.actionLabel, onclick: function () { close(); opts.onAction(); } }));
    }
    el.appendChild(SS.h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "閉じる", onclick: close }, SS.icon("close")));
    box.appendChild(el);
    if (!opts.sticky) { timer = setTimeout(close, opts.actionLabel ? 12000 : 6000); }
    return close;
  };
  // サーバが意図して返した code には日本語の本文が付いている（server/errors.py）。
  // その文の後ろに英語の識別子を足すと、せっかくの文が機械のメッセージに見えるので足さない。
  // サーバが理由を用意できなかったとき（予期しない例外・素の HTTP の応答）だけは、
  // 利用者が問い合わせで指せるように code を添える。
  var UNEXPLAINED_CODE = /^(unknown|internal_error|http_\d+)$/;
  SS.toastError = function (e) {
    var msg = e && e.message ? e.message : "処理できませんでした。";
    if (e && e.code && UNEXPLAINED_CODE.test(e.code)) { msg += "（" + e.code + "）"; }
    SS.toast(msg, { kind: "error", sticky: true });
  };

  // ---- メニュー（同じウィンドウ内のポップアップ）----
  var openMenu = null;
  var modalSeq = 0;
  function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; document.removeEventListener("click", onDocClick, true); document.removeEventListener("keydown", onKey, true); } }
  function onDocClick(e) { if (openMenu && !openMenu.contains(e.target)) { closeMenu(); } }
  // Esc はメニューだけを閉じる（外側の板・ダイアログまで閉じない）
  function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); closeMenu(); } }
  // items: [{label, onSelect, disabled, reason, danger, checked}] / "-" は区切り
  // `checked` を持つ項目はオン・オフの項目（メニューを開いたときの状態を見せる）
  // opts: {up: 上へ開く, left: 左端をそろえる}（置いた側が知っていることを宣言する）
  SS.menu = function (anchor, items, opts) {
    opts = opts || {};
    if (openMenu && openMenu.__anchor === anchor) { closeMenu(); return; }
    closeMenu();
    var ul = SS.h("ul", { class: "menu", role: "menu" });
    items.forEach(function (it) {
      if (it === "-") { ul.appendChild(SS.h("li", { class: "menu__sep", role: "separator" })); return; }
      var li = SS.h("li", { role: "none" });
      var toggle = it.checked !== undefined;
      var b = SS.h("button", { type: "button", role: toggle ? "menuitemcheckbox" : "menuitem", text: it.label, disabled: !!it.disabled,
        "aria-disabled": it.disabled ? "true" : null, "aria-checked": toggle ? (it.checked ? "true" : "false") : null,
        onclick: function () { if (it.disabled) { return; } closeMenu(); it.onSelect(); } });
      if (it.danger) { b.style.color = "var(--color-error)"; }
      li.appendChild(b);
      if (it.disabled && it.reason) { li.appendChild(SS.h("div", { class: "menu__reason", text: it.reason })); }
      ul.appendChild(li);
    });
    var holder = anchor.closest(".menu-anchor") || anchor.parentNode;
    holder.appendChild(ul);
    // 開く向き。既定は下。ボタンが器の下端にあって下に開けない置き場（カードの最下段の行）は
    // 器のクラス `menu-anchor--up` で**上へ**開くと宣言する（置いた側が知っていることなので測らない）
    if (opts.up || (holder.classList && holder.classList.contains("menu-anchor--up"))) {
      ul.style.bottom = (holder.clientHeight - anchor.offsetTop + 2) + "px";
    } else {
      ul.style.top = (anchor.offsetTop + anchor.offsetHeight + 2) + "px";
    }
    if (opts.left) {
      ul.style.left = Math.max(0, anchor.offsetLeft) + "px";
      ul.style.right = "auto";
    } else {
      var right = holder.clientWidth - (anchor.offsetLeft + anchor.offsetWidth);
      ul.style.right = Math.max(0, right) + "px";
    }
    ul.__anchor = anchor;
    openMenu = ul;
    setTimeout(function () { document.addEventListener("click", onDocClick, true); document.addEventListener("keydown", onKey, true); }, 0);
    var first = ul.querySelector("button:not(:disabled)");
    if (first) { first.focus(); }
  };
  SS.closeMenu = closeMenu;

  // ---- 使えない操作の表示（U7）: ボタンは置くが押せない。理由は必ず日本語で見える ----
  SS.cap = function (caps, key) {
    var c = caps && caps[key];
    return c || { available: false, label: key, reason: "この操作が使えるかどうかをサーバから読めませんでした" };
  };
  // 「押せるか・何と書くか・押せない理由」を**1 か所で**ボタンに当てる。作るときも、あとで
  // 答えが変わったときも同じ関数を通す（作るときと当て直すときで見た目が食い違わない）。
  // 当て直しが要るのは、`capabilities` が**開いている間に変わる**からである（生成が始まる・終わる）。
  // `labelOverride` は「1 つの可否を 2 つ以上のボタンが分け合う」ときだけ渡す（書き出しの PDF / PPTX）。
  // **画面に語を書くための穴ではない**: 渡す文字列の正本もサーバ（`vocabulary.export_formats`）である
  SS.applyCap = function (b, caps, key, labelOverride) {
    var c = SS.cap(caps, key);
    var label = labelOverride || c.label;
    if (b.textContent !== label) { b.textContent = label; }
    b.disabled = !c.available;
    if (c.available) {
      b.removeAttribute("aria-disabled");
      b.removeAttribute("title");
    } else {
      b.setAttribute("aria-disabled", "true");
      b.setAttribute("title", c.reason || "");
    }
    return b;
  };
  SS.disabledButton = function (caps, key, extraClass, labelOverride) {
    return SS.applyCap(SS.h("button", { class: "btn " + (extraClass || ""), type: "button" }), caps, key, labelOverride);
  };

  // ---- 画面遷移（pushState。単一ウィンドウ）----
  SS.navigate = function (path, replace) {
    if (SS.settingsLeave && SS.settingsLeave.dirty() && !SS.confirm("保存していない入力があります。画面を移動しますか？")) { return; }
    SS.settingsLeave = null;
    if (replace) { history.replaceState(null, "", path); } else { history.pushState(null, "", path); }
    SS.render();
  };
  SS.route = function () {
    var p = location.pathname;
    // URL の語は「スライド」1 つ（ADR 0041 §1）。古い `/library` `/decks/<id>` はサーバが 301 で
    // ここへ送るので、画面側は新しい形だけを知っていればよい
    var m = /^\/slides\/([0-9a-f-]{36})$/.exec(p);
    if (m) { return { name: "deck", deckId: m[1] }; }
    if (p === "/slides") { return { name: "list" }; }
    if (p === "/settings") { return { name: "settings", section: "brands" }; }
    if (p === "/settings/styles") { return { name: "settings", section: "styles" }; }
    if (p === "/settings/server") { return { name: "settings", section: "server" }; }
    var b = /^\/settings\/brands\/([0-9a-f-]{36})$/.exec(p);
    if (b) { return { name: "settings", section: "brand", brandId: b[1] }; }
    return { name: "home" };
  };
  var renderSeq = 0;
  SS.render = function () {
    closeMenu();
    document.documentElement.classList.remove("is-locked");   // 引き出す板が開いたまま画面が替わっても、背景の固定を残さない
    var r = SS.route();
    if (r.name !== "settings") { SS.settingsLeave = null; }
    var root = document.getElementById("view");
    // 画面ごとに本文の幅を変えられるように、いまの画面名を body に書く（CSS 側で使う）。
    // 上のバーの aria-current を書き換えているのと同じ場所なので、置き場としてここが自然
    document.body.dataset.route = r.name;
    document.querySelectorAll(".topbar a[data-route]").forEach(function (a) {
      if (a.dataset.route === r.name) { a.setAttribute("aria-current", "page"); } else { a.removeAttribute("aria-current"); }
    });
    var seq = ++renderSeq;
    var view = SS.views[r.name];
    var guard = function () { return seq === renderSeq; };
    Promise.resolve(view(root, r, guard)).catch(function (e) {
      if (!guard()) { return; }
      SS.clear(root).appendChild(SS.errorBlock(e));
    });
  };
  SS.errorBlock = function (e) {
    var msg = e && e.message ? e.message : "画面を読み込めませんでした。";
    return SS.h("div", { class: "notice notice--error", role: "alert" },
      SS.h("p", { text: msg }),
      e && e.code ? SS.h("p", { class: "small", text: "コード: " + e.code }) : null,
      SS.h("p", null, SS.h("button", { class: "btn btn--small", type: "button", text: "再読み込み", onclick: function () { SS.render(); } })));
  };
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-nav]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) { return; }
    e.preventDefault();
    SS.navigate(a.getAttribute("href"));
  });
  window.addEventListener("popstate", function () { SS.render(); });

  // ---- 再起動（P4）----
  // POST /api/server/restart のあと、同じ URL の health を見張る。インスタンスが替われば（同じ URL で起動した・
  // 新しい設定で起動できずに前の設定へ戻した）この画面を読み直す。URL が変わる再起動では、前の URL が応答しなく
  // なってから RESTART_GRACE_MS 待っても戻ってこなければ、新しい URL へ移る（別のオリジンの health は CSP の
  // connect-src 'self' で読めないので、前の URL が戻ってこないことを「新しい設定で起動した」の印にする）。
  // どちらでも ?restarted=<id> を付けて開き直し、app.js が health の restart で結果（前の設定に戻した理由など）を出す。
  var RESTART_GRACE_MS = 8000;
  var RESTART_POLL_MS = 500;
  SS.restartServer = function (mode, say) {
    say = say || function () {};
    return SS.api("/api/server/restart", { method: "POST", body: { mode: mode } }).then(function (r) {
      var oldInstance = r.instance_id, id = r.restart_id, target = r.same_url ? null : r.expected_url;
      say(mode === "wait" && r.running_jobs ? "処理中の作業（" + r.running_jobs + " 件）が終わるのを待っています…" : "再起動しています…");
      var downSince = null;
      return new Promise(function (resolve) {
        function land(base) {
          var path = location.pathname + "?restarted=" + encodeURIComponent(id);
          say("再接続しています…");
          resolve(base || location.origin);
          if (base) { location.href = base + path; } else { location.replace(path); }
        }
        function down() {
          if (downSince === null) { downSince = Date.now(); say("再起動が終わるのを待っています…"); }
          if (target && Date.now() - downSince > RESTART_GRACE_MS) { land(target); return; }
          setTimeout(tick, RESTART_POLL_MS);
        }
        function tick() {
          fetch("/api/health", { cache: "no-store" }).then(function (res) {
            if (!res.ok && res.status !== 503) { throw new Error("not ours"); }
            return res.json();
          }).then(function (j) {
            if (j && j.instance_id && j.instance_id !== oldInstance) { land(null); return; }
            downSince = null;
            setTimeout(tick, RESTART_POLL_MS);
          }).catch(down);
        }
        setTimeout(tick, RESTART_POLL_MS);
      });
    });
  };

  // ---- 共通の確認 ----
  SS.confirm = function (message) { return window.confirm(message); };

  // ---- 1 項目のモーダル（同じウィンドウ内。名前の入力に使う）----
  // opts: {title, label, value, submitLabel, maxlength, onSubmit(value) -> Promise}
  // 送信中は押せない。断られたときはモーダルを閉じず、サーバの日本語をその場に出す。
  SS.textModal = function (opts) {
    var id = "modal-input-" + (++modalSeq);
    var input = SS.h("input", { type: "text", id: id, maxlength: opts.maxlength || 200 });
    input.value = opts.value == null ? "" : opts.value;
    var errors = SS.h("p", { class: "reason", role: "alert" });
    var submit = SS.h("button", { class: "btn btn--primary", type: "button", text: opts.submitLabel || "保存する" });
    var cancel = SS.h("button", { class: "btn", type: "button", text: "やめる" });
    var box = SS.h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": opts.title },
      SS.h("h3", { text: opts.title }),
      SS.h("label", { class: "setting-field", for: id }, SS.h("span", { text: opts.label }), input),
      errors,
      SS.h("div", { class: "modal-actions" }, cancel, submit));
    var backdrop = SS.h("div", { class: "modal-backdrop" }, box);
    function close() { document.removeEventListener("keydown", onEscape, true); if (backdrop.parentNode) { backdrop.parentNode.removeChild(backdrop); } }
    function onEscape(e) { if (e.key === "Escape") { e.preventDefault(); close(); } }
    function go() {
      var value = input.value.trim();
      if (!value) { errors.textContent = opts.label + "を入力してください。"; input.focus(); return; }
      submit.disabled = true;
      Promise.resolve(opts.onSubmit(value)).then(close, function (e) {
        submit.disabled = false;
        errors.textContent = (e && e.message) ? e.message : "処理できませんでした。もう一度お試しください。";
      });
    }
    submit.addEventListener("click", go);
    cancel.addEventListener("click", close);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); go(); } });
    backdrop.addEventListener("click", function (e) { if (e.target === backdrop) { close(); } });
    document.addEventListener("keydown", onEscape, true);
    document.body.appendChild(backdrop);
    input.focus();
    input.select();
    return close;
  };
})();
