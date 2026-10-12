// Settings are a local, no-build extension of the single-window application.
// 一覧（/settings）と編集（/settings/brands/<id>）は別画面。配色の算出はサーバが正本であり、
// ここでは preview 応答の数値をそのまま塗るだけで、色も座標も計算しない。
(function () {
  "use strict";
  var SS = window.SS, h = SS.h;
  var clone = function (value) { return JSON.parse(JSON.stringify(value)); };
  var styleKeys = ["emphasis_prompt", "tone", "typeface", "density", "visual_ratio", "allowed_layouts", "style_recipe", "margins", "canvas",
    "engine_style", "image_look", "style_note"];
  var LOGO_ROLE = { normal: "logo_normal", white: "logo_white", transparent: "logo_transparent", symbol: "logo_symbol" };
  var nextInput = 0;
  SS.settingsLeave = null;

  var EXAMPLE_NOTE = "あくまで一例です。実際の生成画像ではありません。";

  // 図表の系列色。順序の正本はこの配列 1 か所だけ: 基本色 → 強調色 → 主役色（濃）。
  var CHART_SERIES = [
    { label: "基本色", height: "48%", color: function (palette) { return palette.input.primary; } },
    { label: "強調色", height: "70%", color: function (palette) { return palette.input.accent; } },
    { label: "主役色（濃）", height: "92%", color: function (palette) { return palette.derived.primary_dark; } }
  ];

  function text(tag, value, cls) { return h(tag, { text: value, class: cls }); }
  function button(label, fn, cls) { return h("button", { type: "button", class: "btn " + (cls || ""), text: label, onclick: fn }); }
  function safeError(error) { return error instanceof SS.ApiError ? error.message : "処理を確認できませんでした。接続を確認してやり直してください。"; }
  function field(label, value, attrs) {
    var id = "setting-input-" + (++nextInput);
    var control = h(attrs && attrs.multiline ? "textarea" : "input", Object.assign({ id: id, type: "text" }, attrs || {}));
    control.value = value == null ? "" : value;
    return { el: control, node: h("label", { class: "setting-field", for: id }, text("span", label), control) };
  }
  function select(label, value, options) {
    var id = "setting-input-" + (++nextInput);
    var control = h("select", { id: id }, options.map(function (option) { return h("option", { value: option.value, text: option.label }); }));
    control.value = value;
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

  SS.applyBrandTheme = function (node, config) {
    var colors = config.colors || {}, semantic = colors.semantic || {};
    ["primary", "accent"].forEach(function (key) { if (colors[key]) { node.style.setProperty("--color-" + key, colors[key]); } });
    Object.keys(semantic).forEach(function (key) { node.style.setProperty("--color-" + key, semantic[key]); });
    if (Array.isArray(colors.gray) && colors.gray.length) {
      node.style.setProperty("--color-text", colors.gray[0]); node.style.setProperty("--color-text-muted", colors.gray[Math.min(2, colors.gray.length - 1)]);
      node.style.setProperty("--color-border", colors.border || colors.gray[Math.max(0, colors.gray.length - 2)]); node.style.setProperty("--color-surface", colors.surface || colors.gray[colors.gray.length - 1]);
    }
  };

  // ---- 見本（カルーセルの 4 枚）----
  // 座標は preview 応答の値を見本の幅・高さで割るだけ。ここで幾何を作らない。
  function pct(ratio) { return (ratio * 100) + "%"; }
  // 予約枠 [x0, y0, x1, y1] と ロゴ [x, y, w, h] を、見本の幅・高さに対する比へ直すだけ。
  // ここが応答の数値を割る唯一の場所であり、位置を作り出す計算はしない。
  function boxRatio(box, canvas) { return [box[0] / canvas[0], box[1] / canvas[1], (box[2] - box[0]) / canvas[0], (box[3] - box[1]) / canvas[1]]; }
  function rectRatio(rect, canvas) { return [rect[0] / canvas[0], rect[1] / canvas[1], rect[2] / canvas[0], rect[3] / canvas[1]]; }
  function place(node, ratios) { node.style.left = pct(ratios[0]); node.style.top = pct(ratios[1]); node.style.width = pct(ratios[2]); node.style.height = pct(ratios[3]); }

  function paintPalette(node, palette) {
    var d = palette.derived;
    node.style.setProperty("--color-primary", palette.input.primary);
    node.style.setProperty("--color-accent", palette.input.accent);
    node.style.setProperty("--color-border", d.border);
    node.style.setProperty("--color-surface", d.surface);
    node.style.setProperty("--color-text", d.gray[0]);
    node.style.setProperty("--color-text-muted", d.gray[Math.min(2, d.gray.length - 1)]);
    node.style.setProperty("--color-success", d.semantic.success);
    node.style.setProperty("--color-warning", d.semantic.warning);
    node.style.setProperty("--color-error", d.semantic.error);
    node.style.setProperty("--color-info", d.semantic.info);
    node.style.setProperty("--sample-base", d.base);
    node.style.setProperty("--sample-primary-dark", d.primary_dark);
    node.style.setProperty("--sample-primary-light", d.primary_light);
  }

  function sample(kind, label) { return h("div", { class: "sample sample--" + kind, "aria-label": label }); }

  // 下の部品（フッタ・ページ番号・透かし）。位置は preview 応答の chrome（エンジンの既定の寸法の写し）を canvas で割るだけ。
  // フッタは中身のページの左下（ロゴが左下ならロゴの右）、ページ番号は中央下、透かしは中央下の端。
  function footerLine(p) {
    if (!p.canvas || !p.chrome) { return null; }
    var parts = [];
    if (p.chrome.footer && p.copyright && p.copyright.text) {
      var foot = text("p", p.copyright.text, "sample__footer sample__chrome");
      place(foot, rectRatio(p.chrome.footer, p.canvas)); parts.push(foot);
    }
    if (p.chrome.page_number) {
      var no = text("p", "3", "sample__pageno sample__chrome");
      place(no, rectRatio(p.chrome.page_number, p.canvas)); parts.push(no);
    }
    if (p.chrome.watermark && p.format && p.format.watermark) {
      var wm = text("p", p.format.watermark, "sample__watermark sample__chrome");
      place(wm, rectRatio(p.chrome.watermark, p.canvas)); parts.push(wm);
    }
    return parts.length ? parts : null;
  }

  // ロゴは選んだ角に入る（ふさがっていれば描くときにエンジンが別の角へ逃がす。見本は選んだ角の姿）。
  // 座標は preview 応答の logo_xywh を canvas で割った値だけを使う（v2 では予約枠 clean_area はいつも null）。
  function logoOverlay(p, withFrame) {
    if (!p.canvas) { return null; }
    var parts = [];
    if (p.clean_area && withFrame) {
      var reserved = h("div", { class: "sample__reserved", "aria-hidden": "true" });
      place(reserved, boxRatio(p.clean_area, p.canvas));
      parts.push(reserved);
    }
    if (p.image_url && p.logo_xywh) {
      var img = h("img", { class: "sample__logo", src: p.image_url, alt: "登録したロゴ" });
      place(img, rectRatio(p.logo_xywh, p.canvas));
      var lost = text("p", "ロゴ画像を読み込めません。画像を選び直してください。", "sample__lost");
      lost.hidden = true;
      img.onerror = function () { img.hidden = true; lost.hidden = false; };
      parts.push(img, lost);
    }
    return parts.length ? parts : null;
  }

  function coverSample(p) {
    var node = sample("cover", "表紙の見本（16対9）");
    node.appendChild(h("div", { class: "sample__band" },
      text("p", "事業計画", "sample__eyebrow"),
      text("h3", "次の成長に向けた3つの取り組み", "sample__title")));
    node.appendChild(text("p", "2026年度 経営会議 ／ 事業推進部", "sample__lede"));
    return node;
  }

  function bulletsSample(p) {
    var node = sample("bullets", "箇条書きの見本（16対9）");
    node.appendChild(text("p", "現状と打ち手", "sample__eyebrow"));
    node.appendChild(text("h3", "重点施策とその根拠", "sample__heading"));
    node.appendChild(text("p", "国内市場は横ばい、海外は二桁成長。投資先を絞り込む。", "sample__body"));
    node.appendChild(h("hr", { class: "sample__rule" }));
    node.appendChild(h("ul", { class: "sample__list" },
      text("li", "重点施策を先に置き、理由を後に書く"),
      text("li", "数字は本文と同じ大きさで、太さだけ変える"),
      text("li", "区切り線は情報の切れ目にだけ使う")));
    return node;
  }

  function chartSample(p) {
    var palette = p.palette;
    var node = sample("chart", "図表の見本（16対9）");
    node.appendChild(text("h3", "四半期ごとの推移", "sample__heading"));
    var chart = h("div", { class: "sample__chart" });
    var legend = h("div", { class: "sample__legend" });
    CHART_SERIES.forEach(function (series) {
      var color = series.color(palette);
      var bar = h("span", { class: "sample__bar" });
      bar.style.height = series.height;
      bar.style.background = color;
      chart.appendChild(bar);
      var key = h("span", { class: "sample__key" });
      var swatch = h("span", { class: "sample__swatch", "aria-hidden": "true" });
      swatch.style.background = color;
      key.appendChild(swatch);
      key.appendChild(text("span", series.label));
      legend.appendChild(key);
    });
    node.appendChild(chart);
    node.appendChild(legend);
    node.appendChild(h("div", { class: "sample__focus" },
      text("p", "ここに注目", "sample__focus-title"),
      text("p", "第3四半期に前年同期比 118% まで回復。", "sample__focus-body")));
    return node;
  }

  function statusSample(p) {
    var node = sample("status", "状態表示の見本（16対9）");
    node.appendChild(text("h3", "移行作業の進みぐあい", "sample__heading"));
    var grid = h("div", { class: "sample__states" });
    grid.appendChild(h("div", { class: "sample__state sample__state--success" },
      text("p", "成功", "sample__state-name"),
      h("p", { class: "sample__bubble" }, text("span", "保存しました"))));
    grid.appendChild(h("div", { class: "sample__state sample__state--warning" },
      text("p", "注意", "sample__state-name"),
      h("p", { class: "sample__note" }, text("span", "※ この数値は速報値です"))));
    grid.appendChild(h("div", { class: "sample__state sample__state--error" },
      text("p", "エラー", "sample__state-name"),
      h("p", { class: "sample__banner" }, text("span", "送信できませんでした"))));
    grid.appendChild(h("div", { class: "sample__state sample__state--info" },
      text("p", "情報", "sample__state-name"),
      h("div", { class: "sample__info" }, text("span", "補足: 出典は年次報告です"))));
    node.appendChild(grid);
    return node;
  }

  var SAMPLE_SLIDES = [
    { caption: "この見本は、主役色を大きな面で使ったときの印象と、ロゴ・フッタ・ページ番号・透かしの位置を確かめるためのものです。", build: coverSample },
    { caption: "この見本は、見出しと本文と箇条書きの読みやすさ、区切り線の濃さを確かめるためのものです。", build: bulletsSample },
    { caption: "この見本は、図表の系列色の見分けやすさと、強調色の目立ち方を確かめるためのものです。", build: chartSample },
    { caption: "この見本は、成功・注意・エラー・情報が状態として伝わるかを確かめるためのものです。状態の色は飾りには使いません。", build: statusSample }
  ];

  // 手動送りのみ。自動送りは実装しない。送りで preview を取り直さない（見比べているものが飛ばないため）。
  // 送りに動きは付けない（prefers-reduced-motion の既存規約と衝突させないため、常に即座に切り替える）。
  function sampleCarousel() {
    var index = 0, latest = null;
    var stage = h("div", { class: "carousel__stage" });
    var caption = text("p", "", "carousel__caption reason");
    var dots = h("div", { class: "carousel__dots" });
    var counter = text("span", "", "carousel__counter small muted");
    var back = button("", function () { move(-1); }, "btn--icon btn--ghost");
    var forward = button("", function () { move(1); }, "btn--icon btn--ghost");
    back.appendChild(SS.icon("arrow_back"));
    forward.appendChild(SS.icon("arrow_forward"));
    back.setAttribute("aria-label", "前の見本");
    forward.setAttribute("aria-label", "次の見本");
    var root = h("section", { class: "carousel", tabindex: "0", "aria-label": "配色の見本" },
      stage, caption, h("div", { class: "carousel__controls" }, back, dots, counter, forward));

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
      paintPalette(stage, latest.palette);
      var slide = list[index].build(latest);
      SS.append(slide, footerLine(latest));
      // `reserved` は「この枚はロゴの固定枠を約束するか」を枚ごとに言う旗。いまは 4 枚すべてが
      // 約束するので実質いつも true だが、旗はカルーセル自身の持ち物なので残す。
      SS.append(slide, logoOverlay(latest, list[index].reserved !== false));
      stage.appendChild(slide);
    }
    root.addEventListener("keydown", function (event) {
      if (event.key === "ArrowRight") { move(1); } else if (event.key === "ArrowLeft") { move(-1); } else { return; }
      event.preventDefault();
    });
    draw();
    return { node: root, update: function (p) { latest = p; draw(); } };
  }

  // ---- ロゴ ----
  // ロゴの画像 1 枚（役割 role）。PNG か JPEG（描くエンジンが受ける形）。画像は加工しない。
  // 送るのは data.assets 全部（画面が扱わない役割の画像も消さない。P1 のレビューの指摘）
  function logoSlot(data, opts, readonly, changed, fail) {
    var role = opts.role;
    var asset = data.assets.filter(function (item) { return item.role === role; })[0] || null;
    var enabled = h("input", { type: "checkbox", id: "logo-use-" + (++nextInput) }); enabled.checked = opts.initiallyOn !== undefined ? opts.initiallyOn && !!asset : !!asset;
    var file = h("input", { type: "file", accept: "image/png,image/jpeg", disabled: readonly || !enabled.checked, "aria-label": opts.label + "の画像" });
    var info = text("p", asset ? "登録画像" : "なし", "small"), img = h("img", { class: "logo-original", alt: opts.label + "のプレビュー", hidden: !asset });
    if (asset) { img.src = "/api/assets/" + asset.asset_id + "/file"; }
    var busy = false;
    function remove() { data.assets = data.assets.filter(function (item) { return item.role !== role; }); img.hidden = true; img.removeAttribute("src"); info.textContent = "なし"; changed(); }
    enabled.addEventListener("change", function () { file.disabled = readonly || !enabled.checked; if (!enabled.checked && opts.removeWhenOff) { remove(); } else { changed(); } });
    file.addEventListener("change", function () {
      var upload = file.files[0]; if (!upload || busy) { return; }
      if (upload.size > 10 * 1024 * 1024) { fail(new SS.ApiError("10 MiB以下の画像を選んでください。")); return; }
      if (upload.type && upload.type !== "image/png" && upload.type !== "image/jpeg") { fail(new SS.ApiError("ロゴは PNG か JPEG を選んでください。")); return; }
      busy = true; info.textContent = "画像を確認しています…";
      SS.uploadImage(upload).then(function (result) {
        data.assets = data.assets.filter(function (item) { return item.role !== role; }); data.assets.push({ asset_id: result.asset_id, role: role, label: opts.label });
        img.src = "/api/assets/" + result.asset_id + "/file"; img.hidden = false; info.textContent = result.width + "×" + result.height + " px の画像を登録しました。"; changed();
      }).catch(fail).finally(function () { busy = false; });
    });
    return { enabled: enabled, has: function () { return data.assets.some(function (item) { return item.role === role; }); },
      node: h("section", { class: "logo-slot" }, h("label", { for: enabled.id }, enabled, " " + opts.checkLabel), text("p", opts.help, "small"), file, img, info,
        button("画像を取り除く", function () { enabled.checked = false; file.disabled = true; remove(); }, "btn--small")) };
  }

  // 四隅は 2x2 のボタンで選ぶ。並びと表示名、そして隅ごとの予約枠はすべてサーバのカタログの値を使い、
  // 隅の名前から位置を導出する計算は画面に持たない（応答の数値を canvas で割るだけ）。
  function cornerPicker(control, initial, changed) {
    var value = initial, known = {};
    control.corners.forEach(function (option) {
      if (option.clean_area) { known[option.value] = boxRatio(option.clean_area, control.canvas); }
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
      // 選んでいる隅は、実際に焼き込む枠（preview 応答）で描き直す。比率が既定と違うブランドでも合う。
      learn: function (p) { if (p.clean_area && p.canvas) { known[value] = boxRatio(p.clean_area, p.canvas); sync(); } }
    };
  }

  // ---- 編集画面（/settings/brands/<id>）。サイドバーは置かない ----
  function brandEditor(root, catalog, item) {
    var data = clone(item), readonly = item.kind === "preset", revisionId = item.current_revision_id;
    var timer = null, dirty = false, serial = 0, verifiedSerial = -1;
    var errors = h("div", { role: "alert" }), notes = h("div", { class: "sample-notes" }), logoWarnings = h("div");
    var status = text("p", "色や位置を変えると、見本も自動で更新されます。", "reason");
    var saveState = text("span", "", "title-save-state small muted");
    var form = h("fieldset", { disabled: readonly });
    var save = button("保存する", saveNow, "btn--primary");
    var carousel = sampleCarousel();
    var name = h("input", { class: "title-input", type: "text", maxlength: 200, "aria-label": "ブランドの名前", readonly: readonly });
    name.value = data.name;
    function fail(error) { SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error")); }
    function invalidate() { serial++; verifiedSerial = -1; dirty = true; save.disabled = true; SS.settingsLeave = { dirty: function () { return dirty; } }; clearTimeout(timer); timer = setTimeout(preview, 300); }
    form.addEventListener("input", invalidate);
    form.addEventListener("change", invalidate);
    var changed = invalidate;
    // 名前も色と同じ「保存する」の対象。Enter は入力を確定するだけで、保存も自動保存もしない。
    name.addEventListener("input", invalidate);
    name.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); name.blur(); } });

    var colors = data.config.colors || {}, semantic = colors.semantic || {}, colorGrid = h("div", { class: "setting-grid" });
    var primary = colorField("基本色", colors.primary, changed), accent = colorField("強調色", colors.accent, changed), info = colorField("情報色（任意）", colors.info || semantic.info || "", changed, true);
    colorGrid.appendChild(primary.node); colorGrid.appendChild(accent.node); colorGrid.appendChild(info.node);
    form.appendChild(h("h2", { text: "色" })); form.appendChild(colorGrid);
    // 使うロゴの役割: いまの variant のもの（無ければ背景透過）。白いロゴはピッチで、ロゴが沈む場所に使う
    var variant = (data.config.logo || {}).variant || "none";
    var mainVariant = variant !== "none" && variant !== "white" ? variant : "transparent";
    var logo = logoSlot(data, { role: LOGO_ROLE[mainVariant], label: "ロゴ", checkLabel: "ロゴを使う", initiallyOn: variant !== "none",
      help: "PNG・JPEG、10 MiB以下。画像は加工しません（色も縦横比もそのまま）。" }, readonly, changed, fail);
    var whiteLogo = logoSlot(data, { role: "logo_white", label: "白いロゴ", checkLabel: "白いロゴも登録する（任意）", removeWhenOff: true,
      help: "ピッチの資料で、写真や絵の上でロゴが沈む場所に使います。ロゴと同じ縦横比の公式の白版だけを登録してください。" }, readonly, changed, fail);
    form.appendChild(h("h2", { text: "ロゴ" })); form.appendChild(logo.node); form.appendChild(whiteLogo.node);
    var control = catalog.logo_control, currentPosition = Object.assign({}, control.default, (data.config.logo || {}).placement || {});
    var corner = cornerPicker(control, currentPosition[control.key], changed);
    form.appendChild(text("p", "ロゴの位置", "setting-field")); form.appendChild(corner.node);
    form.appendChild(text("p", "選んだ角にロゴが入ります。写真や文字で角がふさがったページは、描くときに別の角（右下 ⇄ 左下・右上 → 右下）へ逃がし、どこもふさがっていれば置きません。", "small"));
    form.appendChild(button("既定に戻す", function () { currentPosition = clone(control.default); corner.set(currentPosition[control.key]); changed(); }, "btn--small"));
    form.appendChild(logoWarnings);
    // 書式（フッタ・ページ番号・透かし）はブランドの中に持つ（P4。「書式を登録」は押せないまま）
    var fmt = data.config.format || {}, limits = catalog.limits || {};
    var footer = field("フッタの文字", (data.config.footer || {}).text || "", { maxlength: limits.footer || 120, placeholder: "例: 社外秘・会社名・資料名" });
    footer.el.addEventListener("input", changed);
    var pageNumbers = h("input", { type: "checkbox", id: "brand-page-numbers-" + (++nextInput) }); pageNumbers.checked = !!fmt.page_numbers;
    var watermark = field("透かしの文字", fmt.watermark || "", { maxlength: limits.watermark || 60, placeholder: "例: 社外秘・配布禁止" });
    watermark.el.addEventListener("input", changed);
    form.appendChild(h("h2", { text: "書式", class: "brand-format-title" }));
    form.appendChild(h("section", { class: "brand-format" }, footer.node,
      text("p", "中身のページの左下に毎ページ入ります（表紙・章扉・全面の写真には入りません）。空にすれば入りません。", "small"),
      h("label", { class: "setting-field setting-check", for: pageNumbers.id }, pageNumbers, " ページ番号を中央下に出す"),
      watermark.node,
      text("p", "全ページの中央下の端に薄いグレーで入ります。空にすれば入りません。", "small")));
    var advanced = h("details", {}, text("summary", "拡張設定 — 色"), text("p", "通常は空欄のままで、基本色から算出します。指定した色だけを上書きします。", "small")), advancedGrid = h("div", { class: "setting-grid" }); form.appendChild(advanced);
    advanced.appendChild(advancedGrid);
    var overrideFields = {}, initialOverrideCheck = true;
    [["primary_light", "主役色（淡）"], ["primary_dark", "主役色（濃）"], ["border", "区切り線"], ["surface", "面の色"], ["base", "地の色"]].forEach(function (definition) { overrideFields[definition[0]] = colorField(definition[1], colors[definition[0]] || "", changed, true, false); advancedGrid.appendChild(overrideFields[definition[0]].node); });
    // 押せない理由は「まだ何も変えていない」か「見本の読み直しが済んでいない」のどちらか。
    function refreshSave() {
      save.disabled = readonly || !dirty || verifiedSerial !== serial;
    }
    function config() {
      var primaryValue = primary.value(), accentValue = accent.value(); if (!primaryValue || !accentValue) { throw new SS.ApiError("基本色と強調色は #RRGGBB の6桁で入力してください。", "color_required"); }
      // 範囲から降ろした設定（src/quick_slide/scope.py）はこの画面で扱わない。**キーごと送らない。**
      // 保存済みの値はサーバが次の改訂へそのまま持ち越すので、保存し直しても消えない。
      var c = { schema_version: 1, colors: { primary: primaryValue, accent: accentValue }, logo: { variant: "none", placement: clone(currentPosition) },
        footer: { text: footer.el.value.trim(), style: { position: "footer_left" } },
        format: { page_numbers: !!pageNumbers.checked, watermark: watermark.el.value.trim() } };
      c.logo.placement[control.key] = corner.value();
      if (info.enabled.checked) { var infoValue = info.value(); if (!infoValue) { throw new SS.ApiError("情報色は #RRGGBB の6桁で入力してください。", "info_invalid"); } c.colors.info = infoValue; }
      Object.keys(overrideFields).forEach(function (key) { if (overrideFields[key].enabled.checked) { var value = overrideFields[key].value(); if (!value) { throw new SS.ApiError("詳しく指定する色は #RRGGBB の6桁で入力してください。", "override_invalid"); } c.colors[key] = value; } });
      if (logo.enabled.checked) { c.logo.variant = mainVariant; }
      return c;
    }
    // 保存で送る形。名前と config（schema_version を含む）を 1 回で送り、改訂を 1 つだけ作る。
    // 画像は data.assets を全部送る（使っていない役割の画像も消さない）。サーバもロゴ以外の役割は持ち越す
    function requestBody() { var c = config(); return { name: name.value.trim(), config: c, assets: clone(data.assets) }; }
    function preview() {
      var body; try { body = requestBody(); } catch (error) { fail(error); return Promise.resolve(); }
      var own = serial;
      status.textContent = "配色を算出しています…";
      return SS.api("/api/settings/brands/preview", { method: "POST", body: { config: body.config, assets: body.assets } }).then(function (p) {
        if (own !== serial) { return; }
        if (initialOverrideCheck) {
          initialOverrideCheck = false;
          var restored = false;
          Object.keys(overrideFields).forEach(function (key) {
            if (colors[key] && colors[key] !== p.palette.derived[key]) { overrideFields[key].setEnabled(true); restored = true; }
          });
          if (restored) { serial++; verifiedSerial = -1; return preview(); }
        }
        SS.clear(errors);
        carousel.update(p);
        corner.learn(p);
        SS.clear(notes);
        notes.appendChild(text("p", p.palette.overridden.length ? "上書き中: " + p.palette.overridden.join("、") : "色は基本色・強調色から算出しています。", "small"));
        notes.appendChild(text("p", p.palette.note, "reason"));
        if (p.copyright && p.copyright.message) { notes.appendChild(text("p", p.copyright.message, "reason")); }
        SS.clear(logoWarnings);
        (p.warnings || []).forEach(function (warning) { logoWarnings.appendChild(text("p", warning.message, "notice notice--info small")); });
        status.textContent = "見本を更新しました。"; verifiedSerial = own; refreshSave();
      }).catch(function (error) { save.disabled = true; status.textContent = "見本を更新できませんでした。"; fail(error); });
    }
    async function saveNow() {
      if (verifiedSerial !== serial) { return; }
      var body; try { body = requestBody(); } catch (error) { fail(error); return; }
      if (!body.name) { fail(new SS.ApiError("名前を入力してください。", "name_required")); return; }
      if (body.config.logo.variant !== "none" && !logo.has()) { fail(new SS.ApiError("ロゴの画像を選んでください。使わない場合はチェックを外してください。", "logo_required")); return; }
      try {
        var result = await SS.api("/api/settings/brands/" + item.id, { method: "POST", body: Object.assign(body, { expected_revision_id: revisionId }) });
        if (result.verified !== true) { throw new SS.ApiError("保存結果を確認できませんでした。一覧を読み直してください。", "save_unverified"); }
        revisionId = result.item.current_revision_id;
        data.name = result.item.name; name.value = result.item.name;
        dirty = false; SS.settingsLeave = null; save.disabled = true;
        data.assets = clone(result.item.assets || data.assets);
        saveState.textContent = "保存済み";
        setTimeout(function () { saveState.textContent = ""; }, 1200);
        SS.toast("「" + result.item.name + "」を保存しました。このブランドのスライドを次に描くときから効きます。");
        SS.applyBrandTheme(document.documentElement, result.item.config);
      } catch (error) { fail(error); }
    }

    var head = h("div", { class: "brand-editor__head" },
      h("a", { class: "brand-editor__back", href: "/settings", "data-nav": true, text: "← 一覧" }),
      h("div", { class: "title-editor" }, name, saveState),
      readonly ? h("span", { class: "chip", text: "付属" }) : null);
    var rail = h("div", { class: "brand-editor__rail" }, errors);
    if (readonly) { rail.appendChild(text("p", item.capabilities.edit.reason, "notice")); }
    rail.appendChild(form);
    form.appendChild(h("div", { class: "settings-actions" }, save, status));
    save.disabled = true;
    if (!readonly) {
      var danger = h("section", { class: "danger-zone" }, text("h2", "危険な操作"), text("p", "このブランドを削除すると、設定一覧から取り除かれます。作成済みのスライドは変わりません。", "small"));
      danger.appendChild(button("このブランドを削除", function () {
        if (!SS.confirm("「" + data.name + "」を削除します。作成済みのスライドは残ります。よろしいですか？")) { return; }
        SS.api("/api/settings/brands/" + item.id + "/delete", { method: "POST", body: { expected_revision_id: revisionId } }).then(function (result) {
          if (!result.deleted) { throw new SS.ApiError("削除を確認できませんでした。", "delete_unverified"); }
          dirty = false; SS.settingsLeave = null; SS.toast("ブランドを削除しました。", { sticky: true }); SS.navigate("/settings");
        }).catch(fail);
      }, "btn--danger"));
      rail.appendChild(danger);
    }
    var previewColumn = h("div", { class: "brand-editor__preview" },
      text("h2", "配色シミュレータ", "brand-editor__preview-title"), carousel.node, text("p", EXAMPLE_NOTE, "small"), notes);
    SS.clear(root).appendChild(h("div", { class: "settings-page settings-page--wide" }, head,
      h("div", { class: "brand-editor__body" }, previewColumn, rail)));
    preview();
  }

  // ---- 一覧画面（/settings）----
  var SETTINGS_TABS = [
    { section: "brands", href: "/settings", label: "ブランド", title: "ブランド",
      lead: "ロゴと配色を、スライドスタイルとは別に管理します。" },
    { section: "styles", href: "/settings/styles", label: "スライドスタイル", title: "スライドスタイル",
      lead: "スライドの型を登録します。ブランドの見た目とは別に管理します。" },
    { section: "server", href: "/settings/server", label: "アクセス", title: "アクセス",
      lead: "Quick Slide を、このパソコンだけで使うか、ほかのパソコンやスマートフォンからも開けるようにするかを決めます。" }
  ];

  function settingsHead(section) {
    var current = SETTINGS_TABS.filter(function (t) { return t.section === section; })[0] || SETTINGS_TABS[0];
    return [
      text("h1", current.title),
      text("p", current.lead, "muted"),
      h("nav", { class: "settings-tabs", "aria-label": "設定の画面" },
        SETTINGS_TABS.map(function (t) {
          return h("a", { href: t.href, "data-nav": true,
            "aria-current": t.section === current.section ? "page" : null, text: t.label });
        }))
    ];
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
        { label: "改名", disabled: !edit.available, reason: edit.reason, onSelect: function () {
          SS.textModal({ title: "ブランドの名前を変更", label: "名前", value: entry.name, submitLabel: "変更する",
            onSubmit: function (value) { return saveAs(value, true).then(function (saved) { SS.toast("「" + saved.name + "」に変更しました。"); reload(); }); } });
        } },
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

  function brandsScreen(root, catalog) {
    function reload() { SS.navigate("/settings", true); }
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
    var cap = catalog.capabilities.format;
    SS.clear(root).appendChild(h("div", { class: "settings-page" }, settingsHead("brands"),
      h("div", { class: "settings-toolbar" }, create),
      rows,
      h("fieldset", { disabled: !cap.available }, SS.disabledButton(catalog.capabilities, "format")),
      text("p", cap.reason, "reason")));
  }

  // ---- スライドスタイル ----
  function styleEditor(panel, catalog, item, copying, onDone, selectAllPatterns) {
    var data = clone(item), readonly = item.kind === "preset" && !copying;
    var form = h("fieldset", { disabled: readonly }), errors = h("div", { role: "alert" });
    var name = field("名前", data.name + (copying ? " のコピー" : ""), { maxlength: 200, placeholder: "例: 経理向け月次報告" });
    var emphasis = field("誰に向けるか・どう書くか", data.config.emphasis_prompt || "", { multiline: true, rows: 6, required: true, placeholder: "例: 経営会議の参加者に向け、結論と数値を先に示す。専門用語は短く説明する。" });
    var tone = select("文章の調子", data.config.tone, catalog.tones);
    function dialOptions(key) {
      return [{ value: "", label: "指定なし（これまでどおり）" }].concat(catalog.style_dials[key]);
    }
    var typeface = select("文字の感じ", data.config.typeface || "", dialOptions("typeface"));
    var density = select("密度", data.config.density || "", dialOptions("density"));
    var visualRatio = select("絵の量", data.config.visual_ratio || "", dialOptions("visual_ratio"));
    var recipe = field("描き味の補足（任意）", data.config.style_recipe || "", { placeholder: "例: 紙に描いたような柔らかい線" });
    // エンジンのスタイル（モードごと）・画風・作風の一言（P4。描く回のたびにエンジンへ渡る）
    var engineStyle = data.config.engine_style || {}, defaults = catalog.engine_style_defaults || {};
    var presentationStyle = select("プレゼン（編集できる PPTX）の見せ方", engineStyle.presentation || defaults.presentation, catalog.engine_styles.presentation);
    var pitchStyle = select("ピッチ（絵で描く資料）の見せ方", engineStyle.pitch || defaults.pitch, catalog.engine_styles.pitch);
    var imageLook = select("画風", data.config.image_look || "", [{ value: "", label: "指定なし（見せ方の既定）" }].concat(catalog.image_looks));
    var styleNote = field("作風の一言（任意）", data.config.style_note || "", { maxlength: (catalog.limits || {}).style_note || 400, placeholder: "例: 落ち着いた色で、余白を広く" });

    // 使うパターン = 台本係が使ってよいページの型（エンジンの型の一覧）。「おまかせ」はすべて（ほかと一緒には選べない）
    var currentPatterns = data.config.allowed_layouts || ["auto"];
    var patternInputs = [], autoInput = null, patternError = h("div", { role: "alert" });
    var patternStatus = h("div", { class: "pattern-picker__status", "aria-live": "polite" });
    var patternGrid = h("div", { class: "pattern-grid" });
    catalog.layout_patterns.forEach(function (option) {
      var id = "style-pattern-" + (++nextInput);
      var input = h("input", { type: "checkbox", id: id, value: option.value });
      input.checked = option.value === "auto" ? (selectAllPatterns || currentPatterns.indexOf("auto") !== -1)
        : (!selectAllPatterns && currentPatterns.indexOf(option.value) !== -1);
      if (option.value === "auto") { autoInput = input; } else { patternInputs.push(input); }
      patternGrid.appendChild(h("label", { class: "pattern-choice" + (option.value === "auto" ? " pattern-choice--auto" : ""), for: id }, input,
        h("span", { class: "pattern-choice__copy" }, text("strong", option.label), text("span", option.use, "pattern-choice__use"))));
    });
    var patterns = h("fieldset", { class: "pattern-picker" }, text("legend", "使うパターン（ページの型）"),
      text("p", "「おまかせ」なら台本係がすべての型から選びます。使う型をしぼるときは「おまかせ」を外して、使う型だけを選びます。", "small muted"),
      h("div", { class: "pattern-picker__actions" },
        button("おまかせに戻す", function () { autoInput.checked = true; patternInputs.forEach(function (input) { input.checked = false; }); syncPatternProblem(); }, "btn--small"),
        button("型をすべて外す", function () { autoInput.checked = false; patternInputs.forEach(function (input) { input.checked = false; }); syncPatternProblem(); }, "btn--small")),
      patternStatus, patternGrid, patternError);

    var saveButton = button("保存する", save, "btn--primary");
    function selectedPatterns() {
      if (autoInput && autoInput.checked) { return ["auto"]; }
      return patternInputs.filter(function (input) { return input.checked; }).map(function (input) { return input.value; });
    }
    autoInput.addEventListener("change", function () { if (autoInput.checked) { patternInputs.forEach(function (input) { input.checked = false; }); } });
    patternInputs.forEach(function (input) { input.addEventListener("change", function () { if (input.checked) { autoInput.checked = false; } }); });
    function syncPatternProblem() {
      var chosen = selectedPatterns(), empty = chosen.length === 0;
      SS.clear(patternStatus);
      patternStatus.appendChild(text("p", chosen[0] === "auto" ? "おまかせ（すべての型）" : chosen.length + "種類の型を選択中", "small"));
      SS.clear(patternError);
      if (empty) { patternError.appendChild(text("p", "使うパターンを1つ以上選んでください。", "notice notice--error small")); }
      saveButton.disabled = readonly || empty;
      return empty;
    }
    patternInputs.concat([autoInput]).forEach(function (input) { input.addEventListener("change", syncPatternProblem); });
    function body() {
      var c = {};
      styleKeys.forEach(function (key) { if (Object.prototype.hasOwnProperty.call(data.config, key)) { c[key] = clone(data.config[key]); } });
      c.emphasis_prompt = emphasis.el.value.trim(); c.tone = tone.el.value;
      [["typeface", typeface], ["density", density], ["visual_ratio", visualRatio]].forEach(function (entry) {
        if (entry[1].el.value) { c[entry[0]] = entry[1].el.value; } else { delete c[entry[0]]; }
      });
      if (recipe.el.value.trim() || Object.prototype.hasOwnProperty.call(data.config, "style_recipe")) {
        c.style_recipe = recipe.el.value.trim();
      } else { delete c.style_recipe; }
      c.allowed_layouts = selectedPatterns();
      c.engine_style = { presentation: presentationStyle.el.value, pitch: pitchStyle.el.value };
      if (imageLook.el.value) { c.image_look = imageLook.el.value; } else { delete c.image_look; }
      if (styleNote.el.value.trim() || Object.prototype.hasOwnProperty.call(data.config, "style_note")) {
        c.style_note = styleNote.el.value.trim();
      } else { delete c.style_note; }
      return { name: name.el.value.trim(), config: c, source_id: copying ? item.id : undefined,
        expected_revision_id: item ? item.current_revision_id : undefined };
    }
    function save() {
      SS.clear(errors);
      if (!name.el.value.trim()) { errors.appendChild(text("p", "名前を入力してください。", "notice notice--error")); return; }
      if (!emphasis.el.value.trim()) { errors.appendChild(text("p", "誰に向けて、どう書くかを入力してください。", "notice notice--error")); return; }
      if (syncPatternProblem()) { return; }
      SS.api("/api/settings/styles" + (item && !copying ? "/" + item.id : ""), { method: "POST", body: body() }).then(function (result) {
        if (result.verified !== true) { throw new SS.ApiError("保存結果を確認できませんでした。", "save_unverified"); }
        SS.toast("スタイルを保存しました。", { sticky: true }); onDone();
      }).catch(function (error) { SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error")); });
    }
    SS.clear(panel); panel.appendChild(text("h2", copying ? "複写してスタイルを作る" : "スライドスタイルを編集"));
    if (readonly) { panel.appendChild(text("p", item.capabilities.edit.reason, "notice")); }
    if (item && !copying) { panel.appendChild(button("複写して作る", function () { styleEditor(panel, catalog, item, true, onDone, false); })); }
    panel.appendChild(errors); panel.appendChild(form);
    form.appendChild(name.node);
    form.appendChild(text("h3", "文章のこと", "style-section-title"));
    form.appendChild(emphasis.node);
    form.appendChild(text("p", "想定する読み手と、何をどの順で伝えるかを書きます。企画と画像づくりの両方に使います。", "small muted style-field-help"));
    form.appendChild(tone.node);
    form.appendChild(text("h3", "絵のこと", "style-section-title"));
    form.appendChild(h("div", { class: "style-dials" }, typeface.node, density.node, visualRatio.node));
    form.appendChild(text("p", "密度を「詰める」にすると、要素を多く置く見せ方です。", "small muted style-field-help"));
    form.appendChild(recipe.node);
    form.appendChild(text("h3", "描き方（エンジン）", "style-section-title"));
    form.appendChild(h("div", { class: "style-dials" }, presentationStyle.node, pitchStyle.node, imageLook.node));
    form.appendChild(styleNote.node);
    form.appendChild(text("p", "スライドを次に描くときから効きます（描いてあるスライドは、直すか組み直すまで変わりません）。全ページを1枚に描く infographic は、ピッチで最初に描くときにだけ選べます。", "small muted style-field-help"));
    form.appendChild(patterns);
    form.appendChild(h("div", { class: "settings-actions" }, saveButton));
    syncPatternProblem();
    if (item && !copying && !readonly) {
      panel.appendChild(button("一覧から削除", function () {
        if (!SS.confirm("「" + item.name + "」を一覧から削除します。よろしいですか？")) { return; }
        SS.api("/api/settings/styles/" + item.id + "/delete", { method: "POST", body: { expected_revision_id: item.current_revision_id } }).then(function (result) { if (!result.deleted) { throw new SS.ApiError("削除を確認できませんでした。", "delete_unverified"); } SS.toast("スタイルを削除しました。", { sticky: true }); onDone(); }).catch(function (error) { SS.clear(errors).appendChild(text("p", safeError(error), "notice notice--error")); });
      }, "btn--small"));
    }
  }

  function stylesScreen(root, catalog) {
    var panel = h("section", { class: "settings-editor", "aria-label": "スライドスタイルの編集" });
    var list = h("aside", { class: "settings-list", "aria-label": "スライドスタイル一覧" });
    function reload() { SS.navigate("/settings/styles", true); }
    function renderEditor(item, copying, selectAllPatterns) { styleEditor(panel, catalog, item, copying, reload, selectAllPatterns); }
    SS.clear(root).appendChild(h("div", { class: "settings-page" }, settingsHead("styles"), h("div", { class: "settings-layout" }, list, panel)));
    var startingStyle = catalog.styles.filter(function (entry) { return entry.id === catalog.style_default_id; })[0] || catalog.styles[0];
    list.appendChild(button("新しいスライドスタイル", function () { renderEditor(startingStyle, true, true); }, "btn--primary"));
    catalog.styles.forEach(function (entry) { list.appendChild(button(entry.name + (entry.kind === "preset" ? " · 付属" : ""), function () { renderEditor(entry, false); }, "setting-list-item")); });
    list.appendChild(text("p", "付属スタイルは複写して使えます。", "small muted"));
    renderEditor(catalog.styles[0] || null, false);
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
    return SS.api("/api/settings").then(function (catalog) {
      if (!guard()) { return; }
      if (route.section === "styles") { return stylesScreen(root, catalog); }
      if (route.section === "server") { return serverScreen(root, catalog); }
      if (route.section === "brand") {
        var item = catalog.brands.filter(function (entry) { return entry.id === route.brandId; })[0] || null;
        if (!item) {
          SS.clear(root).appendChild(h("div", { class: "settings-page" },
            h("div", { class: "notice notice--error", role: "alert" }, text("p", "このブランドは見つかりません。一覧から選び直してください。")),
            h("p", null, h("a", { href: "/settings", "data-nav": true, text: "← 一覧" }))));
          return;
        }
        return brandEditor(root, catalog, item);
      }
      return brandsScreen(root, catalog);
    });
  };
})();
