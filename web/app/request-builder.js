// 会話へ持ち込む依頼文を組み立てる。ここから生成の受付は呼ばない（ADR 0029）。
(function () {
  "use strict";
  var SS = window.SS, h = SS.h;
  function displayName(value) { return JSON.stringify(String(value).replace(/[\s\x00-\x1f\x7f]+/g, " ").trim().slice(0, 120)); }
  var LOOKS = { auto: "おまかせ", images: "画像多め", diagrams: "図解・説明を中心に" };
  var WORKFLOWS = { outline: "ページ構成を確認してから作る", direct: "そのまま作成まで一気に進める" };

  SS.views.create = function (root, route, guard) {
    SS.clear(root).appendChild(h("p", { text: "読み込んでいます…", class: "muted" }));
    return SS.api("/api/request-builder").then(function (data) {
      if (!guard()) { return; }
      var state = Object.assign({}, data.preferences), kinds = SS.deckKinds();
      var busy = false;
      var theme = h("textarea", { id: "request-theme", class: "create__text", rows: "3",
        placeholder: "例: 最新の国産モデルの競争について調べて", maxlength: "16000" });
      var status = h("p", { class: "request-status small", role: "status", "aria-live": "polite" });
      var error = h("p", { class: "create__error", role: "alert" });
      var preview = h("textarea", { class: "request-preview", id: "request-preview", readonly: true,
        "aria-label": "コピーされる依頼文", placeholder: "テーマを入力すると、コピーされる依頼文がここに表示されます。" });
      var copy = h("button", { type: "button", class: "btn btn--primary request-copy" }, SS.icon("content_copy"), "依頼文をコピー");
      var selectAll = h("button", { type: "button", class: "btn btn--small", text: "依頼文をすべて選択" });
      selectAll.addEventListener("click", function () { preview.focus(); preview.select(); preview.setSelectionRange(0, preview.value.length); });
      var brand = h("select", { id: "request-brand" }, data.brands.map(function (b) {
        return h("option", { value: b.id, text: b.name, selected: b.id === state.brand_id });
      }));
      var template = h("select", { id: "request-template" });
      var templateHint = h("p", { class: "small muted" });
      var quantity = h("input", { id: "request-quantity", type: "number", min: "1", max: String(data.max_quantity), step: "1",
        placeholder: "おまかせ", value: state.quantity === null ? "" : String(state.quantity) });
      var quantityChoice = h("select", { id: "request-quantity-choice", "aria-label": "分量の候補" },
        h("option", { value: "", text: "おまかせ" }), h("option", { value: "5", text: "5ページ" }),
        h("option", { value: "10", text: "10ページ" }), h("option", { value: "custom", text: "数を指定" }));
      quantityChoice.value = state.quantity === null ? "" : [5, 10].indexOf(state.quantity) >= 0 ? String(state.quantity) : "custom";
      quantity.hidden = quantityChoice.value !== "custom";
      quantity.setAttribute("aria-label", "分量を数で指定");
      var lookHint = h("p", { class: "small muted" });
      var look = h("select", { id: "request-look" }, Object.keys(LOOKS).map(function (k) {
        return h("option", { value: k, text: LOOKS[k], selected: state.look === k });
      }));
      function field(id, label, el, note) {
        return h("div", { class: "request-field" }, h("label", { for: id, text: label }), el, note || null);
      }
      function selectedTemplate() { return data.templates.find(function (t) { return t.template_id === state.template_id; }); }
      function syncTemplates() {
        var motion = state.mode === "motion";
        look.options[1].disabled = motion;
        if (motion && look.value === "images") { look.value = "auto"; }
        lookHint.textContent = motion ? "モーションは文字・図形の動きで見せます。画像は使いません。" : "";
        quantityChoice.options[1].textContent = "5" + (motion ? "場面" : "ページ");
        quantityChoice.options[2].textContent = "10" + (motion ? "場面" : "ページ");
        var matches = data.templates.filter(function (t) { return t.brand_id === state.brand_id && t.modes.indexOf(state.mode) >= 0 && state.mode !== "motion"; });
        if (!matches.some(function (t) { return t.template_id === state.template_id; })) { state.template_id = null; }
        SS.clear(template).appendChild(h("option", { value: "", text: "指定しない" }));
        matches.forEach(function (t) { template.appendChild(h("option", { value: t.template_id, text: t.name + "（v" + t.revision + "）" })); });
        template.value = state.template_id || "";
        template.disabled = state.mode === "motion";
        templateHint.textContent = state.mode === "motion" ? "モーションはテンプレートに未対応です。" :
          "登録したテンプレートを会話へ伝えます。生成への対応状況は会話で確認します。";
      }
      var cards = h("div", { class: "create__kinds", role: "radiogroup", "aria-labelledby": "request-kind-label" }, kinds.map(function (k) {
        // この画面は依頼文の作成。生成CLIの不足でコピーまで禁止しない。
        var radio = h("input", { type: "radio", name: "request-kind", value: k.mode, class: "create__radio", checked: k.mode === state.mode });
        radio.addEventListener("change", function () { state.mode = k.mode; syncTemplates(); sync(); });
        return h("label", { class: "kind-card", "data-tone": k.tone || "neutral" }, radio,
          h("span", { class: "kind-card__head" }, SS.icon(k.icon || "image"), h("span", { class: "kind-card__name", text: k.label }), h("span", { class: "kind-card__mark", "aria-hidden": "true" })),
          h("span", { class: "kind-card__desc", text: k.description }),
          h("span", { class: "kind-card__out small", text: "書き出し: " + (k.outputs || []).map(function (o) { return o.label || o; }).join("・") }));
      }));
      var help = h("button", { type: "button", class: "btn btn--icon btn--ghost", "aria-label": "形式の違いを見る", "aria-haspopup": "dialog" }, SS.icon("help"));
      help.addEventListener("click", function () { SS.openKindHelp(kinds, help); });
      var workflows = h("fieldset", { class: "request-workflow" }, h("legend", { text: "進め方" }), Object.keys(WORKFLOWS).map(function (key) {
        var radio = h("input", { type: "radio", name: "request-workflow", value: key, checked: state.workflow === key });
        radio.addEventListener("change", function () { state.workflow = key; sync(); });
        return h("label", null, radio, h("span", null, WORKFLOWS[key], h("small", { class: "muted", text: key === "outline" ? "会話で各ページの見出し・要点を確認してから作成します。" : "構成だけの確認を挟まず作成します。" })));
      }));
      function conditions() {
        return { mode: state.mode, brand_id: state.brand_id, template_id: state.template_id, quantity: quantity.value === "" ? null : Number(quantity.value), look: look.value, workflow: state.workflow };
      }
      function prompt() {
        if (!theme.value.trim()) { return ""; }
        var kind = kinds.find(function (k) { return k.mode === state.mode; });
        var chosenBrand = data.brands.find(function (b) { return b.id === state.brand_id; });
        var t = selectedTemplate(), q = conditions().quantity;
        var lines = ["Quick Slide で資料を作ってください。", "", "## テーマ・依頼", theme.value.trim(), "", "## 作成条件",
          "- 形式: " + (kind ? kind.label : state.mode) + "（mode: " + state.mode + "）",
          "- 分量: " + (q === null ? "内容に合わせておまかせ" : q + (state.mode === "motion" ? "場面" : "ページ")),
          "- 見せ方: " + LOOKS[look.value],
          "- ブランド: " + displayName(chosenBrand.name) + "（brand_id: " + chosenBrand.id + "）",
          "- デザインテンプレート: " + (t ? displayName(t.name) + "（template_id: " + t.template_id + "、v" + t.revision + "、revision_id: " + t.revision_id + "）" : "指定なし"),
          "- 進め方: " + WORKFLOWS[state.workflow], "", "## 進めるときのお願い",
          "調査・事実確認・必要な外部スキルの実行は、この会話で行い、調べた情報には出典を添えてください。",
          "完成した原稿を quick-slide スキルで、この Quick Slide と同じ環境へ渡してください。描画エンジンを直接呼ばず、Quick Slide で資料を作成してください。",
          "必須の情報が足りない場合は、この会話で質問してください。"];
        if (t) { lines.push("選んだテンプレートを先に取得し、版・対応形式と必要な条件を確認してください。生成へ適用できない場合は、別の設定に置き換えたり黙って省略したりせず、この会話で確認してください。"); }
        if (state.workflow === "outline") {
          lines.push("まず各ページの見出し・要点を示して、私のOKを待ってください。了承後は合意したページ数・順序・主張を保ち、--outline-fixed で投入してください。");
        } else { lines.push("構成だけの中間承認は挟まず、原稿作成からスライド作成まで進めてください。"); }
        lines.push("quick-slide スキルや同じ環境を使えない場合は、その旨と原稿を返してください。資料やURLを作ったことにしないでください。", "形式・ブランド・枚数などの条件は、原稿本文とは別に対応する引数で渡してください。見せ方の希望は --style-note に渡してください。", "完成後は Quick Slide の画面のURLをこの会話に残してください。");
        return lines.join("\n");
      }
      function resizePreview() {
        if (window.matchMedia("(max-width: 767px)").matches) {
          preview.style.height = "auto";
          preview.style.height = preview.scrollHeight + "px";
        } else { preview.style.height = ""; }
      }
      function validQuantity() { return quantity.validity.valid && !(quantityChoice.value === "custom" && quantity.value === ""); }
      function sync() {
        error.textContent = validQuantity() ? "" : "分量は1〜" + data.max_quantity + "の整数で指定してください。";
        preview.value = prompt();
        copy.disabled = busy || !preview.value || !validQuantity();
        selectAll.disabled = !preview.value;
        status.textContent = "";
        resizePreview();
      }
      function refreshCandidates() {
        return SS.api("/api/request-builder").then(function (latest) {
          if (!guard()) { return; }
          data.brands = latest.brands; data.templates = latest.templates;
          if (!data.brands.some(function (b) { return b.id === state.brand_id; })) { state.brand_id = latest.preferences.brand_id; }
          SS.clear(brand);
          data.brands.forEach(function (b) { brand.appendChild(h("option", { value: b.id, text: b.name })); });
          brand.value = state.brand_id;
          syncTemplates(); sync();
          status.textContent = "コピーした依頼文には使えなくなった候補が含まれます。そのまま貼り付けないでください。候補を更新したので、条件を確認してコピーし直してください。";
        }, function () {
          if (guard()) { status.textContent = "コピーした依頼文には使えなくなった候補が含まれます。貼り付けず、入力を控えて画面を再読み込みしてください。"; }
        });
      }
      copy.addEventListener("click", function () {
        if (copy.disabled) { return; }
        // コピー中に選択を変えても、記憶するのは実際にコピーした時点の条件。
        var saved = conditions(), text = preview.value;
        busy = true; copy.disabled = true;
        SS.copyText(text).then(function (ok) {
          if (!ok) { status.textContent = "コピーできませんでした。「依頼文をすべて選択」から手動でコピーしてください。"; return; }
          status.textContent = "コピーしました。会話に貼り付けて送信してください。";
          return SS.api("/api/request-builder/preferences", { method: "POST", body: saved }).then(function () {
            if (guard()) { status.textContent = "コピーしました。選択条件を記憶しました。会話に貼り付けて送信してください。"; }
          }, function (e) {
            if (!guard()) { return; }
            if (e.code === "request_conditions_changed") { return refreshCandidates(); }
            status.textContent = "依頼文はコピーしましたが、条件を記憶できませんでした。" + (e.code === "request_conditions_unsaved" ? "" : (e.message || ""));
          });
        }).catch(function () { if (guard()) { status.textContent = "コピーできませんでした。「依頼文をすべて選択」から手動でコピーしてください。"; } })
          .finally(function () { busy = false; if (guard()) { copy.disabled = !preview.value || !validQuantity(); } });
      });
      theme.addEventListener("input", sync);
      quantityChoice.addEventListener("change", function () {
        quantity.hidden = quantityChoice.value !== "custom";
        quantity.value = quantityChoice.value === "custom" ? "" : quantityChoice.value;
        sync();
        if (!quantity.hidden) { quantity.focus(); }
      });
      quantity.addEventListener("input", function () { quantityChoice.value = "custom"; sync(); });
      look.addEventListener("change", sync);
      brand.addEventListener("change", function () { state.brand_id = brand.value; syncTemplates(); sync(); });
      template.addEventListener("change", function () { state.template_id = template.value || null; sync(); });
      SS.clear(root);
      root.appendChild(h("a", { href: "/", "data-nav": true, class: "request-back", text: "← ホーム" }));
      root.appendChild(h("div", { class: "view-head" }, h("div", null, h("h1", { text: "資料を作成する" }), h("p", { class: "muted", text: "会話に渡す依頼文をつくります。" }))));
      root.appendChild(h("p", { class: "request-notice" }, "この画面ではスライドは作成されません。依頼文をコピーして、Quick Slide を導入した Codex・Claude Code の会話に貼り付けて送信してください。"));
      if (data.warning) { root.appendChild(h("p", { class: "request-notice", role: "status", text: data.warning })); }
      var inputs = h("div", { class: "request-inputs" }, field("request-theme", "どんな資料を作りますか？", theme),
        h("div", { class: "request-kind-head" }, h("span", { id: "request-kind-label", text: "形式" }), help), cards,
        h("div", { class: "request-params" }, field("request-brand", "ブランド", brand), field("request-template", "テンプレート", template, templateHint),
          field("request-quantity-choice", "分量", h("div", { class: "request-quantity" }, quantityChoice, quantity)), field("request-look", "見せ方", look, lookHint)), workflows, error);
      var output = h("div", { class: "request-output" }, h("div", { class: "request-preview-wrap" }, h("label", { for: "request-preview", text: "コピーされる依頼文" }), preview),
        h("div", { class: "request-copy-area" }, copy, status, selectAll, h("p", { class: "small muted", text: "コピーすると選択条件を記憶します。テーマ本文は保存しません。" })));
      root.appendChild(h("section", { class: "request-composer", "aria-label": "依頼文の作成" }, inputs, output));
      syncTemplates(); sync();
      var observer = new ResizeObserver(function () { if (guard()) { resizePreview(); } else { observer.disconnect(); } });
      observer.observe(root);
    });
  };
})();
