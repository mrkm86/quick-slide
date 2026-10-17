// デザインテンプレート（設定 › ブランド › デザインテンプレート）の本番の画面。
//   - section(brand): ブランドの詳細の下に出す一覧（追加・インポート・編集・複製・エクスポート・削除）
//   - screen(...): 編集画面（/settings/brands/<ブランド>/templates/<テンプレート>）。中身は design-template-editor.js で、
//     ここは通信（SS.api と本番の経路）と、未保存のときに画面を離れる確認だけを渡す
// 保存の判断（版・上書き・競合）はサーバ。ここは入力を送って、答えを描くだけ。
(function () {
  "use strict";
  var SS = window.SS, h = SS.h;
  var BASE = "/api/settings/design-templates";
  function text(tag, value, cls) { return h(tag, { text: value, class: cls }); }
  function button(label, fn, cls) { return h("button", { type: "button", class: "btn " + (cls || ""), text: label, onclick: fn }); }
  function editorPath(brandId, templateId) { return "/settings/brands/" + brandId + "/templates/" + templateId; }
  function modeLabel(mode) { return mode === "pitch" ? "ピッチ" : "プレゼン"; }

  // ---- 一覧（ブランドの詳細の下）----
  function section(brand) {
    var rows = h("div", { class: "setting-rows" });
    var picker = h("input", { type: "file", accept: ".md,text/markdown,text/plain", hidden: true });
    var importButton = button("インポート", function () { picker.click(); }, "btn--small btn--ghost");
    var addButton = button("追加する", addTemplate, "btn--small");
    picker.addEventListener("change", function () { var file = picker.files[0]; picker.value = ""; if (file) { importFile(file); } });
    var node = h("section", { class: "editor-section", id: "design-templates" },
      h("div", { class: "template-section-head" }, text("h2", "デザインテンプレート", "editor-section__title"),
        h("div", { class: "template-actions" }, importButton, addButton, picker)),
      text("p", "このブランドで使う、台本と描画の方針です。", "small muted"),
      rows);

    function load() {
      return SS.api(BASE + "?brand_id=" + encodeURIComponent(brand.id)).then(function (result) {
        draw(result.templates);
      }).catch(function (error) {
        SS.clear(rows).appendChild(text("p", error && error.message ? error.message : "テンプレートを読み込めませんでした。", "notice notice--error"));
      });
    }
    function draw(items) {
      SS.clear(rows);
      if (!items.length) { rows.appendChild(text("p", "まだありません。", "small muted")); return; }
      items.forEach(function (item) {
        // 編集はよく使うので見せ、複製・エクスポート・削除は ⋮ に畳む（ブランドの一覧と同じ置き方）
        var more = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": item.name + "の操作", "aria-haspopup": "menu", onclick: function () {
          SS.menu(more, [{ label: "複製", onSelect: function () { duplicate(item); } },
            { label: "エクスポート", onSelect: function () { exportMarkdown(item); } }, "-",
            { label: "削除", danger: true, onSelect: function () { remove(item); } }]);
        } }, SS.icon("more_vert"));
        var href = editorPath(brand.id, item.template_id);
        // 1行目に名前と版の札（ページの版と同じ見た目）、2行目に使える形式の札
        rows.appendChild(h("div", { class: "setting-row", "data-template": item.template_id }, h("div", { class: "setting-row__name" },
          h("div", { class: "template-list__title" }, h("a", { href: href, "data-nav": true, text: item.name }), item.revision ? text("span", "v" + item.revision, "chip page__ver") : null),
          h("div", { class: "template-list__modes" }, item.modes.map(function (m) { return SS.kindTag(m); }))),
          h("div", { class: "template-actions menu-anchor" }, h("a", { class: "btn btn--small", href: href, "data-nav": true, text: "編集" }), more)));
      });
    }

    // 追加は名前だけ聞いて、空のテンプレート（v1）を作ってから編集画面へ行く
    function addTemplate() {
      SS.textModal({ title: "デザインテンプレートを追加", label: "名前", submitLabel: "追加する", onSubmit: function (name) {
        return SS.api(BASE, { method: "POST", body: { brand_id: brand.id, document: SS.newDesignTemplate("new", brand.id, name) } }).then(function (result) {
          load();
          SS.navigate(editorPath(brand.id, result.template_id));
        });
      } });
    }
    // 複製は別のテンプレートとして v1 から始める（元の版の履歴は引き継がない）
    function duplicate(item) {
      SS.api(BASE + "/" + item.template_id + "/copy", { method: "POST", body: { expected_revision_id: item.revision_id } }).then(function (result) {
        SS.toast("「" + result.document.name + "」を作りました。");
        load();
        SS.navigate(editorPath(brand.id, result.template_id));
      }).catch(function (error) { SS.toast(error.message, { kind: "error" }); load(); });
    }
    // 消さずに一覧から外す。このテンプレートで作った資料は、作ったときの中身を控えているので描き直せる
    function remove(item) {
      if (!SS.confirm("「" + item.name + "」を削除します。このテンプレートで作った資料は、そのまま直したり描き直したりできます。")) { return; }
      SS.api(BASE + "/" + item.template_id + "/delete", { method: "POST", body: { expected_revision_id: item.revision_id } }).then(function () {
        SS.toast("「" + item.name + "」を削除しました。");
        load();
      }).catch(function (error) { SS.toast(error.message, { kind: "error" }); load(); });
    }
    // 書き出すファイルには、リソースのパスがそのまま入る（仕様。確認は出さない）
    function exportMarkdown(item) {
      var filename = "qs-template.md";
      fetch(BASE + "/" + item.template_id + "/markdown").then(function (r) {
        if (!r.ok) {
          return r.json().then(function (j) { throw new SS.ApiError((j && j.error && j.error.message) || "エクスポートできませんでした。", "export_failed", r.status); },
            function () { throw new SS.ApiError("エクスポートできませんでした。", "export_failed", r.status); });
        }
        var m = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get("Content-Disposition") || "");
        if (m) { try { filename = decodeURIComponent(m[1]); } catch (e) { /* 読めなければ既定の名前 */ } }
        return r.blob();
      }).then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = h("a", { href: url, download: filename });
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      }).catch(SS.toastError);
    }
    // 取り込みは 2 段: ファイルを読んで中身を確かめ、取り込み方を選んでから保存する（黙って上書きしない）
    function importFile(file) {
      file.text().then(function (body) {
        return SS.api(BASE + "/import/check", { method: "POST", body: { text: body, brand_id: brand.id } }).then(function (summary) { importDialog(body, summary); });
      }).catch(function (error) { SS.toast(error && error.message ? error.message : "ファイルを読み込めませんでした。", { kind: "error" }); });
    }
    function importDialog(body, summary) {
      var missing = summary.resources.filter(function (r) { return !r.exists; });
      var errors = h("p", { class: "reason", role: "alert" });
      var existing = summary.existing, choices = summary.choices || [];
      var box = h("div", { class: "modal template-import-modal", role: "dialog", "aria-modal": "true", "aria-label": "デザインテンプレートのインポート" },
        text("h3", "デザインテンプレートのインポート"),
        text("p", "「" + summary.name + "」（" + summary.modes.map(modeLabel).join("・") + "）を、" + brand.name + "に取り込みます。"),
        existing ? text("p", "同じテンプレート「" + existing.name + "」（v" + existing.revision + "）がすでにあります。", "small") : null,
        summary.resources.length ? h("ul", { class: "small template-import__resources" }, summary.resources.map(function (r) {
          return h("li", null, text("span", r.name + "："), text("span", r.exists ? "見つかりました" : "このパソコンで見つかりません", r.exists ? "muted" : "template-row__missing"));
        })) : null,
        missing.length ? text("p", "見つからないリソースが " + missing.length + " 件あります。取り込んだあとに、リソースのパスを直してください。", "small") : null,
        errors);
      var backdrop = h("div", { class: "modal-backdrop" }, box), busy = false;
      function close() { document.removeEventListener("keydown", onKey, true); backdrop.remove(); }
      function onKey(e) { if (e.key === "Escape") { e.preventDefault(); close(); } }
      function run(mode) {
        if (busy) { return; }
        busy = true; errors.textContent = "";
        SS.api(BASE + "/import", { method: "POST", body: { text: body, brand_id: brand.id, mode: mode,
          expected_revision_id: mode === "new_version" && existing ? existing.current_revision_id : undefined } }).then(function (result) {
          close();
          SS.toast("「" + result.document.name + "」を v" + result.document.revision + " として取り込みました。");
          load();
        }).catch(function (error) { busy = false; errors.textContent = error && error.message ? error.message : "取り込めませんでした。"; });
      }
      var actions = [button("やめる", close)];
      if (choices.indexOf("add") >= 0) { actions.push(button("別のテンプレートとして追加", function () { run("add"); }, choices.indexOf("create") < 0 && choices.indexOf("new_version") < 0 ? "btn--primary" : "")); }
      if (choices.indexOf("new_version") >= 0 && existing) { actions.push(button("v" + (existing.revision + 1) + " として取り込む", function () { run("new_version"); }, "btn--primary")); }
      if (choices.indexOf("create") >= 0) { actions.push(button("取り込む", function () { run("create"); }, "btn--primary")); }
      box.appendChild(h("div", { class: "modal-actions template-import__actions" }, actions));
      backdrop.addEventListener("click", function (e) { if (e.target === backdrop) { close(); } });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(backdrop);
      actions[actions.length - 1].focus();
    }

    load();
    return node;
  }

  // ---- 編集画面 ----
  // 編集画面の通信。保存は「いま読んだ版の ID」と一緒に送り、別の画面で先に更新されていればサーバが 409 で断る（自動では読み直さない）
  function adapter(templateId, state) {
    var url = BASE + "/" + templateId;
    return {
      save: function (p) {
        var body = { document: p.document, mode: p.mode, expected_revision_id: state.token };
        if (p.source && p.source.revision_id) { body.source_revision_id = p.source.revision_id; }
        return SS.api(url, { method: "POST", body: body }).then(function (result) {
          state.token = result.current_revision_id;
          return { document: result.document, action: result.action, message: result.message, persistent: true };
        });
      },
      revisions: function () { return SS.api(url + "/revisions").then(function (result) { return result.revisions; }); },
      pick: function (kind) { return SS.api("/api/settings/resource-path/pick?kind=" + encodeURIComponent(kind), { method: "POST" }); },
      compiled: function (mode, revision) { return SS.api(url + "/compiled?mode=" + encodeURIComponent(mode) + "&revision=" + revision); },
      exportUrl: function (mode, revision) { return url + "/compiled/export?mode=" + encodeURIComponent(mode) + "&revision=" + revision; }
    };
  }

  function notFound(root, brandId) {
    SS.clear(root).appendChild(h("div", { class: "settings-page" },
      h("div", { class: "notice notice--error", role: "alert" }, text("p", "このテンプレートは見つかりません。ブランドの画面から選び直してください。")),
      h("p", null, h("a", { href: "/settings/brands/" + brandId, "data-nav": true, text: "← ブランドの画面" }))));
  }

  var unloadGuard = null;
  function screen(root, catalog, route, guard) {
    var brand = catalog.brands.filter(function (entry) { return entry.id === route.brandId; })[0] || null;
    if (!brand) {
      SS.clear(root).appendChild(h("div", { class: "settings-page" },
        h("div", { class: "notice notice--error", role: "alert" }, text("p", "このブランドは見つかりません。一覧から選び直してください。")),
        h("p", null, h("a", { href: "/settings/brands", "data-nav": true, text: "← ブランドの一覧" }))));
      return Promise.resolve();
    }
    return SS.api(BASE + "/" + route.templateId).then(function (view) {
      if (!guard()) { return; }
      if (view.brand_id !== brand.id || view.deleted_at) { notFound(root, brand.id); return; }
      var item = JSON.parse(JSON.stringify(view.document)), state = { token: view.current_revision_id }, unsaved = false;
      // 保存していない編集があるうちは、画面を移るとき（SS.navigate・戻る）とタブを閉じるときに確かめる。
      // タブを閉じる確認は、いま開いている編集画面の分を1つだけ持つ（前の編集画面の分は外す）
      if (unloadGuard) { window.removeEventListener("beforeunload", unloadGuard); }
      unloadGuard = function (event) {
        if (!document.querySelector(".template-editor")) { window.removeEventListener("beforeunload", unloadGuard); return; }
        if (unsaved) { event.preventDefault(); event.returnValue = ""; }
      };
      window.addEventListener("beforeunload", unloadGuard);
      SS.designTemplateEditor(root, { item: item, brand: { id: brand.id, name: brand.name }, brandHref: "/settings/brands/" + brand.id, brandNav: true,
        io: adapter(route.templateId, state),
        onDirty: function (value) { unsaved = value; SS.settingsLeave = value ? { dirty: function () { return unsaved; } } : null; },
        onSave: function () {} });
    }, function (error) {
      if (!guard()) { return; }
      if (error && error.status === 404) { notFound(root, brand.id); return; }
      throw error;
    });
  }

  SS.designTemplates = { section: section, screen: screen };
})();
