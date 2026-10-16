// Settings are a local, no-build extension of the single-window application.
// 一覧（/settings）と編集（/settings/brands/<id>）は別画面。配色の算出はサーバが正本であり、
// ここでは preview 応答の数値をそのまま塗るだけで、色も座標も計算しない。
(function () {
  "use strict";
  var SS = window.SS, h = SS.h;
  var clone = function (value) { return JSON.parse(JSON.stringify(value)); };
  var LOGO_ROLE = { normal: "logo_normal", white: "logo_white", transparent: "logo_transparent", symbol: "logo_symbol" };
  var nextInput = 0;
  SS.settingsLeave = null;

  var EXAMPLE_NOTE = "これはイメージです。実際に描いたスライドではありません。";

  function text(tag, value, cls) { return h(tag, { text: value, class: cls }); }
  function button(label, fn, cls) { return h("button", { type: "button", class: "btn " + (cls || ""), text: label, onclick: fn }); }
  function safeError(error) { return error instanceof SS.ApiError ? error.message : "処理を確認できませんでした。接続を確認してやり直してください。"; }
  function field(label, value, attrs) {
    var id = "setting-input-" + (++nextInput);
    var control = h(attrs && attrs.multiline ? "textarea" : "input", Object.assign({ id: id, type: "text" }, attrs || {}));
    control.value = value == null ? "" : value;
    return { el: control, node: h("label", { class: "setting-field", for: id }, text("span", label), control) };
  }
  function normalizeHex(value) {
    var raw = (value || "").trim();
    if (/^[0-9a-fA-F]{6}$/.test(raw)) { return "#" + raw.toUpperCase(); }
    if (/^#[0-9a-fA-F]{6}$/.test(raw)) { return raw.toUpperCase(); }
    return null;
  }
  function colorField(label, value, changed, optional, initiallyEnabled) {
    var id = "setting-input-" + (++nextInput), valid = normalizeHex(value) || getComputedStyle(document.documentElement).getPropertyValue("--color-text").trim();
    var picker = h("input", { type: "color", id: id, value: valid, "aria-label": label + "の色を選ぶ" });
    var code = h("input", { type: "text", value: value || "", maxlength: 7, placeholder: "#RRGGBB", "aria-label": label + "の16進コード" });
    var enabled = optional ? h("input", { type: "checkbox", id: "setting-enable-" + (++nextInput), "aria-label": label + "を指定する" }) : null;
    if (enabled) { enabled.checked = initiallyEnabled === undefined ? !!value : initiallyEnabled; }
    function syncFromCode() { var hex = normalizeHex(code.value); if (hex) { code.value = hex; picker.value = hex; } changed(); }
    picker.addEventListener("input", function () { code.value = picker.value.toUpperCase(); changed(); });
    code.addEventListener("input", changed); code.addEventListener("change", syncFromCode);
    function setEnabled(active) { enabled.checked = active; picker.disabled = !active; code.disabled = !active; }
    if (enabled) { enabled.addEventListener("change", function () { setEnabled(enabled.checked); changed(); }); setEnabled(enabled.checked); }
    return { node: h("div", { class: "setting-field color-field" }, enabled ? h("label", { for: enabled.id }, enabled, " " + label) : text("span", label), h("div", { class: "color-field__controls" }, picker, code)), value: function () { return normalizeHex(code.value); }, enabled: enabled, setEnabled: setEnabled };
  }

  // ---- 見本（カルーセル）----
  // プレゼン（html-slide）の型の縮図。色は preview 応答の slide.roles（エンジンの deck_palette が作る役）をそのまま塗り、
  // 字の大きさ・余白・角はエンジンの雛形（DADS のスライド用ローカルデザインシステム）の数字を 1920×1080 の比で写す（CSS 側）。
  // 座標は preview 応答の値を見本の幅・高さで割るだけ。ここで幾何を作らない。
  function pct(ratio) { return (ratio * 100) + "%"; }
  // 予約枠 [x0, y0, x1, y1] と ロゴ [x, y, w, h] を、見本の幅・高さに対する比へ直すだけ。
  // ここが応答の数値を割る唯一の場所であり、位置を作り出す計算はしない。
  function boxRatio(box, canvas) { return [box[0] / canvas[0], box[1] / canvas[1], (box[2] - box[0]) / canvas[0], (box[3] - box[1]) / canvas[1]]; }
  function rectRatio(rect, canvas) { return [rect[0] / canvas[0], rect[1] / canvas[1], rect[2] / canvas[0], rect[3] / canvas[1]]; }
  function place(node, ratios) { node.style.left = pct(ratios[0]); node.style.top = pct(ratios[1]); node.style.width = pct(ratios[2]); node.style.height = pct(ratios[3]); }

  // 役の名前（エンジンの CSS 変数と同じ）→ 見本の custom property（--s-<役>）
  function paintRoles(node, roles) {
    Object.keys(roles).forEach(function (role) { node.style.setProperty("--s-" + role, roles[role]); });
  }

  function sample(kind, label) { return h("div", { class: "sample sample--" + kind, role: "img", "aria-label": label }); }

  // 下の部品（フッタ・ページ番号・透かし）。位置は preview 応答の chrome（エンジンの既定の寸法の写し）を canvas で割るだけ。
  // フッタとページ番号は中身のページだけ（表紙・章扉には入らない。エンジンの決まり）。透かしは全ページ。
  // ページ番号は見本の並びの番号（表紙が 1 枚目）。表紙・章扉には出ないので、中身のページは 3 から
  function footerLine(p, content, pageNo) {
    if (!p.canvas || !p.chrome) { return null; }
    var parts = [];
    if (content && p.chrome.footer && p.copyright && p.copyright.text) {
      var foot = text("p", p.copyright.text, "sample__footer sample__chrome");
      place(foot, rectRatio(p.chrome.footer, p.canvas)); parts.push(foot);
    }
    if (content && p.chrome.page_number) {
      var no = text("p", String(pageNo), "sample__pageno sample__chrome");
      place(no, rectRatio(p.chrome.page_number, p.canvas)); parts.push(no);
    }
    if (p.chrome.watermark && p.format && p.format.watermark) {
      var wm = text("p", p.format.watermark, "sample__watermark sample__chrome");
      place(wm, rectRatio(p.chrome.watermark, p.canvas)); parts.push(wm);
    }
    return parts.length ? parts : null;
  }

  // ロゴは選んだ角に入る（ふさがっていれば描くときにエンジンが別の角へ逃がす。見本は選んだ角の姿）。
  // 座標は preview 応答の logo_xywh を canvas で割った値だけを使う。
  function logoOverlay(p) {
    if (!p.canvas || !p.image_url || !p.logo_xywh) { return null; }
    var img = h("img", { class: "sample__logo", src: p.image_url, alt: "登録したロゴ" });
    place(img, rectRatio(p.logo_xywh, p.canvas));
    var lost = text("p", "ロゴ画像を読み込めません。画像を選び直してください。", "sample__lost");
    lost.hidden = true;
    img.onerror = function () { img.hidden = true; lost.hidden = false; };
    return [img, lost];
  }

  // 中身の型の左上の見出し（キッカー 28B・見出し 64B）と上の帯
  function slideHead(kicker, headline) {
    return [h("div", { class: "s-band", "aria-hidden": "true" }),
      h("div", { class: "s-head" }, text("p", kicker, "s-kicker"), text("p", headline, "s-headline"))];
  }

  function coverSample() {
    var node = sample("cover", "表紙の見本");
    node.appendChild(h("div", { class: "s-cover" },
      text("p", "事業計画", "s-cover__label"),
      text("p", "次の成長に向けた3つの取り組み", "s-cover__title"),
      h("div", { class: "s-cover__rule", "aria-hidden": "true" }),
      text("p", "2026年度 経営会議", "s-cover__subtitle")));
    node.appendChild(h("div", { class: "s-cover__meta" }, text("p", "2026年10月"), text("p", "事業推進部")));
    return node;
  }

  function sectionSample() {
    var node = sample("section", "章扉の見本");
    node.appendChild(h("div", { class: "s-sec__panel", "aria-hidden": "true" }));
    node.appendChild(h("div", { class: "s-sec__side" }, text("p", "第1章", "s-sec__label"), text("p", "01", "s-sec__no")));
    node.appendChild(h("div", { class: "s-sec__text" }, text("p", "いまの立ち位置", "s-sec__title"),
      text("p", "市場の動きと、私たちの強み", "s-sec__subtitle")));
    return node;
  }

  function bulletsSample() {
    var node = sample("bullets", "箇条書きの見本");
    SS.append(node, slideHead("現状と打ち手", "重点施策は3つに絞る"));
    var list = h("div", { class: "s-list" });
    [["国内は既存顧客の深耕", "解約率を下げ、単価を上げる"], ["海外は2拠点に集中", "伸びている地域から先に投資する"], ["開発は共通基盤へ", "重複している機能をまとめる"]].forEach(function (item, index) {
      list.appendChild(h("div", { class: "s-li" }, text("span", String(index + 1), "s-li__no"),
        h("div", { class: "s-li__text" }, text("p", item[0], "s-li__title"), text("p", item[1], "s-li__body"))));
    });
    node.appendChild(list);
    return node;
  }

  function chartSample() {
    var node = sample("chart", "棒グラフの見本");
    SS.append(node, slideHead("実績", "第3四半期に前年比118%まで回復"));
    var chart = h("div", { class: "s-chart" });
    [["第1四半期", "92", 46], ["第2四半期", "104", 52], ["第3四半期", "118", 59, true], ["第4四半期", "110", 55]].forEach(function (bar) {
      var group = h("div", { class: "s-chart__group" + (bar[3] ? " s-chart__group--hl" : "") },
        text("span", bar[1], "s-chart__value"), h("span", { class: "s-chart__bar" }), text("span", bar[0], "s-chart__label"));
      group.querySelector(".s-chart__bar").style.height = bar[2] + "%";
      chart.appendChild(group);
    });
    node.appendChild(chart);
    node.appendChild(h("div", { class: "s-callout" }, text("p", "118%", "s-callout__value"),
      text("p", "前年同期比。新しい顧客層の受注が伸びた。", "s-callout__text")));
    return node;
  }

  // お知らせの枠（DADS の注釈ブロック）。種類は色だけでなく、アイコンと語でも示す（エンジンの NOTICE_KINDS と同じ組）
  var NOTICE_KINDS = [
    { kind: "info", label: "お知らせ", icon: "info", title: "出典は2025年度の年次報告です" },
    { kind: "success", label: "完了", icon: "check", title: "新しい手順への移行が完了しました" },
    { kind: "warning", label: "注意", icon: "alert", title: "この数値は速報値です" },
    { kind: "error", label: "重要", icon: "error", title: "旧システムは3月末で停止します" }
  ];
  function noticeSample() {
    var node = sample("notice", "お知らせの枠の見本");
    SS.append(node, slideHead("お知らせ", "移行作業の進みぐあい"));
    var stack = h("div", { class: "s-notices" });
    NOTICE_KINDS.forEach(function (n) {
      stack.appendChild(h("div", { class: "s-notice s-notice--" + n.kind },
        h("div", { class: "s-notice__band", "aria-hidden": "true" }),
        h("div", { class: "s-notice__main" },
          h("p", { class: "s-notice__label" }, SS.icon(n.icon, "s-notice__icon"), text("span", n.label)),
          text("p", n.title, "s-notice__title"))));
    });
    node.appendChild(stack);
    return node;
  }

  var SAMPLE_SLIDES = [
    { caption: "表紙。ラベルと題の下線に主役の色が入ります。", build: coverSample, content: false },
    { caption: "章扉。主役の色を大きな面で使い、白い文字を載せます。", build: sectionSample, content: false },
    { caption: "箇条書き。番号の丸は主役の色、補足は灰色の文字です。", build: bulletsSample, content: true },
    { caption: "棒グラフ。強める棒と囲みに強調色を使い、ほかの棒は灰色です。", build: chartSample, content: true },
    { caption: "お知らせの枠。成功・注意・エラーは DADS の決まった色で、アイコンと語を必ず添えます。", build: noticeSample, content: true }
  ];

  // 手動送りのみ。自動送りは実装しない。送りで preview を取り直さない（見比べているものが飛ばないため）。
  // 送りに動きは付けない（prefers-reduced-motion の既存規約と衝突させないため、常に即座に切り替える）。
  function sampleCarousel() {
    var index = 0, latest = null;
    var stage = h("div", { class: "carousel__stage" });
    var caption = text("p", "", "carousel__caption small");
    var note = text("p", EXAMPLE_NOTE, "carousel__note small muted");
    var dots = h("div", { class: "carousel__dots" });
    var counter = text("span", "", "carousel__counter small muted");
    var back = button("", function () { move(-1); }, "btn--icon btn--ghost");
    var forward = button("", function () { move(1); }, "btn--icon btn--ghost");
    back.appendChild(SS.icon("arrow_back"));
    forward.appendChild(SS.icon("arrow_forward"));
    back.setAttribute("aria-label", "前の見本");
    forward.setAttribute("aria-label", "次の見本");
    var root = h("section", { class: "carousel", tabindex: "0", "aria-label": "配色の見本（左右の矢印キー・スワイプ・クリックで送る）" },
      note, stage, h("div", { class: "carousel__controls" }, back, dots, counter, forward), caption);

    function move(delta) { var list = SAMPLE_SLIDES; index = (index + delta + list.length) % list.length; draw(); }
    function draw() {
      var list = SAMPLE_SLIDES;
      if (index >= list.length) { index = list.length - 1; }
      SS.clear(dots);
      list.forEach(function (slide, position) {
        var on = position === index;
        dots.appendChild(h("button", { type: "button", class: "carousel__dot" + (on ? " carousel__dot--on" : ""),
          text: on ? "●" : "○", "aria-label": (position + 1) + "枚目の見本を見る", "aria-current": on ? "true" : null,
          onclick: function () { index = position; draw(); } }));
      });
      counter.textContent = (index + 1) + " / " + list.length;
      caption.textContent = list[index].caption;
      SS.clear(stage);
      if (!latest) { stage.appendChild(text("p", "見本はまだ作れていません。基本色と強調色を入力してください。", "reason")); return; }
      if (!latest.slide || !latest.slide.roles) {
        stage.appendChild(text("p", "この配色ではプレゼンの色を作れないため、見本を出せません。", "notice notice--error")); return;
      }
      paintRoles(stage, latest.slide.roles);
      var slide = list[index].build(latest);
      SS.append(slide, footerLine(latest, list[index].content, index + 1));
      SS.append(slide, logoOverlay(latest));
      stage.appendChild(slide);
    }
    root.addEventListener("keydown", function (event) {
      if (event.target !== root) { return; }
      if (event.key === "ArrowRight") { move(1); } else if (event.key === "ArrowLeft") { move(-1); } else { return; }
      event.preventDefault();
    });
    // 見本の上でも送れる: 左右のスワイプ（指・ペン・マウスのドラッグ）と、クリック（左の 3 分の 1 は前・ほかは次）。
    // 縦のスクロールは止めない（CSS の touch-action: pan-y）。スワイプした直後のクリックは送りに数えない
    var SWIPE_PX = 40, startX = null, startY = 0, swiped = false;
    stage.addEventListener("pointerdown", function (event) {
      if (!event.isPrimary || event.button > 0) { return; }
      startX = event.clientX; startY = event.clientY; swiped = false;
    });
    stage.addEventListener("pointerup", function (event) {
      if (startX === null) { return; }
      var dx = event.clientX - startX, dy = event.clientY - startY;
      startX = null;
      if (Math.abs(dx) >= SWIPE_PX && Math.abs(dx) > Math.abs(dy)) { swiped = true; move(dx < 0 ? 1 : -1); }
    });
    stage.addEventListener("pointercancel", function () { startX = null; });
    stage.addEventListener("click", function (event) {
      if (swiped) { swiped = false; return; }
      if (!latest || !stage.querySelector(".sample")) { return; }
      var box = stage.getBoundingClientRect();
      move(event.clientX - box.left < box.width / 3 ? -1 : 1);
    });
    draw();
    return { node: root, update: function (p) { latest = p; draw(); }, show: function (position) { index = position; draw(); } };
  }

  // 「他の要素の色」の行。色は preview 応答の slide.roles（エンジンが塗る値）だけ。
  // edit があれば、その色は人が変えられる（ブランドの colors のキー。サーバの slide_palette.EDITABLE と同じ 4 つ）
  var ROLE_ROWS = [
    { role: "primary", label: "主役の色（文字・面）" }, { role: "primary-mark", label: "主役の色（図形・線）" }, { role: "primary-soft", label: "主役の色（淡い地）" },
    { role: "accent", label: "強調色（文字・面）" }, { role: "accent-mark", label: "強調色（図形・線）" }, { role: "accent-soft", label: "強調色（淡い地）" },
    { role: "bg", label: "背景", edit: "base" }, { role: "surface", label: "カード・表の背景", edit: "surface" },
    { role: "ink", label: "本文", edit: "ink" }, { role: "muted", label: "補足", edit: "muted" }, { role: "rule", label: "罫線・枠" }
  ];
  var EDIT_KEYS = ROLE_ROWS.filter(function (row) { return row.edit; }).map(function (row) { return row.edit; });
  var INPUT_LABELS = { primary: "基本色", accent: "強調色" };

  // 「他の要素の色」: 基本色・強調色からエンジンが計算した色（はじめは畳む）。変えられる 4 色は色見本を押して選ぶ。
  // 変えた行には「変更済み」と、その行だけを戻すボタンを付ける。部品は一度だけ作り、見本を更新するたびに値だけを差し替える
  // （作り直すと、色を選んでいる最中に選ぶ窓が閉じるため）。overrides は画面の持ち物（{colors のキー: "#RRGGBB"}）
  function otherColorsPanel(overrides, onChange) {
    var problems = h("div", { class: "slide-colors__problems", role: "alert" });
    var lead = text("p", "", "small slide-colors__lead");
    var resetAll = h("button", { type: "button", class: "btn btn--ghost btn--small slide-colors__reset-all", hidden: true },
      SS.icon("replay"), text("span", "すべて自動に戻す"));
    var editTitle = h("p", { class: "slide-colors__group", tabindex: "-1", text: "変えられる色" });
    resetAll.addEventListener("click", function () { EDIT_KEYS.forEach(function (key) { delete overrides[key]; }); onChange(); editTitle.focus({ preventScroll: true }); });
    // 変えられる色（4 つ）と、自動で決まる色を別の一覧にする（押せる行が 1 か所にまとまる）
    var editList = h("ul", { class: "slide-colors__list" }), autoList = h("ul", { class: "slide-colors__list" });
    var rows = ROLE_ROWS.map(function (row) {
      var entry = { row: row, hex: text("code", "", "slide-colors__hex") };
      var nameText = text("span", row.label, "slide-colors__name");
      if (row.edit) {
        var id = "other-color-" + row.edit + "-" + (++nextInput);
        entry.picker = h("input", { type: "color", id: id, class: "slide-colors__picker", "aria-describedby": id + "-state" });
        entry.picker.addEventListener("input", function () { overrides[row.edit] = entry.picker.value.toUpperCase(); });   // 見本の更新は fieldset が拾う
        entry.state = h("span", { id: id + "-state", class: "slide-colors__state" });
        entry.note = text("span", "", "slide-colors__warn");
        entry.undo = h("button", { type: "button", class: "btn btn--icon btn--ghost slide-colors__undo", "aria-label": row.label + "を自動に戻す",
          title: row.label + "を自動に戻す", hidden: true }, SS.icon("replay"));
        // 戻したあとのフォーカスは行へ移す（色見本へ移すと、環境によっては色を選ぶ窓が開く。押したボタンは隠れる）
        entry.undo.addEventListener("click", function () { delete overrides[row.edit]; onChange(); entry.item.focus({ preventScroll: true }); });
        entry.item = h("li", { class: "slide-colors__item slide-colors__item--edit", tabindex: "-1" }, entry.picker,
          h("label", { class: "slide-colors__text", for: id }, nameText, entry.hex, entry.state, entry.note), entry.undo);
      } else {
        entry.chip = h("span", { class: "slide-colors__chip", "aria-hidden": "true" });
        entry.item = h("li", { class: "slide-colors__item" }, entry.chip, h("span", { class: "slide-colors__text" }, nameText, entry.hex));
      }
      (row.edit ? editList : autoList).appendChild(entry.item);
      return entry;
    });
    var adjustBox = h("div", { class: "adjust-rows", hidden: true });
    var autoNote = text("p", "", "small muted slide-colors__lead");
    var details = h("details", { class: "slide-colors" },
      h("summary", { class: "slide-colors__summary" }, "他の要素の色"),
      h("div", { class: "slide-colors__head" }, titleRow(editTitle, "変えられる色", [
        "背景: スライドの地の色です。",
        "カード・表の背景: カード・比較の箱・指標の箱・表の見出しの行など、背景の上に置く箱の色です。",
        "本文: 見出しと本文の文字の色です。",
        "補足: 副題・説明・注記・出典・フッター・ページ番号など、控えめに見せる文字の色です。",
        "読めない組み合わせ（文字と背景の差が足りない）になると、保存できません。"]), resetAll), lead, editList,
      text("p", "自動で決まる色", "slide-colors__group"), autoNote, autoList, adjustBox);

    function swatch(value) {
      var c = h("span", { class: "slide-colors__chip", "aria-hidden": "true" }); c.style.background = value;
      return h("span", { class: "adjust-row__color" }, c, text("code", value.toUpperCase(), "slide-colors__hex"));
    }
    function update(slide) {
      SS.clear(problems);
      if (!slide || slide.error) {
        problems.appendChild(text("p", "この配色ではプレゼンを描けません。" + (slide && slide.error ? "（" + slide.error + "）" : ""), "notice notice--error small"));
      } else if (slide.problems && slide.problems.length) {
        problems.appendChild(text("p", "読みにくい色の組み合わせがあるため、このままでは保存できません。変えた色を見直すか、自動に戻してください。", "notice notice--error small"));
      }
      var roles = (slide && slide.roles) || {}, applied = (slide && slide.overrides) || {};
      var adjusted = (slide && slide.adjusted) || [];
      lead.textContent = "色見本を押すと変えられます。変えなければ自動の色を使います。";
      autoNote.textContent = "基本色と強調色から決めます。" + (adjusted.length ? "薄い色は、読みやすいように同じ色味の少し濃い色に置き換えます。" : "");
      var any = false;
      rows.forEach(function (entry) {
        var value = roles[entry.row.role] || "";
        entry.hex.textContent = value.toUpperCase();
        if (!entry.row.edit) { entry.chip.style.background = value; return; }
        var changed = overrides[entry.row.edit] != null, info = applied[entry.row.edit];
        any = any || changed;
        // 人が決めた色は、選んだ値のまま（選んでいる最中に書き戻さない。カード・表の背景が使われないときも、選んだ色を見せたまま理由を出す）。
        // 自動の色（決めていない・戻した）は、いつも使う色を書き戻す
        if (value && !changed) { entry.picker.value = value.toLowerCase(); }
        entry.item.classList.toggle("slide-colors__item--changed", changed);
        entry.state.textContent = changed ? "変更済み" : "自動";
        entry.undo.hidden = !changed;
        entry.note.textContent = changed && info && !info.applied ? info.reason : "";
      });
      resetAll.hidden = !any;
      SS.clear(adjustBox);
      adjustBox.hidden = !adjusted.length;
      if (adjusted.length) {
        adjustBox.appendChild(text("p", "自動で濃くした色", "adjust-rows__title"));
        adjusted.forEach(function (a) {
          adjustBox.appendChild(h("div", { class: "adjust-row", title: "背景との明るさの差 " + a.contrast + ":1 → 4.5:1 以上（DADS の文字の決まり）" },
            text("span", INPUT_LABELS[a.role], "adjust-row__name"), swatch(a.input), text("span", "→", "adjust-row__arrow"), swatch(a.used)));
        });
      }
    }
    return { node: h("div", { class: "slide-colors-wrap" }, problems, details), update: update, details: details };
  }

  // ヘルプ（見出しの横の「？」）は共通の部品（core.js の SS.helpTip）
  var helpTip = SS.helpTip;
  // 見出し（や項目名）と、その横の「？」を 1 行に並べる。helps が無ければ見出しだけ
  function titleRow(heading, topic, helps) {
    return helps && helps.length ? h("div", { class: "title-row" }, heading, helpTip(topic, helps)) : heading;
  }

  // ---- ロゴ ----
  // ロゴの画像 1 枚（役割 role）。PNG か JPEG（描くエンジンが受ける形）。画像は加工しない。
  // 送るのは data.assets 全部（画面が扱わない役割の画像も消さない。P1 のレビューの指摘）
  function logoSlot(data, opts, readonly, changed, fail) {
    var role = opts.role;
    var asset = data.assets.filter(function (item) { return item.role === role; })[0] || null;
    var enabled = h("input", { type: "checkbox", id: "logo-use-" + (++nextInput) }); enabled.checked = opts.initiallyOn !== undefined ? opts.initiallyOn && !!asset : !!asset;
    var file = h("input", { type: "file", accept: "image/png,image/jpeg", disabled: readonly || !enabled.checked, "aria-label": opts.label + "の画像" });
    // 登録した画像は、右に取り除くアイコンを添えて見せる（無ければ行ごと隠す）。読み込み中・登録の結果だけを文で知らせる
    var info = h("p", { class: "small logo-slot__info", "aria-live": "polite" }), img = h("img", { class: "logo-original", alt: opts.label + "のプレビュー" });
    var removeButton = h("button", { type: "button", class: "btn btn--icon btn--ghost logo-slot__remove", "aria-label": opts.label + "の画像を取り除く",
      title: opts.label + "の画像を取り除く", disabled: readonly }, SS.icon("delete"));
    var current = h("div", { class: "logo-slot__current", hidden: !asset }, img, removeButton);
    if (asset) { img.src = "/api/assets/" + asset.asset_id + "/file"; }
    var busy = false;
    function remove() { data.assets = data.assets.filter(function (item) { return item.role !== role; }); current.hidden = true; img.removeAttribute("src"); info.textContent = ""; changed(); }
    removeButton.addEventListener("click", function () { enabled.checked = false; file.disabled = true; remove(); enabled.focus(); });
    enabled.addEventListener("change", function () { file.disabled = readonly || !enabled.checked; if (!enabled.checked && opts.removeWhenOff) { remove(); } else { changed(); } });
    file.addEventListener("change", function () {
      var upload = file.files[0]; if (!upload || busy) { return; }
      if (upload.size > 10 * 1024 * 1024) { fail(new SS.ApiError("10 MiB以下の画像を選んでください。")); return; }
      if (upload.type && upload.type !== "image/png" && upload.type !== "image/jpeg") { fail(new SS.ApiError("ロゴは PNG か JPEG を選んでください。")); return; }
      busy = true; info.textContent = "画像を確認しています…";
      SS.uploadImage(upload).then(function (result) {
        data.assets = data.assets.filter(function (item) { return item.role !== role; }); data.assets.push({ asset_id: result.asset_id, role: role, label: opts.label });
        img.src = "/api/assets/" + result.asset_id + "/file"; current.hidden = false; info.textContent = result.width + "×" + result.height + " px の画像を登録しました。"; changed();
      }).catch(fail).finally(function () { busy = false; });
    });
    return { enabled: enabled, has: function () { return data.assets.some(function (item) { return item.role === role; }); },
      node: h("section", { class: "logo-slot" }, titleRow(h("label", { for: enabled.id }, enabled, " " + opts.checkLabel), opts.label, opts.help), file, current, info) };
  }

  // 角は 3 つのボタンで選ぶ。並びと表示名、そして角ごとの印の位置（mark）はすべてサーバのカタログの値を使い、
  // 角の名前から位置を導出する計算は画面に持たない（応答の数値を canvas で割るだけ）。
  function cornerPicker(control, initial, changed) {
    var value = initial, known = {};
    control.corners.forEach(function (option) {
      if (option.mark) { known[option.value] = rectRatio(option.mark, control.canvas); }
    });
    var grid = h("div", { class: "corner-grid", role: "radiogroup", "aria-label": "ロゴの位置" });
    var entries = control.corners.map(function (option) {
      var mini = h("span", { class: "corner-choice__mini", "aria-hidden": "true" });
      var node = h("button", { type: "button", class: "corner-choice", role: "radio",
        onclick: function () { value = option.value; sync(); changed(); } },
        mini, text("span", option.label, "corner-choice__label"));
      grid.appendChild(node);
      return { option: option, node: node, mini: mini };
    });
    function sync() {
      entries.forEach(function (entry) {
        var on = entry.option.value === value;
        entry.node.setAttribute("aria-checked", on ? "true" : "false");
        entry.node.className = "corner-choice" + (on ? " corner-choice--on" : "");
        SS.clear(entry.mini);
        var box = known[entry.option.value];
        if (!box) { return; }
        var mark = h("span", { class: "corner-choice__mark" });
        place(mark, box);
        entry.mini.appendChild(mark);
      });
    }
    sync();
    return {
      node: grid,
      value: function () { return value; },
      set: function (next) { value = next; sync(); },
      // 選んでいる角は、実際のロゴの寸法で置いた位置（preview 応答）で描き直す。
      learn: function (p) { if (p.logo_xywh && p.canvas) { known[value] = rectRatio(p.logo_xywh, p.canvas); sync(); } }
    };
  }

  // ---- 編集画面（/settings/brands/<id>）。サイドバーは置かない ----
  // 上の帯（名前・状態・保存）は画面の上に貼り付く。左に設定の欄（色 → ロゴ → フッター → ウォーターマーク → 詳細 → 削除）、右にプレビュー（貼り付く）。
  // 狭い画面ではプレビューを隠し、帯の「プレビュー」から全画面の板で開く。
  function editorSection(title, helps) {
    return h("section", { class: "editor-section" }, titleRow(text("h2", title, "editor-section__title"), title, helps));
  }
  function brandEditor(root, catalog, item) {
    var data = clone(item), readonly = item.kind === "preset", revisionId = item.current_revision_id;
    var timer = null, dirty = false, serial = 0, verifiedSerial = -1;
    var errors = h("div", { role: "alert", class: "brand-editor__errors" }), logoWarnings = h("div");
    var status = h("span", { class: "brand-editor__status small", "aria-live": "polite" });
    var saveState = text("span", "", "title-save-state small muted");
    var form = h("fieldset", { class: "brand-editor__form", disabled: readonly });
    var save = button("保存する", saveNow, "btn--primary");
    var carousel = sampleCarousel();
    // 「他の要素の色」で変えた色（{colors のキー: "#RRGGBB"}）。本文・補足は保存済みの値から、背景・カード・表の背景は最初の見本で見分ける
    var overrides = {}, overridesChecked = false, slideOk = true;
    ["ink", "muted"].forEach(function (key) { if (typeof (data.config.colors || {})[key] === "string") { overrides[key] = data.config.colors[key]; } });
    var otherColors = otherColorsPanel(overrides, function () { invalidate(); });
    var name = h("input", { class: "title-input", type: "text", maxlength: 200, "aria-label": "ブランドの名前", readonly: readonly });
    name.value = data.name;
    // 状態の文。狭い画面では短い言い方（short）に替える（帯にプレビューと保存のボタンを並べるため）。色だけでなく文字で伝える
    function setStatus(message, tone, short) {
      SS.clear(status);
      if (message) { status.appendChild(text("span", message, "status-long")); status.appendChild(text("span", short || message, "status-short")); }
      status.className = "brand-editor__status small" + (tone ? " brand-editor__status--" + tone : "");
    }
    function fail(error) { SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error")); }
    function invalidate() {
      serial++; verifiedSerial = -1; dirty = true; save.disabled = true; saveState.textContent = "";
      setStatus("見本を更新しています…", "", "更新中…");
      SS.settingsLeave = { dirty: function () { return dirty; } };
      clearTimeout(timer); timer = setTimeout(preview, 300);
    }
    form.addEventListener("input", invalidate);
    form.addEventListener("change", invalidate);
    var changed = invalidate;
    // 名前も色と同じ「保存する」の対象。Enter は入力を確定するだけで、保存も自動保存もしない。
    name.addEventListener("input", invalidate);
    name.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); name.blur(); } });

    // 色
    var colors = data.config.colors || {}, semantic = colors.semantic || {};
    var primary = colorField("基本色", colors.primary, changed), accent = colorField("強調色", colors.accent, changed);
    // 色は設定の欄の先頭（基本色・強調色 → 他の要素の色（畳む））。ほかの欄とは別の fieldset で、変更の拾い方は同じ
    var colorFieldset = h("fieldset", { class: "brand-editor__colors", disabled: readonly },
      titleRow(text("h2", "色", "editor-section__title"), "色", ["基本色は、見出しの印・番号・章扉に使います。",
        "強調色は、強める語・棒・囲みに使います。", "ほかの色は、この 2 色から自動で決めます（「他の要素の色」で見られます）。"]),
      h("div", { class: "color-pair" }, primary.node, accent.node),
      otherColors.node);
    colorFieldset.addEventListener("input", invalidate);
    colorFieldset.addEventListener("change", invalidate);

    // ロゴ: 使うロゴの役割は、いまの variant のもの（無ければ背景透過）
    var variant = (data.config.logo || {}).variant || "none";
    var mainVariant = variant !== "none" && variant !== "white" ? variant : "transparent";
    var logo = logoSlot(data, { role: LOGO_ROLE[mainVariant], label: "ロゴ", checkLabel: "ロゴを使う", initiallyOn: variant !== "none" },
      readonly, changed, fail);
    // 白いロゴはピッチ（画像で描く資料）だけが使う。無ければ、ロゴの後ろに地を敷いて読めるようにする
    var whiteLogo = logoSlot(data, { role: "logo_white", label: "白いロゴ", checkLabel: "白いロゴを登録する", removeWhenOff: true,
      help: ["ピッチ（画像で描く資料）で、写真や絵の上でロゴが沈むときに使います。", "登録しなければ、ロゴの後ろに地を敷いて読めるようにします。",
        "ロゴと同じ縦横比の、公式の白版だけを登録してください。"] }, readonly, changed, fail);
    var control = catalog.logo_control, currentPosition = Object.assign({}, control.default, (data.config.logo || {}).placement || {});
    var corner = cornerPicker(control, currentPosition[control.key], changed);
    var logoSection = editorSection("ロゴ", ["おすすめは、背景が透明な PNG です。どの背景の上にも馴染みます。",
      "JPEG も使えます（背景の色ごと入ります）。10 MiB 以下。", "画像は加工しません（色も縦横比もそのまま）。"]);
    logoSection.appendChild(logo.node);
    logoSection.appendChild(titleRow(text("p", "位置", "setting-label"), "ロゴの位置", ["選んだ角にロゴが入ります。",
      "写真や文字で角がふさがったページは、描くときに別の角（右下 ⇄ 左下・右上 → 右下）へ逃がします。"]));
    logoSection.appendChild(corner.node);
    logoSection.appendChild(logoWarnings);
    var whiteDetails = h("details", { class: "editor-details", open: whiteLogo.has() }, text("summary", "白いロゴ（任意・ピッチ用）"), whiteLogo.node);
    logoSection.appendChild(whiteDetails);
    form.appendChild(logoSection);

    // 書式（フッター・ページ番号・ウォーターマーク）はブランドの中に持つ
    var fmt = data.config.format || {}, limits = catalog.limits || {};
    // フッターとウォーターマークは入るページが違うので節を分ける（フッターは中身のページだけ・ウォーターマークは全ページ）。
    // 欄の名前に置く場所を書く。例は欄の中に置かず（DADS: プレースホルダーを使わない）、「？」で示す
    var footer = field("表示文字（左下に固定）", (data.config.footer || {}).text || "", { maxlength: limits.footer || 120 });
    footer.el.addEventListener("input", changed);
    var pageNumbers = h("input", { type: "checkbox", id: "brand-page-numbers-" + (++nextInput) }); pageNumbers.checked = !!fmt.page_numbers;
    var footerSection = editorSection("フッター", ["中身のページに入ります（表紙・章扉・全面の写真のページには入りません）。",
      "表示文字には、会社名や資料名を入れます（例: © 2026 会社名）。空にすれば入りません。"]);
    footerSection.appendChild(h("div", { class: "brand-format" }, footer.node,
      h("label", { class: "setting-field setting-check", for: pageNumbers.id }, pageNumbers, " ページ番号を出す（中央に固定）")));
    form.appendChild(footerSection);
    var watermark = field("表示文字", fmt.watermark || "", { maxlength: limits.watermark || 60 });
    watermark.el.addEventListener("input", changed);
    var watermarkSection = editorSection("ウォーターマーク", ["全ページに入ります（表紙・章扉も含みます）。",
      "いちばん下の端の中央に、小さく薄いグレーで入ります。扱いの注意を入れます（例: 社外秘・配布禁止）。空にすれば入りません。"]);
    watermarkSection.appendChild(h("div", { class: "brand-format" }, watermark.node));
    form.appendChild(watermarkSection);

    // 押せない理由は「まだ何も変えていない」「見本の読み直しが済んでいない」「読めない色の組み合わせ」のどれか。
    function refreshSave() {
      save.disabled = readonly || !dirty || verifiedSerial !== serial || !slideOk;
    }
    function config() {
      var primaryValue = primary.value(), accentValue = accent.value(); if (!primaryValue || !accentValue) { throw new SS.ApiError("基本色と強調色は #RRGGBB の6桁で入力してください。", "color_required"); }
      // 範囲から降ろした設定（src/quick_slide/scope.py）はこの画面で扱わない。**キーごと送らない。**
      // 保存済みの値はサーバが次の改訂へそのまま持ち越すので、保存し直しても消えない。
      var c = { schema_version: 1, colors: { primary: primaryValue, accent: accentValue }, logo: { variant: "none", placement: clone(currentPosition) },
        footer: { text: footer.el.value.trim(), style: { position: "footer_left" } },
        format: { page_numbers: !!pageNumbers.checked, watermark: watermark.el.value.trim() } };
      c.logo.placement[control.key] = corner.value();
      // 「他の要素の色」で変えた色だけを送る（送らない色は自動。画面から外した上書きはサーバが持ち越す）
      EDIT_KEYS.forEach(function (key) { if (overrides[key]) { c.colors[key] = overrides[key]; } });
      if (logo.enabled.checked) { c.logo.variant = mainVariant; }
      return c;
    }
    // 保存で送る形。名前と config（schema_version を含む）を 1 回で送り、改訂を 1 つだけ作る。
    // 画像は data.assets を全部送る（使っていない役割の画像も消さない）。サーバもロゴ以外の役割は持ち越す
    function requestBody() { var c = config(); return { name: name.value.trim(), config: c, assets: clone(data.assets) }; }
    function preview() {
      var body; try { body = requestBody(); } catch (error) { fail(error); setStatus("入力を確かめてください", "error", "入力を確認"); return Promise.resolve(); }
      var own = serial;
      return SS.api("/api/settings/brands/preview", { method: "POST", body: { config: body.config, assets: body.assets } }).then(function (p) {
        if (own !== serial) { return; }
        if (!overridesChecked) {
          // 保存の形は算出値も持つので、背景・カード・表の背景は「算出し直した値と違う＝人が決めた」とみなす
          overridesChecked = true;
          var restored = false;
          ["base", "surface"].forEach(function (key) {
            if (colors[key] && colors[key].toUpperCase() !== String(p.palette.derived[key]).toUpperCase()) { overrides[key] = colors[key]; restored = true; }
          });
          if (Object.keys(overrides).length) { otherColors.details.open = true; }
          if (restored) { serial++; verifiedSerial = -1; return preview(); }
        }
        SS.clear(errors);
        carousel.update(p);
        corner.learn(p);
        otherColors.update(p.slide);
        slideOk = !!(p.slide && p.slide.roles && !(p.slide.problems || []).length);
        SS.clear(logoWarnings);
        (p.warnings || []).forEach(function (warning) { logoWarnings.appendChild(text("p", warning.message, "notice notice--info small")); });
        verifiedSerial = own; refreshSave();
        if (readonly) { setStatus(""); } else if (!slideOk) { setStatus("読みにくい色の組み合わせがあります", "error", "色を確認"); }
        else if (dirty) { setStatus("保存していない変更があります", "dirty", "未保存"); } else { setStatus(""); }
      }).catch(function (error) { save.disabled = true; setStatus("見本を更新できませんでした", "error", "更新できません"); fail(error); });
    }
    async function saveNow() {
      if (readonly || !dirty || verifiedSerial !== serial) { return; }
      var body; try { body = requestBody(); } catch (error) { fail(error); return; }
      if (!body.name) { fail(new SS.ApiError("名前を入力してください。", "name_required")); return; }
      if (body.config.logo.variant !== "none" && !logo.has()) { fail(new SS.ApiError("ロゴの画像を選んでください。使わない場合はチェックを外してください。", "logo_required")); return; }
      save.disabled = true; setStatus("保存しています…", "", "保存中…");
      try {
        var result = await SS.api("/api/settings/brands/" + item.id, { method: "POST", body: Object.assign(body, { expected_revision_id: revisionId }) });
        if (result.verified !== true) { throw new SS.ApiError("保存結果を確認できませんでした。一覧を読み直してください。", "save_unverified"); }
        revisionId = result.item.current_revision_id;
        data.name = result.item.name; name.value = result.item.name;
        crumbs.querySelector("[aria-current]").textContent = result.item.name;
        dirty = false; SS.settingsLeave = null;
        data.assets = clone(result.item.assets || data.assets);
        SS.clear(errors); setStatus("");
        saveState.textContent = "保存済み";
        SS.toast("「" + result.item.name + "」を保存しました。このブランドのスライドを次に描くときから効きます。");
      } catch (error) { setStatus("保存できませんでした", "error", "保存できません"); fail(error); refreshSave(); }
    }

    // パンくず（設定 › ブランド › 名前）は帯の上。名前の欄がこの画面の見出し（H1）で、帯と一緒に上に貼り付く
    var crumbs = breadcrumb([{ label: "設定", href: "/settings" }, { label: "ブランド", href: "/settings/brands" }, { label: data.name }]);
    // プレビューのボタン（狭い画面だけ出す。PC はプレビューを右に常に出す）。押すと全画面の板で見本を開く
    var previewButton = h("button", { type: "button", class: "btn brand-editor__preview-button", "aria-haspopup": "dialog" },
      SS.icon("eye"), text("span", "プレビュー", "brand-editor__preview-label"));
    var bar = h("div", { class: "brand-editor__bar" },
      h("h1", { class: "title-editor" }, name), readonly ? h("span", { class: "chip", text: "付属" }) : null,
      h("div", { class: "settings-actions brand-editor__actions" }, status, saveState, previewButton, readonly ? null : save));
    // 左（狭い画面では全部）: 設定の欄。色 → ロゴ → フッター → ウォーターマーク → 詳細設定 → 削除
    var rail = h("div", { class: "brand-editor__rail" }, errors);
    if (readonly) { rail.appendChild(text("p", item.capabilities.edit.reason, "notice")); }
    rail.appendChild(colorFieldset);
    rail.appendChild(form);
    // デザインテンプレート（一覧・追加・編集は別の画面。ブランドの保存とは別々に保存する）
    if (SS.designTemplates) { rail.appendChild(SS.designTemplates.section(item)); }
    save.disabled = true;
    if (!readonly) {
      var danger = h("section", { class: "editor-section danger-zone" }, text("h2", "ブランドの削除", "editor-section__title"));
      danger.appendChild(button("このブランドを削除", function () {
        if (!SS.confirm("「" + data.name + "」を削除します。作成済みのスライドは残ります。よろしいですか？")) { return; }
        SS.api("/api/settings/brands/" + item.id + "/delete", { method: "POST", body: { expected_revision_id: revisionId } }).then(function (result) {
          if (!result.deleted) { throw new SS.ApiError("削除を確認できませんでした。", "delete_unverified"); }
          dirty = false; SS.settingsLeave = null; SS.toast("ブランドを削除しました。", { sticky: true }); SS.navigate("/settings/brands");
        }).catch(fail);
      }, "btn--danger btn--small"));
      rail.appendChild(danger);
    }
    // 右（PC）: プレビュー。色だけでなく、ロゴ・フッタ・ページ番号・透かしも入る。狭い画面では隠し、帯のボタンで開く
    var previewHelp = ["このブランドでプレゼンを描いたときの見本です。色・ロゴ・フッタ・ページ番号・透かしが入ります。",
      "色はデジタル庁デザインシステム（DADS）の決まりで、読める濃さにそろえます。",
      "見本は、左右にスワイプするか、押して送れます（左側を押すと前、右側を押すと次）。",
      "ピッチ（画像で描く資料）では、この色を絵の指示として渡します。絵の中で同じ色になるとは限りません。"];
    var carouselHome = h("div", { class: "brand-editor__carousel" }, carousel.node);
    var previewColumn = h("div", { class: "brand-editor__preview" },
      h("div", { class: "brand-editor__preview-head" }, titleRow(text("h2", "プレビュー", "brand-editor__preview-title"), "プレビュー", previewHelp)),
      carouselHome);
    // 全画面の板（狭い画面）。見本（カルーセル）はこの板へ移し、閉じたら元の場所へ戻す（見本は 1 つだけ持つ）
    function openPreview() {
      // 開いたら閉じるボタンに焦点を置く（先頭の「？」に置くと、説明を開くつもりの無い人にも枠が出る）
      var close = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "閉じる", autofocus: true }, SS.icon("close"));
      var dlg = h("dialog", { class: "preview-sheet", "aria-labelledby": "preview-sheet-title" },
        h("div", { class: "preview-sheet__head" }, titleRow(h("h2", { id: "preview-sheet-title", text: "プレビュー" }), "プレビュー", previewHelp), close));
      dlg.appendChild(carousel.node);
      function done() { if (dlg.open) { dlg.close(); } }
      function onPop() { done(); }
      close.addEventListener("click", done);
      window.addEventListener("popstate", onPop);
      dlg.addEventListener("close", function () {
        window.removeEventListener("popstate", onPop);
        carouselHome.appendChild(carousel.node);
        dlg.remove();
        if (previewButton.isConnected) { previewButton.focus(); }
      });
      document.body.appendChild(dlg);
      dlg.showModal();
    }
    previewButton.addEventListener("click", openPreview);
    var page = h("div", { class: "settings-page settings-page--wide brand-editor" }, crumbs, bar,
      h("div", { class: "brand-editor__body" }, rail, previewColumn));
    // ⌘S / Ctrl+S で保存する（この画面にいる間だけ）
    function onKey(event) {
      if (!page.isConnected) { document.removeEventListener("keydown", onKey); return; }
      if ((event.metaKey || event.ctrlKey) && !event.altKey && (event.key === "s" || event.key === "S")) { event.preventDefault(); saveNow(); }
    }
    document.addEventListener("keydown", onKey);
    SS.clear(root).appendChild(page);
    preview();
  }

  // ---- 設定のトップ（/settings）と、各設定の画面の見出し ----
  // 設定の中の行き先。トップはこの並びでリンクの一覧を出す（タブはやめた: 狭い画面で崩れるため）
  var SETTINGS_SECTIONS = [
    { section: "brands", href: "/settings/brands", label: "ブランド", title: "ブランド",
      lead: "スライドに載せるロゴと配色を管理します。" },
    { section: "server", href: "/settings/server", label: "アクセス", title: "アクセス",
      lead: "Quick Slide を、このパソコンだけで使うか、ほかのパソコンやスマートフォンからも開けるようにするかを決めます。" },
    { section: "samples", href: "/settings/samples", label: "見本", title: "見本",
      lead: "プレゼン・ピッチ・モーションの見本のスライドです。消したあとでも、ここから入れ直せます。" },
    { section: "updates", href: "/settings/updates", label: "更新", title: "更新",
      lead: "使っている Quick Slide のバージョンと、新しいバージョンのお知らせです。" }
  ];

  // 各設定の画面の見出し: パンくず（設定 › いまの画面）→ H1（いまの画面の名前）と説明
  function settingsHead(section) {
    var current = SETTINGS_SECTIONS.filter(function (t) { return t.section === section; })[0] || SETTINGS_SECTIONS[0];
    return [
      breadcrumb([{ label: "設定", href: "/settings" }, { label: current.label }]),
      h("div", { class: "settings-section-head" }, text("h1", current.title), text("p", current.lead, "muted"))
    ];
  }

  // 設定のトップ: H1「設定」と、各設定へのリンクの一覧（行全体がリンク。名前と説明と「›」）
  function settingsIndex(root) {
    var list = h("ul", { class: "settings-index" });
    SETTINGS_SECTIONS.forEach(function (t) {
      list.appendChild(h("li", null, h("a", { class: "settings-index__link", href: t.href, "data-nav": true },
        h("span", { class: "settings-index__text" }, text("span", t.label, "settings-index__name"), text("span", t.lead, "settings-index__lead")),
        SS.icon("chevron_right", "settings-index__chevron"))));
    });
    SS.clear(root).appendChild(h("div", { class: "settings-page" }, text("h1", "設定"), list));
  }

  // パンくず（DADS: ヘッダーとページ見出しの間に現在地を階層で示す）。最後の段（いまのページ）はリンクにしない
  function breadcrumb(items) {
    var list = h("ol", { class: "breadcrumb__list" });
    items.forEach(function (item, index) {
      var last = index === items.length - 1;
      list.appendChild(h("li", { class: "breadcrumb__item" },
        last ? h("span", { "aria-current": "page", text: item.label }) : h("a", { href: item.href, "data-nav": true, text: item.label })));
    });
    return h("nav", { class: "breadcrumb", "aria-label": "現在地" }, list);
  }

  function brandRow(entry, reload) {
    var colors = entry.config.colors || {};
    var swatches = h("span", { class: "setting-row__swatches", "aria-hidden": "true" });
    ["primary", "accent"].forEach(function (key) {
      if (!colors[key]) { return; }
      var chip = h("span", { class: "setting-row__swatch" });
      chip.style.background = colors[key];
      swatches.appendChild(chip);
    });
    function saveAs(name, existing) {
      var body = { name: name, config: clone(entry.config), assets: clone(entry.assets || []) };
      if (existing) { body.expected_revision_id = entry.current_revision_id; }
      return SS.api("/api/settings/brands" + (existing ? "/" + entry.id : ""), { method: "POST", body: body }).then(function (result) {
        if (result.verified !== true) { throw new SS.ApiError("保存結果を確認できませんでした。一覧を読み直してください。", "save_unverified"); }
        return result.item;
      });
    }
    var menuButton = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "操作", "aria-haspopup": "menu" }, SS.icon("more_vert"));
    menuButton.addEventListener("click", function () {
      var edit = entry.capabilities.edit, remove = entry.capabilities.delete;
      SS.menu(menuButton, [
        { label: "名前を変更", disabled: !edit.available, reason: edit.reason, onSelect: function () {
          SS.textModal({ title: "ブランドの名前を変更", label: "名前", value: entry.name, submitLabel: "変更する",
            onSubmit: function (value) { return saveAs(value, true).then(function (saved) { SS.toast("「" + saved.name + "」に変更しました。"); reload(); }); } });
        } },
        { label: "エクスポート", disabled: entry.kind === "preset", reason: "付属のブランドはエクスポートできません。複製してからエクスポートしてください。",
          onSelect: function () { exportBrand(entry); } },
        { label: "複製", onSelect: function () {
          SS.textModal({ title: "ブランドを複製", label: "新しい名前", value: entry.name + " のコピー", submitLabel: "複製する",
            onSubmit: function (value) { return saveAs(value, false).then(function (saved) { SS.toast("「" + saved.name + "」を作りました。"); SS.navigate("/settings/brands/" + saved.id); }); } });
        } },
        "-",
        { label: "削除", danger: true, disabled: !remove.available, reason: remove.reason, onSelect: function () {
          if (!SS.confirm("「" + entry.name + "」を削除します。作成済みのスライドは残ります。よろしいですか？")) { return; }
          SS.api("/api/settings/brands/" + entry.id + "/delete", { method: "POST", body: { expected_revision_id: entry.current_revision_id } }).then(function (result) {
            if (!result.deleted) { throw new SS.ApiError("削除を確認できませんでした。", "delete_unverified"); }
            SS.toast("ブランドを削除しました。", { sticky: true }); reload();
          }).catch(SS.toastError);
        } }
      ]);
    });
    return h("div", { class: "setting-row menu-anchor" },
      h("a", { class: "setting-row__name", href: "/settings/brands/" + entry.id, "data-nav": true, text: entry.name }),
      entry.kind === "preset" ? h("span", { class: "chip", text: "付属" }) : null,
      swatches,
      menuButton);
  }

  // ---- ブランドのエクスポート・インポート（ZIP。形はサーバの brand_package.py）----
  // エクスポート: サーバが作った ZIP を受け取り、サーバが決めたファイル名（qs-ブランド-<名前>.zip）で保存させる
  function exportBrand(entry) {
    var filename = "qs-brand.zip";
    fetch("/api/settings/brands/" + entry.id + "/export").then(function (r) {
      if (!r.ok) { return r.json().then(function (j) { throw new SS.ApiError((j && j.error && j.error.message) || "エクスポートできませんでした。", "export_failed", r.status); }, function () { throw new SS.ApiError("エクスポートできませんでした。", "export_failed", r.status); }); }
      var m = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get("Content-Disposition") || "");
      if (m) { try { filename = decodeURIComponent(m[1]); } catch (e) { /* 読めなければ既定の名前 */ } }
      return r.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = h("a", { href: url, download: filename });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      SS.toast("「" + entry.name + "」をエクスポートしました。ZIP をそのまま渡せば、相手の Quick Slide でインポートできます。");
    }).catch(SS.toastError);
  }

  // エクスポートした日時（サーバは UTC で持つ）を、この機械の時刻で「2026/10/12 19:13」の形にする。読めなければ出さない
  function exportedAt(value) {
    var d = value ? new Date(value) : null;
    if (!d || isNaN(d.getTime())) { return ""; }
    return d.toLocaleString("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  // インポート: まず中身を確かめ（何も保存しない）、同じ ID のブランドがあれば「上書き／別のブランドとして追加／やめる」を選ばせる
  function importBrand(file, opener) {
    SS.postFile("/api/settings/brands/import/check", file, "ZIP を送信できませんでした。接続を確認してやり直してください。").then(function (check) {
      var pkg = check.package, existing = check.existing, choices = check.choices || [];
      var error = h("p", { class: "notice notice--error small", role: "alert", hidden: true });
      var swatches = h("span", { class: "setting-row__swatches", "aria-hidden": "true" });
      [pkg.colors.primary, pkg.colors.accent].forEach(function (c) { var chip = h("span", { class: "setting-row__swatch" }); chip.style.background = c; swatches.appendChild(chip); });
      var facts = h("ul", { class: "import-dialog__facts small" },
        text("li", "ロゴ: " + (pkg.logos.length ? "あり（" + pkg.logos.length + " 枚）" : "なし")),
        text("li", "フッター: " + (pkg.footer || "なし")),
        text("li", "ウォーターマーク: " + (pkg.watermark || "なし")),
        exportedAt(pkg.exported_at) ? text("li", "エクスポートした日時: " + exportedAt(pkg.exported_at)) : null);
      var lead;
      if (existing && choices.indexOf("replace") >= 0) {
        lead = h("div", { class: "notice import-dialog__same" }, text("p", "同じブランド「" + existing.name + "」がすでにあります（ID が同じです）。"),
          text("p", "上書きすると、このブランドの新しい版になります。前の版も履歴に残ります。", "small"));
      } else if (existing) {
        lead = text("p", "付属のブランドと ID が同じなので、別のブランドとして追加します。", "notice small");
      } else {
        lead = null;
      }
      var close = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "閉じる" }, SS.icon("close"));
      var actions = h("div", { class: "modal-actions import-dialog__actions" });
      var dlg = h("dialog", { class: "kind-help import-dialog", "aria-labelledby": "import-dialog-title" },
        h("div", { class: "kind-help__head" }, h("h2", { id: "import-dialog-title", text: "ブランドをインポート" }), close),
        h("div", { class: "import-dialog__brand" }, swatches, text("p", pkg.name, "import-dialog__name")), facts, lead, error, actions);
      var busy = false;
      function done() { if (dlg.open) { dlg.close(); } }
      function run(mode, label) {
        if (busy) { return; }
        busy = true; error.hidden = true;
        var url = "/api/settings/brands/import?mode=" + mode + (mode === "replace" ? "&expected_revision_id=" + encodeURIComponent(existing.current_revision_id) : "");
        SS.postFile(url, file, "ZIP を送信できませんでした。接続を確認してやり直してください。").then(function (result) {
          if (result.verified !== true) { throw new SS.ApiError("インポートした結果を確認できませんでした。一覧を読み直してください。", "import_unverified"); }
          done();
          SS.toast("「" + result.item.name + "」を" + label + "。");
          SS.navigate("/settings/brands/" + result.item.id);
        }).catch(function (e) { busy = false; error.textContent = e && e.message ? e.message : "インポートできませんでした。"; error.hidden = false; });
      }
      function choice(label, cls, mode, toastLabel) { actions.appendChild(button(label, function () { run(mode, toastLabel); }, cls)); }
      if (choices.indexOf("replace") >= 0) {
        choice("上書きする", "btn--primary", "replace", "上書きしました");
        choice("別のブランドとして追加", "", "add", "別のブランドとして追加しました");
      } else {
        choice(existing ? "追加する" : "インポート", "btn--primary", "add", existing ? "追加しました" : "インポートしました");
      }
      actions.appendChild(button("やめる", done, "btn--ghost"));
      close.addEventListener("click", done);
      dlg.addEventListener("click", function (e) { if (e.target === dlg) { done(); } });
      dlg.addEventListener("close", function () { dlg.remove(); if (opener && opener.isConnected) { opener.focus(); } });
      document.body.appendChild(dlg);
      dlg.showModal();
    }).catch(SS.toastError);
  }

  function brandsScreen(root, catalog) {
    function reload() { SS.navigate("/settings/brands", true); }
    var rows = h("div", { class: "setting-rows" });
    catalog.brands.forEach(function (entry) { rows.appendChild(brandRow(entry, reload)); });
    if (!catalog.brands.length) { rows.appendChild(text("p", "登録されたブランドがありません。", "reason")); }
    var create = button("新しいブランド", function () {
      SS.textModal({ title: "新しいブランド", label: "名前", value: "", submitLabel: "作る",
        onSubmit: function (value) {
          return SS.api("/api/settings/brands", { method: "POST", body: { name: value, config: clone(catalog.brand_default), assets: [] } }).then(function (result) {
            if (result.verified !== true) { throw new SS.ApiError("保存結果を確認できませんでした。一覧を読み直してください。", "save_unverified"); }
            SS.navigate("/settings/brands/" + result.item.id);
          });
        } });
    }, "btn--primary");
    var picker = h("input", { type: "file", accept: ".zip,application/zip", hidden: true });
    picker.addEventListener("change", function () { var file = picker.files[0]; picker.value = ""; if (file) { importBrand(file, importButton); } });
    var importButton = button("", function () { picker.click(); });
    importButton.appendChild(SS.icon("upload")); importButton.appendChild(text("span", "インポート"));
    // 書式（フッター・ページ番号・ウォーターマーク）はブランドの編集画面で設定する。一覧に「書式を登録」は置かない
    SS.clear(root).appendChild(h("div", { class: "settings-page" }, settingsHead("brands"),
      h("div", { class: "settings-toolbar" }, create, importButton, picker),
      rows));
  }

  // ---- アクセス（どこから開けるか。/settings/server。ADR 0049）----
  function serverScreen(root, catalog) {
    var state = catalog.server;
    var shut = state.bind_scope === "loopback";   // ループバックかはサーバがアドレスの意味で決めた値を使う
    var errors = h("div", { class: "setting-errors", role: "alert" });
    var dirty = false;

    var choice = h("div", { class: "setting-field" },
      text("span", "開ける範囲"),
      h("div", { class: "setting-choices" },
        radio("here", "このパソコンだけ（おすすめ）", "このパソコンのブラウザからだけ開けます。いちばん安全です。", shut),
        radio("out", "ほかの端末からも開けるようにする", "指定したネットワーク（社内 LAN や VPN など）につながっているパソコンやスマートフォンから開けます。", !shut)));

    var addr = field("公開する IP アドレス", shut ? "" : state.host,
      { placeholder: "100.100.1.23", spellcheck: "false", autocapitalize: "off" });
    var names = field("ホスト名（任意）", state.host_names.join("\n"),
      { multiline: true, rows: "3", placeholder: "my-mac\nmy-mac.example.net", spellcheck: "false", autocapitalize: "off" });
    var port = field("ポート番号", String(state.port || ""), { inputmode: "numeric", maxlength: 5, spellcheck: "false" });
    port.el.addEventListener("input", function () { dirty = true; SS.settingsLeave = { dirty: function () { return dirty; } }; });
    var away = h("div", { class: "setting-away" }, addr.node,
      text("p", "このパソコンに割り当てられている IP アドレスを 1 つ入力します。どのネットワークに公開されるかは、このアドレスで決まります。すべてのネットワークを意味する 0.0.0.0 は使えません。", "small muted"),
      names.node,
      text("p", "IP アドレスの代わりに名前で開きたいときに書きます。1 行に 1 つずつ。空欄なら IP アドレスで開きます。", "small muted"),
      h("div", { class: "notice notice--warn" },
        text("p", "Quick Slide にはパスワードがありません。公開したネットワークにつながっている人は誰でも、スライドの閲覧・削除や、画像生成の利用枠の消費ができてしまいます。自分の端末だけがつながっているネットワークにだけ公開してください。")));

    function radio(value, label, note, checked) {
      var input = h("input", { type: "radio", name: "server-openness", value: value });
      input.checked = !!checked;
      input.addEventListener("change", sync);
      return h("label", { class: "setting-choice" }, input,
        h("span", null, text("strong", label), text("span", note, "small muted")));
    }
    function picked() {
      var on = choice.querySelector("input[value=\"out\"]");
      return on && on.checked ? "out" : "here";
    }
    function sync() {
      away.hidden = picked() !== "out";
      dirty = true;
      SS.settingsLeave = { dirty: function () { return dirty; } };
    }

    var save = button("保存する", function () {
      var open = picked() === "out";
      var body = {
        host: open ? addr.el.value.trim() : "127.0.0.1",
        host_names: open ? names.el.value.split("\n").map(function (n) { return n.trim(); }).filter(Boolean) : []
      };
      var portText = port.el.value.trim();
      if (portText && String(state.port) !== portText) {
        if (!/^[0-9]{1,5}$/.test(portText)) { SS.clear(errors).appendChild(text("p", "ポート番号は数字で入力してください。", "notice notice--error")); return; }
        body.port = parseInt(portText, 10);
      }
      SS.clear(errors);
      save.disabled = true;
      SS.api("/api/settings/server", { method: "POST", body: body }).then(function (result) {
        if (result.verified !== true) { throw new SS.ApiError("保存できたか確認できませんでした。画面を読み込み直してください。", "save_unverified"); }
        dirty = false; SS.settingsLeave = null;
        SS.toast("保存しました。「再起動」を押すと反映されます。");
        SS.navigate("/settings/server", true);
      }).catch(function (error) {
        save.disabled = false;
        SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error"));
      });
    }, "btn--primary");

    var now = shut
      ? "現在は、このパソコンからだけ開けます。"
      : "現在は " + state.host + " で公開しています。ほかの端末からも開けます。";
    var live = state.restart_required || state.settings_error ? restartBanner(state) : null;

    away.hidden = shut;
    SS.clear(root).appendChild(h("div", { class: "settings-page" }, settingsHead("server"),
      h("div", { class: "setting-panel" },
        text("p", now, "muted"), live, choice, away, port.node,
        text("p", "通常は 7801 のままで構いません。変えると、開くときの URL の番号も変わります。", "small muted"), errors,
        h("div", { class: "setting-actions" }, save))));
  }

  // ---- 見本（同梱の見本のスライド。最初の起動で入る。消したあとでもここから入れ直せる。docs/adr/0007-bundled-samples.md） ----
  var SAMPLE_MODES = { presentation: "プレゼン", pitch: "ピッチ", motion: "モーション" };
  var SAMPLE_STATES = {
    present: "入っています",
    outdated: "古い版が入っています。「見本を入れ直す」で新しい版にできます。",
    missing: "入っていません（消したか、ごみ箱にあります）。"
  };
  function sampleRow(item) {
    var links = h("span", { class: "sample-row__decks" });
    item.decks.forEach(function (deck) {
      var label = deck.title === item.title ? "開く" : "開く（" + deck.title + "）";
      links.appendChild(h("a", { href: "/slides/" + deck.id, "data-nav": true, text: label + (deck.archived ? "（アーカイブ中）" : "") }));
    });
    return h("div", { class: "setting-row sample-row", "data-sample": item.id },
      h("span", { class: "setting-row__name" }, text("span", item.title),
        text("span", SAMPLE_MODES[item.mode] || item.mode, "sample-row__mode")),
      h("span", { class: "sample-row__state" }, text("span", SAMPLE_STATES[item.state] || "", "small muted sample-row__state-text"), links));
  }
  function samplesScreen(root, guard) {
    return SS.api("/api/samples").then(function (view) {
      if (!guard()) { return; }
      var rows = h("div", { class: "setting-rows" });
      view.items.forEach(function (item) { rows.appendChild(sampleRow(item)); });
      var errors = h("div", { role: "alert" });
      var restore = button("見本を入れ直す", function () {
        restore.disabled = true;
        SS.clear(errors);
        SS.api("/api/samples/restore", { method: "POST", body: {} }).then(function (result) {
          SS.toast(result.message);
          SS.navigate("/settings/samples", true);
        }).catch(function (error) {
          restore.disabled = false;
          SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error"));
        });
      }, "btn--primary sample-restore");
      restore.disabled = !view.available;
      var body = view.available ? [rows,
        text("p", "入れ直すと、消した見本を新しいスライドとして入れます。入っている見本はそのまま残ります。", "small muted"),
        errors, h("div", { class: "setting-actions" }, restore)]
        : [text("p", view.reason, "reason")];
      SS.clear(root).appendChild(h("div", { class: "settings-page" }, settingsHead("samples"),
        h("div", { class: "setting-panel" }, body)));
    });
  }

  // ---- 更新（/settings/updates。docs/adr/0020-release-and-update-notice.md）----
  // 版の比べ方も GitHub への問い合わせもサーバ（`/api/update`）。画面は答えを並べるだけ
  var UPDATE_ERRORS = {
    unreachable: "GitHub につながらず、新しいバージョンを確かめられませんでした。ネットワークを確かめて、あとでもう一度お試しください。",
    no_release: "公開されたリリースがまだありません。",
    bad_response: "GitHub の答えを読み取れず、新しいバージョンを確かめられませんでした。あとでもう一度お試しください。"
  };
  var UPDATE_OFF = {
    env: "環境変数 QUICK_SLIDE_NO_UPDATE_CHECK で、新しいバージョンを確かめるのを止めています。",
    settings: "新しいバージョンを確かめるのを止めています。",
    server: "このサーバは、新しいバージョンを確かめない設定で起動しています。"
  };
  function commandBlock(label, lines, after) {
    var commands = lines.join("\n");
    var code = text("code", commands);
    var copy = h("button", { type: "button", class: "btn btn--small btn--ghost update-howto__copy", "aria-label": label + " のコマンドをコピー" },
      SS.icon("content_copy"), h("span", { text: "コピー" }));
    copy.addEventListener("click", function () {
      SS.copyText(commands).then(function (ok) {
        if (ok) { SS.toast(label + " のコマンドをコピーしました。"); return; }
        var range = document.createRange();   // 写せなかったら文字を選んでおき、自分で写してもらう
        range.selectNodeContents(code);
        var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        SS.toast("コピーできませんでした。選んだ文字を ⌘C（Windows は Ctrl+C）でコピーしてください。", { kind: "error" });
      });
    });
    return h("div", { class: "update-howto__item" },
      h("div", { class: "update-howto__head" }, text("h3", label), copy),
      h("pre", { class: "update-howto__code" }, code),
      after ? text("p", after, "small muted") : null);
  }
  function updatesScreen(root, guard, refresh) {
    return SS.api("/api/update" + (refresh ? "?refresh=1" : "")).then(function (st) {
      if (!guard()) { return; }
      var errors = h("div", { role: "alert" });
      var said;
      if (!st.enabled) { said = UPDATE_OFF[st.disabled_by] || UPDATE_OFF.settings; }
      else if (st.update_available) { said = "新しいバージョン " + st.latest_version + " が公開されています。"; }
      else if (st.error && !st.latest_version) { said = UPDATE_ERRORS[st.error] || UPDATE_ERRORS.unreachable; }
      else if (st.latest_version) { said = "最新のバージョンを使っています。"; }
      else { said = "まだ確かめていません。"; }
      var when = st.enabled && st.checked_at
        ? "最後に確かめた日時: " + SS.fmtDate(st.checked_at) + (st.stale ? "（いまは確かめられなかったため、前の結果です）" : "") : "";

      var check = button("今すぐ確かめる", function () {
        check.disabled = true;
        updatesScreen(root, guard, true).catch(function (error) {
          check.disabled = false;
          SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error"));
        });
      }, "update-check");
      check.disabled = !st.enabled;

      var toggle = h("input", { type: "checkbox", id: "update-check-toggle" });
      toggle.checked = st.disabled_by !== "settings";
      toggle.disabled = st.disabled_by === "env" || st.disabled_by === "server";
      toggle.addEventListener("change", function () {
        toggle.disabled = true;
        SS.api("/api/settings/updates", { method: "POST", body: { check: toggle.checked } }).then(function () {
          SS.toast(toggle.checked ? "新しいバージョンを確かめるようにしました。" : "新しいバージョンを確かめるのを止めました。");
          SS.navigate("/settings/updates", true);
        }).catch(function (error) {
          toggle.disabled = false;
          toggle.checked = !toggle.checked;
          SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error"));
        });
      });
      var switchField = h("div", { class: "setting-field setting-field--optional" },
        h("label", { for: "update-check-toggle" }, toggle, " 新しいバージョンが出たら知らせる"),
        text("p", "オンのあいだ、このサーバは 6 時間に 1 回まで、GitHub（api.github.com）に最新のリリースを問い合わせます。送るのは問い合わせと、いま使っているバージョンの番号だけです。スライドの中身や設定は送りません。", "small muted"));

      var panel = h("div", { class: "setting-panel" },
        h("p", null, text("strong", "現在のバージョン：" + st.current_version)),
        h("div", { class: st.update_available ? "notice notice--info" : "notice", role: "status" }, text("p", said),
          // 変更点はアプリの中に写さず、GitHub のリリースの頁を見てもらう（外のサイトへはこの 1 本だけ。同じウィンドウで開く）
          st.latest_version && st.release_url ? h("p", null, h("a", { href: st.release_url, rel: "noopener noreferrer",
            class: "update-release__link", text: "Quick Slide " + st.latest_version + " の変更点を見る（GitHub）" })) : null,
          when ? text("p", when, "small muted") : null),
        h("div", { class: "setting-actions" }, check), errors, switchField);

      if (st.update_available) {
        // やり方は 3 つ（頼む・コマンドを会話に貼る・ターミナルで実行する）。どれでもよいことが一目で分かるように並べる
        var ways = h("ol", { class: "update-howto__ways" },
          h("li", null, text("strong", "会話で頼む（おすすめ）"),
            text("p", "Claude Code か Codex に「Quick Slide を更新して」と伝えます。")),
          h("li", null, text("strong", "コマンドを会話に貼る"),
            text("p", "下のコマンドを、Claude Code か Codex の会話にそのまま貼り付けます。")),
          h("li", null, text("strong", "ターミナルで実行する"),
            text("p", "下のコマンドを自分で実行します。")));
        panel.appendChild(h("section", { class: "update-howto", "aria-label": "更新のしかた" }, text("h2", "更新のしかた"),
          text("p", "次のどれかで更新できます。"), ways,
          commandBlock("Claude Code", ["claude plugin marketplace update quick-slide", "claude plugin update quick-slide@quick-slide"],
            "更新したら /reload-plugins を実行するか、新しい会話を始めます。"),
          commandBlock("Codex", ["codex plugin marketplace upgrade quick-slide", "codex plugin add quick-slide@quick-slide"],
            "更新したら Codex を起動し直します。"),
          text("p", "動いている Quick Slide のサーバは、次に Quick Slide を使ったときに新しいバージョンで起動し直します（処理中の作業があれば、終わってから）。", "small muted")));
      }
      SS.clear(root).appendChild(h("div", { class: "settings-page" }, settingsHead("updates"), panel));
    });
  }

  // 「再起動が必要」の帯（アクセスの画面）。処理中の作業があれば「終わるのを待って」と「今すぐ」を出す。
  // 押したあとの進み・つなぎ直し・新しい URL への移動は core.js の SS.restartServer
  function restartBanner(state) {
    var box = h("div", { class: "notice notice--warn restart-banner", role: "status" });
    if (state.settings_error) {
      box.appendChild(text("p", "設定ファイルを読み込めません（" + state.settings_error + "）。ファイルを直すまで再起動できません。"));
      return box;
    }
    box.appendChild(text("p", "保存した設定はまだ反映されていません。現在は " + state.running_url
      + " で動いています。再起動すると " + state.expected_url + " で開けるようになります。"));
    var progress = text("p", "", "small restart-banner__progress");
    var buttons = h("div", { class: "setting-actions restart-banner__actions" });
    function go(mode) {
      buttons.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      SS.restartServer(mode, function (message) { progress.textContent = message; }).catch(function (error) {
        buttons.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
        progress.textContent = "";
        SS.toastError(error);
      });
    }
    if (!state.restartable) {
      box.appendChild(text("p", "この画面からは再起動できません。コマンド（start）で起動し直してください。", "small"));
      return box;
    }
    if (state.running_jobs > 0) {
      buttons.appendChild(button("処理中の " + state.running_jobs + " 件が終わってから再起動", function () { go("wait"); }, "btn--primary restart-wait"));
      buttons.appendChild(button("今すぐ再起動", function () {
        if (!SS.confirm("処理中の作業を止めて再起動します。画像を描いている途中のものは、再起動のあとに続きから描きます。台本を書いている途中のものは失敗になります。よろしいですか？")) { return; }
        go("now");
      }, "restart-now"));
    } else {
      buttons.appendChild(button("再起動", function () { go("now"); }, "btn--primary restart-now"));
    }
    box.appendChild(buttons);
    box.appendChild(progress);
    return box;
  }
  SS.restartBanner = restartBanner;

  SS.views.settings = function (root, route, guard) {
    SS.clear(root).appendChild(text("p", "設定を読み込んでいます…"));
    if (route.section === "index") { settingsIndex(root); return Promise.resolve(); }
    if (route.section === "samples") { return samplesScreen(root, guard); }
    if (route.section === "updates") { return updatesScreen(root, guard); }
    return SS.api("/api/settings").then(function (catalog) {
      if (!guard()) { return; }
      if (route.section === "server") { return serverScreen(root, catalog); }
      if (route.section === "template") { return SS.designTemplates.screen(root, catalog, route, guard); }
      if (route.section === "brand") {
        var item = catalog.brands.filter(function (entry) { return entry.id === route.brandId; })[0] || null;
        if (!item) {
          SS.clear(root).appendChild(h("div", { class: "settings-page" },
            h("div", { class: "notice notice--error", role: "alert" }, text("p", "このブランドは見つかりません。一覧から選び直してください。")),
            h("p", null, h("a", { href: "/settings/brands", "data-nav": true, text: "← ブランドの一覧" }))));
          return;
        }
        return brandEditor(root, catalog, item);
      }
      return brandsScreen(root, catalog);
    });
  };
})();
