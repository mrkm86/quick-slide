/* デザインテンプレートの全入力と、保存した文書から生成した指示の確認。 */
(function () {
  "use strict";
  var SS = window.SS, h = SS.h;
  function text(tag, value, cls) { return h(tag, { text: value, class: cls }); }
  function button(label, fn, attrs) { return h("button", Object.assign({ type: "button", class: "btn btn--small", onclick: fn, text: label }, attrs || {})); }
  function empty(id, brand, name) {
    return { schema_version: 3, id: id, brand_id: brand, revision: 0, based_on: null, name: name || "", description: "", modes: ["presentation"], structure: "", instructions: "", resources: [], attachments: [] };
  }
  SS.newDesignTemplate = empty;
  // 旧形式の保存文書はサーバが移して渡す。ここで移すのは画面案に埋め込んだ見本だけ。
  SS.normalizeDesignTemplate = function (item, brandId) {
    if (item.schema_version === 3) { return item; }
    var d = empty(item.id, brandId, item.name);
    d.modes = item.modes.map(function (m) { return m === "ピッチ" ? "pitch" : "presentation"; });
    d.description = item.description || "";
    d.instructions = [item.brief, item.drawing].filter(Boolean).join("\n\n");
    return d;
  };
  // 見出し・箇条書き・番号付きの箇条書き・表・太字・`記号囲み`・段落だけを描く。HTML としては解釈せず、文字として入れる。
  // Markdown で書かれていなくても崩れないよう、1つの改行はそのまま改行として描く。
  function renderMarkdown(source) {
    var lines = source.replace(/\r\n?/g, "\n").split("\n"), out = [], i = 0;
    var heading = /^(#{1,6})\s+(.*)$/, bullet = /^\s*[-*・]\s+(.*)$/, numbered = /^\s*\d+[.)．]\s+(.*)$/, row = /^\s*\|/;
    function code(value) {
      return value.split(/(`[^`]+`)/).filter(Boolean).map(function (part) {
        return /^`[^`]+`$/.test(part) ? h("code", { text: part.slice(1, -1) }) : part;
      });
    }
    // 太字の中に `記号囲み` があってもよいように、太字を先に分ける
    function inline(value) {
      return value.split(/(\*\*[^*]+\*\*)/).filter(Boolean).map(function (part) {
        return /^\*\*[^*]+\*\*$/.test(part) ? h("strong", null, code(part.slice(2, -2))) : code(part);
      });
    }
    function cells(line) {
      // 両端の | を外して区切る。`\|` は区切りにしない
      return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(function (c) { return c.trim().replace(/\\\|/g, "|"); });
    }
    function table() {
      var rows = [];
      while (i < lines.length && row.test(lines[i])) { rows.push(lines[i]); i++; }
      var head = rows.length > 1 && /^\s*\|?\s*:?-{3,}/.test(rows[1]) ? cells(rows[0]) : null;
      var body = rows.slice(head ? 2 : 0).map(cells);
      // スマホでは幅が足りないので、表だけを横にずらして読む（画面全体ははみ出させない）
      return h("div", { class: "template-md-table", tabindex: "0", role: "region", "aria-label": "表" }, h("table", null,
        head ? h("thead", null, h("tr", null, head.map(function (c) { return h("th", null, inline(c)); }))) : null,
        h("tbody", null, body.map(function (r) { return h("tr", null, r.map(function (c) { return h("td", null, inline(c)); })); }))));
    }
    function list(tag, pattern) {
      var node = h(tag);
      while (i < lines.length && pattern.test(lines[i])) { node.appendChild(h("li", null, inline(pattern.exec(lines[i])[1]))); i++; }
      return node;
    }
    while (i < lines.length) {
      var line = lines[i], m;
      if (!line.trim()) { i++; continue; }
      if ((m = heading.exec(line))) { out.push(h("h" + Math.min(m[1].length + 2, 6), null, inline(m[2]))); i++; continue; }
      if (bullet.test(line)) { out.push(list("ul", bullet)); continue; }
      if (numbered.test(line)) { out.push(list("ol", numbered)); continue; }
      if (row.test(line)) { out.push(table()); continue; }
      var para = h("p");
      while (i < lines.length && lines[i].trim() && !heading.test(lines[i]) && !bullet.test(lines[i]) && !numbered.test(lines[i]) && !row.test(lines[i])) {
        if (para.firstChild) { para.appendChild(h("br")); }
        SS.append(para, inline(lines[i])); i++;
      }
      out.push(para);
    }
    return h("div", { class: "template-md" }, out);
  }
  // フォルダ・ファイルの札と「戻す」のアイコン（Material Symbols Outlined の folder・draft・refresh）。共通のアイコン集には無いので、ここだけで持つ。
  var KIND_ICONS = {
    directory: "M160-160q-33 0-56.5-23.5T80-240v-480q0-33 23.5-56.5T160-800h240l80 80h320q33 0 56.5 23.5T880-640v400q0 33-23.5 56.5T800-160H160Zm0-80h640v-400H447l-80-80H160v480Zm0 0v-480 480Z",
    // 「1手戻る」ではなく「保存済みに戻す」なので、ぐるりと一周する矢印にする
    restore: "M480-160q-134 0-227-93t-93-227q0-134 93-227t227-93q69 0 132 28.5T720-690v-110h80v280H520v-80h168q-32-56-87.5-88T480-720q-100 0-170 70t-70 170q0 100 70 170t170 70q77 0 139-44t87-116h84q-28 106-114 173t-196 67Z",
    file: "M240-80q-33 0-56.5-23.5T160-160v-640q0-33 23.5-56.5T240-880h320l240 240v480q0 33-23.5 56.5T720-80H240Zm280-520v-200H240v640h480v-440H520ZM240-800v200-200 640-640Z"
  };
  function localIcon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    svg.setAttribute("viewBox", "0 -960 960 960"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("class", "icon");
    path.setAttribute("d", KIND_ICONS[name]); svg.appendChild(path);
    return svg;
  }
  function kindTag(kind) {
    return h("span", { class: "template-row__kind template-row__kind--" + (kind === "file" ? "file" : "directory") }, localIcon(kind === "file" ? "file" : "directory"), kind === "file" ? "ファイル" : "フォルダ");
  }
  var showDebug = /(^|[?&])debug(=|&|$)/.test(location.search);
  SS.designTemplateEditor = function (root, options) {
    var item = options.item, d = JSON.parse(JSON.stringify(options.called || item)), changed = false, editSerial = 0, compileSerial = 0;
    d.revision = item.revision;
    // 保存は今の版の上書き。新しい版は保存ボタンの ▾ の「v5 として保存」で作る。
    // 前の版は「版の一覧」でコピーして新しい版を作れる。コピーしている間（calledFrom）は、保存すると新しい版になり、最新の版は変わらない。
    var calledFrom = options.calledFrom || null, calledRow = options.calledRow || null;
    // 通信と戻り先は呼ぶ側が渡す（本番は SS.api と本番の経路、検証用の画面案はプレビューサーバ）
    var io = options.io;
    var state = text("span", "", "small muted"), errors = h("div", { role: "alert" });
    // ページの版と同じく「v4」の札で見せ、押すと版を見られる。
    var version = h("button", { type: "button", class: "chip page__ver template-ver", onclick: openVersions });
    // 保存していない編集（と前の版のコピー）を取り消して、最新の版の保存済みの内容に戻す。未保存の変更があるときだけ出す
    var revert = h("button", { type: "button", class: "btn btn--small btn--ghost template-revert", hidden: true, onclick: function () {
      if (!window.confirm((calledFrom ? "v" + (item.revision + 1) + " の作成をやめて、" : "保存していない変更を取り消して、") + "v" + item.revision + " の保存済みの内容に戻します。")) { return; }
      options.onDirty(false);
      SS.designTemplateEditor(root, Object.assign({}, options, { called: null, calledFrom: null, calledRow: null }));
      SS.toast("v" + item.revision + " の保存済みの内容に戻しました。");
    } }, localIcon("restore"), text("span", "戻す"));
    var save = button("保存する", function () { saveDocument(false); }, { class: "btn btn--primary template-save-main" });
    var saveMore = h("button", { type: "button", class: "btn btn--primary template-save-more", "aria-haspopup": "menu", "aria-label": "保存のしかたを選ぶ", title: "保存のしかたを選ぶ",
      onclick: function () {
        SS.menu(saveMore, [{ label: "v" + item.revision + " を上書き保存", onSelect: function () { saveDocument(false); } },
          { label: "v" + (item.revision + 1) + " として保存（新しい版）", onSelect: function () { saveDocument(true); } }]);
      } }, SS.icon("expand_more"));
    function showVersion() {
      var next = item.revision + 1;
      version.textContent = calledFrom ? "v" + next + " 作成中" : "v" + item.revision; version.hidden = !item.revision;
      version.classList.toggle("template-ver--new", !!calledFrom);
      version.setAttribute("aria-label", "版を見る（" + (calledFrom ? "v" + calledFrom + " をコピーして v" + next + " を作成中" : "いまは v" + item.revision) + "）");
      save.textContent = calledFrom ? "v" + next + " として保存" : "保存する";
      // まだ一度も保存していないとき・前の版をコピーして作っているときは、上書きを選ばせない
      saveMore.hidden = !item.revision || !!calledFrom; save.classList.toggle("template-save-main", !saveMore.hidden);
    }
    showVersion();
    var delivery = h("aside", { class: "template-delivery" }), page;
    function dirty() { editSerial++; changed = true; revert.hidden = !item.revision; state.textContent = "未保存の変更があります"; options.onDirty(true); delivery.dataset.stale = "true"; }
    function control(label, value, setter, multi, help) {
      var node = h(multi ? "textarea" : "input", { type: multi ? null : "text", rows: multi ? 4 : null, oninput: function (e) { setter(e.target.value); dirty(); } }); node.value = value;
      return h("label", { class: "template-field" }, text("span", label), help ? text("span", help, "small muted") : null, node);
    }
    // 説明は欄の下に並べず、見出しの横の「？」に畳む。
    function tipControl(label, tip, value, setter) {
      var node = h("input", { type: "text", "aria-label": label, oninput: function (e) { setter(e.target.value); dirty(); } }); node.value = value;
      return h("div", { class: "template-field" }, h("div", { class: "title-row" }, text("span", label), SS.helpTip(label, [tip])), node);
    }
    function select(label, value, values, setter, help) {
      var node = h("select", { onchange: function (e) { setter(e.target.value); dirty(); } }, values.map(function (v) { return h("option", { value: v[0], text: v[1] }); })); node.value = value;
      return h("label", { class: "template-field" }, text("span", label), help ? text("span", help, "small muted") : null, node);
    }
    function check(label, value, setter) {
      return h("label", { class: "template-check" }, h("input", { type: "checkbox", checked: value, onchange: function (e) { setter(e.target.checked); dirty(); } }), text("span", label));
    }
    function panel(id, title, help, children, aside) {
      return h("section", { class: "template-panel template-section", id: "template-" + id },
        h("div", { class: "template-section__head" }, text("h2", title), aside || null), help ? text("p", help, "small muted") : null, children);
    }
    // 見出しの右上に置く「書き方の例」。押したときだけ例を出し、そのままコピーして欄に貼れるようにする。
    function guide(id, topic, example) {
      var pre = text("pre", example);
      var copy = h("button", { type: "button", class: "btn btn--icon btn--ghost template-guide__copy", "aria-label": topic + "の例をコピー", title: "コピー" },
        SS.icon("content_copy"));
      copy.addEventListener("click", function () {
        SS.copyText(example).then(function (ok) {
          // 写したら畳んで、欄に貼る作業へ戻す
          if (ok) { SS.toast(topic + "の例をコピーしました。"); card.hidden = true; toggle.setAttribute("aria-expanded", "false"); toggle.focus(); return; }
          var range = document.createRange(); range.selectNodeContents(pre);
          var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
          SS.toast("コピーできませんでした。選んだ文字を ⌘C（Windows は Ctrl+C）でコピーしてください。", { kind: "error" });
        });
      });
      var card = h("div", { class: "template-guide", id: "template-guide-" + id, hidden: true },
        h("div", { class: "template-guide__head" }, text("span", "書き方の例"), copy), pre,
        text("p", "「##」で見出し、「-」で箇条書きにできます。分けて書くと、読み返しやすく、AI にも伝わりやすくなります。", "small muted"));
      var toggle = guideToggle(card);
      return { toggle: toggle, card: card };
    }
    // 見出しの右上の「？」。押すたびに例を開け閉めする。
    function guideToggle(card) {
      var toggle = h("button", { type: "button", class: "btn btn--icon btn--ghost template-guide__toggle", "aria-expanded": "false", "aria-controls": card.id,
        "aria-label": "書き方の例", title: "書き方の例", onclick: function () { card.hidden = !card.hidden; toggle.setAttribute("aria-expanded", card.hidden ? "false" : "true"); } },
        SS.icon("help"));
      return toggle;
    }
    function removeButton(label, fn) {
      return h("button", { type: "button", class: "btn btn--icon btn--ghost template-row__remove", "aria-label": label, title: label, onclick: fn }, SS.icon("delete"));
    }
    // 各行は普段は畳んだカード（summary）で見せ、鉛筆でその行だけ開いて編集する。足したばかりの行は開いておく。
    // 開いているかどうかは画面だけの状態で、保存する文書には入れない。
    function rowsPanel(list, title, draw, fresh, prefix, summary) {
      var body = h("div", { class: "template-rows" }), open = new Set();
      function redraw(focusRow) {
        SS.clear(body);
        list.forEach(function (row, i) {
          var editing = open.has(row), label = row.name.trim() || title + " " + (i + 1);
          var toggle = h("button", { type: "button", class: "btn btn--icon btn--ghost template-mode", "aria-expanded": editing ? "true" : "false",
            "aria-label": label + (editing ? "をたたむ" : "を編集"), title: editing ? "たたむ" : "編集",
            onclick: function () { if (editing) { open.delete(row); } else { open.add(row); } redraw(row); } }, SS.icon(editing ? "expand_less" : "edit"));
          // 並び順に意味はないので、入れ替えの操作は置かない。消しても保存しなければ元に戻る。
          var card = h("div", { class: "template-row" + (editing ? " template-row--open" : "") },
            h("div", { class: "template-row__head" }, text("span", label, "template-row__title"),
              h("div", { class: "template-row__tools" }, toggle, removeButton(label + "を削除", function () { open.delete(row); list.splice(i, 1); dirty(); redraw(); }))),
            editing ? draw(row) : summary(row));
          card.toggle = toggle;
          body.appendChild(card);
          if (row === focusRow) { setTimeout(function () { (editing ? card.querySelector("input,select,textarea") : toggle).focus(); }, 0); }
        });
        if (!list.length) { body.appendChild(text("p", "まだありません。", "small muted")); }
      }
      function add(values) { var row = Object.assign(fresh(prefix + "-" + Date.now().toString(36)), values || {}); list.push(row); open.add(row); dirty(); redraw(row); }
      redraw();
      // スライドの「＋ページを追加」と同じ部品で、一覧の最後に置く。
      var node = h("div", null, body, h("div", { class: "gap gap--end" },
        h("button", { type: "button", class: "gap__add", text: "＋" + title + "を追加", onclick: function () { add(); } })));
      node.addRow = add;
      // 保存時のエラーで欄に飛ぶときは、全部の行を開く
      node.openAll = function () { list.forEach(function (row) { open.add(row); }); redraw(); };
      node.redraw = function () { redraw(); };
      return node;
    }
    var modes = h("div", { class: "template-field" }, text("span", "使える形式"), h("div", { class: "template-checks" },
      [["presentation", "プレゼン"], ["pitch", "ピッチ"]].map(function (entry) { return check(entry[1], d.modes.includes(entry[0]), function (v) { d.modes = d.modes.filter(function (m) { return m !== entry[0]; }); if (v) { d.modes.push(entry[0]); } }); })));
    // 見出しは panel の1つだけ。欄には見える名前を付けず、読み上げ用の名前だけ持たせる。
    // 中身があれば描いた状態で見せ、鉛筆で書く状態にする。空なら最初から書く状態（プレースホルダーを見せる）。
    function area(label, value, setter) {
      var input = h("textarea", { "aria-label": label, rows: 18, oninput: function (e) { setter(e.target.value); dirty(); } }); input.value = value;
      var view = h("div", { class: "template-md-view", role: "region", "aria-label": label });
      var toggle = h("button", { type: "button", class: "btn btn--icon btn--ghost template-mode" });
      var field = h("div", { class: "template-field" }, view, input);
      function show(editing) {
        input.hidden = !editing; view.hidden = editing;
        if (!editing) { SS.clear(view).appendChild(renderMarkdown(input.value)); }
        SS.clear(toggle).appendChild(SS.icon(editing ? "eye" : "edit"));
        toggle.setAttribute("aria-label", editing ? label + "を見る" : label + "を編集"); toggle.title = editing ? "見る" : "編集";
      }
      toggle.addEventListener("click", function () { var editing = input.hidden; show(editing); (editing ? input : toggle).focus(); });
      input.showEditor = function () { show(true); };
      show(!value.trim());
      field.toggle = toggle;
      return field;
    }
    var additional = area("追加指示", d.instructions, function (v) { d.instructions = v; });
    additional.querySelector("textarea").placeholder = "## やること\n\n## やらないこと（代わりにどうするかも添える）\n\n迷ったら右上の「書き方の例」を見てください。";
    var instructionsGuide = guide("instructions", "追加指示", "## やること\n1ページで伝えることは1つにする。見出しは結論を短い文で書く。\n白地を広く取り、ブランドの基本色は見出しと図に使う。\n「イラスト素材」から、内容に合う画像を選んで使う。\n開催日が原稿にない場合は、作る前に確認する。\n\n## やらないこと\n原稿にない数字・日付・事実を作らない（足りないときは「要確認」と書く）。\n写真調にしない（平らな挿絵にする）。\n図や表が主役のページに挿絵を入れない。");
    var structureExample = "## 表紙\n題材と、この資料で伝えたいこと。\n\n## 課題と提案\n現状の課題と、解決の方向を示す。\n\n## 補足の主張（必要な場合だけ）\nほかのページに埋もれる重要な主張があるときに追加する。\n\n## 最後に聞きたいこと\n相手に判断してほしいことを1〜3個に絞る。";
    var structure = area("構成", d.structure, function (v) { d.structure = v; });
    var structureInput = structure.querySelector("textarea");
    structureInput.classList.add("template-structure-input"); structureInput.placeholder = structureExample;
    var structureGuide = guide("structure", "構成", structureExample);
    // このパソコンで見つからないリソース（保存した版で確かめた結果。{リソースの ID: そのときのパス}）。保存と読み込みのあとに更新する
    var missing = {};
    var refs = rowsPanel(d.resources, "リソース", function (row) {
      var path = control("このパソコン上のパス", row.path, function (v) { row.path = v; }, false, "絶対パス、または ~/ から始まるパスを指定します。");
      path.querySelector("input").placeholder = row.kind === "file" ? "例：~/Documents/company-profile.md" : "例：~/Pictures/slide-images";
      var kind = h("select", { "aria-label": "リソースの種類", onchange: function (e) { row.kind = e.target.value; dirty(); } },
        [["directory", "フォルダ"], ["file", "ファイル"]].map(function (v) { return h("option", { value: v[0], text: v[1] }); })); kind.value = row.kind;
      // ブラウザのファイル選択ではフルパスが取れないので、サーバのパソコンでダイアログを開く。
      var pick = button("選ぶ…", function () {
        pick.disabled = true;
        io.pick(row.kind).then(function (result) {
          if (result.cancelled || !page.isConnected) { return; }
          row.path = result.path; path.querySelector("input").value = result.path; dirty();
        }).catch(function (error) { error.section = "resources"; error.control = path.querySelector("input"); fail(error); })
          .finally(function () { pick.disabled = false; });
      }, { "aria-label": "ダイアログでパスを選ぶ" });
      return [
        tipControl("名前", "追加指示や構成から、この名前で呼べます。ほかのリソースと異なる名前にしてください。", row.name, function (v) { row.name = v; }),
        h("div", { class: "template-field" }, text("span", "種類"), h("div", { class: "template-pick" }, kind, pick)),
        path,
        control("使い方（任意）", row.usage, function (v) { row.usage = v; }, true, "追加指示にまとめて書いてもかまいません。")];
    }, function (id) { return { id: id, name: "", kind: "directory", path: "", usage: "" }; }, "res", function (row) {
      var where = row.path.trim().replace(/\/+$/, "").split("/").pop();
      return h("div", { class: "template-row__summary" },
        h("p", { class: "small template-row__where", title: row.path }, kindTag(row.kind),
          where ? text("span", where) : text("span", "パスが未指定です", "template-row__missing"),
          where && missing[row.id] === row.path ? text("span", "見つかりません", "template-row__missing") : null),
        row.usage.trim() ? text("p", row.usage.trim(), "small muted template-row__usage") : null);
    });
    // リソースは欄が分かれているので、例は写すのではなく、名前・種類・使い方の入った行として足す（パスは各自で選ぶ）。
    var resourceExamples = [
      { name: "画像フォルダ", kind: "directory", where: "~/Pictures/slide-images",
        usage: "中の目次（README.md）を見て、ページの内容に最適な画像を1枚選ぶ。目次にない画像は使わない。合う画像がなければ画像なしにする。" },
      { name: "ファクトチェック", kind: "directory", where: "~/.claude/skills/fact-check",
        usage: "中の SKILL.md の手順で、台本の数字・固有名詞・日付を確かめる。確かめられないものは削らずに「要確認」と書き添える。" },
      { name: "会社概要", kind: "file", where: "~/Documents/company-profile.md",
        usage: "会社を紹介するページは、このファイルの内容だけで書く。載っていない実績や数字は足さない。" }];
    var resourceGuideCard = h("div", { class: "template-guide", id: "template-guide-resources", hidden: true },
      h("div", { class: "template-guide__head" }, text("span", "書き方の例")),
      resourceExamples.map(function (ex) {
        return h("div", { class: "template-guide__example" },
          h("dl", null, text("dt", "名前"), text("dd", ex.name), text("dt", "種類"), text("dd", ex.kind === "file" ? "ファイル" : "フォルダ"),
            text("dt", "パスの例"), text("dd", ex.where), text("dt", "使い方"), text("dd", ex.usage)),
          button("この例を追加", function () {
            // 例を畳んでから足す（足した行が開いて、名前の欄に移る）
            resourceGuideCard.hidden = true; resourceGuideToggle.setAttribute("aria-expanded", "false");
            refs.addRow({ name: ex.name, kind: ex.kind, usage: ex.usage });
            SS.toast("「" + ex.name + "」を追加しました。パスを指定してください。");
          }, { "aria-label": "「" + ex.name + "」の例をリソースに追加" }));
      }),
      text("p", "追加指示から「画像フォルダ」のように名前で呼べます。例は保存されません。", "small muted"));
    var resourceGuideToggle = guideToggle(resourceGuideCard);
    var materials = h("div");
    // 添付は前の画面で取り込んだものを残すだけ。新しいファイルは、種類「ファイル」のリソースとしてパスで指定する。
    function redrawMaterials() {
      SS.clear(materials);
      if (d.attachments.length) { materials.appendChild(text("h3", "添付済みのファイル")); }
      d.attachments.forEach(function (row, i) {
        materials.appendChild(h("fieldset", { class: "template-row" }, text("legend", row.file),
          removeButton(row.file + "を外す", function () { d.attachments.splice(i, 1); dirty(); redrawMaterials(); }),
          row.image ? h("img", { class: "template-material-image", src: row.url, alt: row.name }) : null,
          tipControl("名前", "追加指示や構成から、この名前で呼べます。ほかのリソースと異なる名前にしてください。", row.name, function (v) { row.name = v; }),
          control("使い方（任意）", row.usage, function (v) { row.usage = v; }, true)));
      });
    }
    redrawMaterials();
    function fail(error) {
      SS.clear(errors).appendChild(text("p", error.message, "notice notice--error"));
      var message = error.message, section = error.section || (/リソース|添付/.test(message) ? "resources" : /構成/.test(message) ? "structure" : /追加指示/.test(message) ? "instructions" : /名前|テンプレート名|形式|用途/.test(message) ? "basic" : null);
      if (section && panelNodes[section]) {
        activateTab(section);
        if (section === "resources" && !error.control) { refs.openAll(); }
        var target = error.control || panelNodes[section].querySelector("input,textarea,select");
        if (target) { var detail = target.closest("details"); if (detail) { detail.open = true; } }
        if (target && target.showEditor) { target.showEditor(); }
        if (target) { target.focus(); }
      }
      errors.scrollIntoView({ block: "nearest" });
    }
    // asNew: 今の内容を最新の次の版として保存する（▾ の「新しい版として保存」）
    async function saveDocument(asNew) {
      var from = asNew ? item.revision : calledFrom;
      save.disabled = true; SS.clear(errors);
      var submittedSerial = editSerial;
      try {
        Object.keys(panelNodes).forEach(function (section) {
          panelNodes[section].querySelectorAll("input:not([type=file]),textarea:not([readonly])").forEach(function (control) {
            if (control.value.length > 60000) {
              var error = new Error("この項目は60,000文字以内で入力してください。"); error.section = section; error.control = control; throw error;
            }
          });
        });
        var resourceNames = d.resources.concat(d.attachments).map(function (r) { return r.name.trim(); });
        if (new Set(resourceNames).size !== resourceNames.length) { var error = new Error("リソースの名前は重複しないようにしてください。"); error.section = "resources"; throw error; }
        var result = await io.save({ document: Object.assign({}, d, { based_on: from }), mode: from ? "new_version" : "overwrite", source: asNew ? null : calledRow });
        calledRow = null;
        d.revision = result.document.revision; d.based_on = result.document.based_on; calledFrom = null;
        Object.assign(item, JSON.parse(JSON.stringify(result.document))); changed = editSerial !== submittedSerial;
        if (!page.isConnected) { return; }
        options.onDirty(changed);
        state.textContent = changed ? "未保存の変更があります" : ""; revert.hidden = !changed; showVersion();
        SS.toast(result.action === "unchanged" && result.message ? result.message : "v" + item.revision + " を保存しました。" + (result.persistent === false ? "サーバを止めると消えます。" : ""));
        options.onSave(item); checkResources(); await showCompiled();
      } catch (error) { fail(error); } finally { save.disabled = false; }
    }
    async function showCompiled(mode) {
      if (!showDebug) { return; }
      var ownCompile = ++compileSerial;
      if (!item.revision) { SS.clear(delivery).appendChild(panel("delivery-content", "保存した内容を渡す", "まずテンプレートを保存してください。", text("p", "保存した内容から、台本係・素材準備・描画係へ渡す指示を共通の処理で生成します。"))); return; }
      mode = mode || item.modes[0];
      try {
        var revision = item.revision;
        var result = await io.compiled(mode, revision);
        if (!page.isConnected || ownCompile !== compileSerial || revision !== item.revision) { return; }
        SS.clear(delivery); delivery.dataset.stale = changed ? "true" : "false";
        var modeSelect = h("select", { "aria-label": "渡す内容の形式", onchange: function (e) { showCompiled(e.target.value); } }, item.modes.map(function (m) { return h("option", { value: m, text: m === "pitch" ? "ピッチ" : "プレゼン" }); })); modeSelect.value = mode;
        var output = h("textarea", { readonly: true, rows: 14, "aria-label": "保存内容から生成した指示", class: "template-output" });
        var choices = h("select", { "aria-label": "確認する指示" }, [["writer.md", "台本係へ渡す指示"], ["preparation.md", "リソースを読む・素材を選ぶ指示"], ["style.yaml", "描画係へ渡すスタイル"], ["engine-input.json", "描画設定とブランドの継承"]].filter(function (entry) { return result.files[entry[0]]; }).map(function (entry) { return h("option", { value: entry[0], text: entry[1] }); }));
        function update() { output.value = result.files[choices.value]; } choices.addEventListener("change", update); update();
        var refsStatus = result.resources.map(function (r) { return text("li", r.name + "：" + (!r.exists ? "見つかりません。パスを直すまで素材準備へ進めません" : r.guide ? "見つかりました。中の " + r.guide + " を先に読みます" : "見つかりました")); });
        delivery.appendChild(panel("delivery-content", "保存した内容から生成した指示", "Quick Slide とエンジン直接実行で、同じ入力を使うための確認です。", [
          text("p", "v" + result.template_revision + " の内容", "small muted"), text("p", "未保存の編集は、ここには反映されません。", "small template-stale-note"), modeSelect, choices, output,
          text("p", "指示を組み立てた段階です。台本作成・素材選択・描画は実行していません。", "small muted"),
          h("ul", { class: "small" }, refsStatus), h("a", { class: "btn btn--small", href: io.exportUrl(mode, revision), download: true, text: "共通の指示ファイルを書き出す" }),
          h("details", null, text("summary", "引き渡しの状態"), text("p", result.can_prepare ? "リソースはすべて見つかりました。素材準備は未実行です。" : "見つからないリソースがあります。素材準備へ進めません。"),
            result.notes.map(function (n) { return text("p", n, "small muted"); }), text("code", result.contract_sha256, "small"))]));
      } catch (error) { if (page.isConnected && ownCompile === compileSerial) { SS.clear(delivery).appendChild(panel("delivery-content", "指示を生成できません", error.message)); } }
    }
    // スマホの幅に4つ並ぶよう、タブの名前は短くする。中の見出し・説明・AI への指示も同じ呼び名にそろえる
    var tabDefinitions = [["basic", "基本"], ["structure", "構成"], ["instructions", "追加指示"], ["resources", "リソース"]];
    var activeTab = "basic", panelNodes = {}, tabButtons = {};
    var position = text("span", "", "small muted"); position.setAttribute("aria-live", "polite");
    var previous = button("前の項目", function () { changeTab(-1); });
    var next = button("次の項目", function () { changeTab(1); });
    var nav = h("div", { class: "template-tabs", role: "tablist", "aria-label": "テンプレートの設定項目" });
    function activateTab(id, focusTab) {
      activeTab = id;
      tabDefinitions.forEach(function (entry) {
        var selected = entry[0] === id;
        tabButtons[entry[0]].setAttribute("aria-selected", selected ? "true" : "false");
        tabButtons[entry[0]].tabIndex = selected ? 0 : -1;
        panelNodes[entry[0]].hidden = !selected;
      });
      var index = tabDefinitions.findIndex(function (entry) { return entry[0] === id; });
      position.textContent = (index + 1) + " / " + tabDefinitions.length + " · " + tabDefinitions[index][1];
      previous.disabled = index === 0; next.disabled = index === tabDefinitions.length - 1;
      if (focusTab) {
        tabButtons[id].focus({ preventScroll: true });
        tabButtons[id].scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }
    function changeTab(delta) {
      var index = tabDefinitions.findIndex(function (entry) { return entry[0] === activeTab; });
      activateTab(tabDefinitions[index + delta][0], true);
      nav.scrollIntoView({ block: "start" });
    }
    tabDefinitions.forEach(function (entry, i) {
      var node = h("button", { type: "button", id: "template-tab-" + entry[0], class: "template-tab", role: "tab", "aria-controls": "template-" + entry[0],
        onclick: function () { activateTab(entry[0], true); }, onkeydown: function (event) {
          var index = event.key === "ArrowRight" ? (i + 1) % tabDefinitions.length : event.key === "ArrowLeft" ? (i + tabDefinitions.length - 1) % tabDefinitions.length : event.key === "Home" ? 0 : event.key === "End" ? tabDefinitions.length - 1 : -1;
          if (index >= 0) { event.preventDefault(); activateTab(tabDefinitions[index][0], true); }
        }, text: entry[1] });
      tabButtons[entry[0]] = node; nav.appendChild(node);
    });
    page = h("div", { class: "settings-page template-editor" },
      h("nav", { class: "breadcrumb", "aria-label": "現在地" }, h("a", { href: options.brandHref || "#" + options.brandPath, "data-nav": options.brandNav ? true : null, text: options.brand.name }), " › デザインテンプレート › ", item.name),
      // 幅によって並びを変えない: 題名、注意、その下に「版と状態 … 保存」の1行
      h("div", { class: "template-head" }, text("h1", "デザインテンプレートの編集"),
        h("div", { class: "notice notice--info template-caution" }, text("p", "AI はここに書いた指示を、いつも守れるとは限りません。できあがった資料で、指示どおりになっているか確かめてください。")),
        h("div", { class: "template-toolbar" }, h("div", { class: "template-toolbar__status" }, version, state), h("div", { class: "template-toolbar__actions menu-anchor" }, revert, h("div", { class: "template-split" }, save, saveMore)))),
      nav, errors,
      h("div", { class: "template-tab-panels" },
        panel("basic", "基本", options.brand.name + "の色・ロゴ・字体を引き継ぎます。", [control("テンプレート名", d.name, function (v) { d.name = v; }), control("用途の説明", d.description, function (v) { d.description = v; }), modes]),
        panel("structure", "構成", "資料のページの並びと、各ページで伝えることを書きます。書き方は自由です。原稿でページの並びが決まっているときは、原稿のほうを優先します。", [structureGuide.card, structure], h("div", { class: "template-section__tools" }, structure.toggle, structureGuide.toggle)),
        panel("instructions", "追加指示", "文章の書き方も見た目も、ここにまとめて書きます。登録したリソースは「名前」で呼べます。", [instructionsGuide.card, additional], h("div", { class: "template-section__tools" }, additional.toggle, instructionsGuide.toggle)),
        panel("resources", "リソース", "参考資料や素材を、名前を付けて登録します。フォルダを登録すると、中の説明書き（README.md など）を先に読んでから必要なファイルを選びます。", [resourceGuideCard, refs, materials], resourceGuideToggle)),
      h("div", { class: "template-tab-footer" }, previous, position, next));
    // AI に渡す指示の原文は開発の確かめ用。普段の画面には出さず、URL に ?debug を付けたときだけ出す
    if (showDebug) { page.appendChild(h("details", { class: "template-debug" }, text("summary", "保存した指示を確認する（検証用）"), delivery)); }
      tabDefinitions.forEach(function (entry) {
      var node = page.querySelector("#template-" + entry[0]);
      node.setAttribute("role", "tabpanel"); node.setAttribute("aria-labelledby", "template-tab-" + entry[0]); node.tabIndex = 0;
      node.querySelectorAll("input,textarea,select").forEach(function (control) { control.dataset.section = entry[0]; });
      panelNodes[entry[0]] = node;
    });
    // 版を見る。版ごとの由来・変更・中身を見せるだけで、ここからは書き換えない。
    function openVersions() {
      var list = h("div", { class: "template-versions" }), detail = h("div", { class: "template-version-detail" });
      var close = h("button", { type: "button", class: "btn", text: "閉じる" });
      var box = h("div", { class: "modal template-versions-modal", role: "dialog", "aria-modal": "true", "aria-label": "版の一覧" },
        text("h3", "版の一覧"), list, detail, h("div", { class: "modal-actions" }, close));
      var backdrop = h("div", { class: "modal-backdrop" }, box);
      function shut() { document.removeEventListener("keydown", onKey, true); backdrop.remove(); version.focus(); }
      function onKey(e) { if (e.key === "Escape") { e.preventDefault(); shut(); } }
      close.addEventListener("click", shut);
      backdrop.addEventListener("click", function (e) { if (e.target === backdrop) { shut(); } });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(backdrop);
      list.appendChild(text("p", "読み込んでいます…", "small muted"));
      io.revisions().then(function (rows) {
        SS.clear(list);
        rows.forEach(function (row) {
          var latest = row.revision === item.revision;
          var note = row === rows[rows.length - 1] ? "最初の版" : row.changed.length ? "変更：" + row.changed.join("・") : "変更なし";
          var b = h("button", { type: "button", class: "template-version", "aria-pressed": "false", onclick: function () {
            list.querySelectorAll(".template-version").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
            showDetail(row, latest);
          } }, h("span", { class: "template-version__no", text: "v" + row.revision + (row.based_on ? "（v" + row.based_on + " から）" : "") + (latest ? "・最新" : "") }),
            text("span", row.saved_at ? SS.fmtDate(row.saved_at) : "", "small muted"), text("span", note, "small"));
          list.appendChild(b);
        });
        var first = list.querySelector(".template-version"); if (first) { first.click(); first.focus(); }
      }).catch(function (error) { SS.clear(list).appendChild(text("p", error.message, "notice notice--error")); });
      function showDetail(row, latest) {
        var doc = row.document;
        function block(label, value, markdown) {
          return h("div", null, text("h4", label), !value ? text("p", "（なし）", "small muted") : markdown ? h("div", { class: "template-md-view" }, renderMarkdown(value)) : text("pre", value));
        }
        // リソースは編集画面の畳んだカードと同じ見た目で、パスと使い方を全部見せる
        var resources = doc.resources.map(function (r) {
          return h("div", { class: "template-row" }, text("p", r.name, "template-row__title"),
            h("p", { class: "small template-row__where" }, kindTag(r.kind), text("span", r.path || "パスが未指定です", r.path ? "template-version__path" : "template-row__missing")),
            r.usage.trim() ? text("p", r.usage, "small template-version__usage") : null);
        }).concat(doc.attachments.map(function (r) {
          return h("div", { class: "template-row" }, text("p", r.name, "template-row__title"), text("p", "添付ファイル：" + r.file, "small muted"),
            r.usage.trim() ? text("p", r.usage, "small template-version__usage") : null);
        }));
        // 前の版は、中身より上の「コピーして v5 を作成」から編集できる（この版も最新の版も変わらない）
        var call = latest ? null : h("div", { class: "title-row template-version-call" },
          SS.helpTip("コピーして作成", ["v" + row.revision + " の中身をコピーして編集し、v" + (item.revision + 1) + " として保存します。v" + row.revision + " と v" + item.revision + " は変わりません。"]),
          h("button", { type: "button", class: "btn btn--primary", text: "コピーして v" + (item.revision + 1) + " を作成", onclick: function () {
            if (changed && !window.confirm("保存していない変更を取り消して、v" + row.revision + " をコピーします。")) { return; }
            shut();
            SS.designTemplateEditor(root, Object.assign({}, options, { called: doc, calledFrom: row.revision, calledRow: row }));
            SS.toast("v" + row.revision + " をコピーしました。「v" + (item.revision + 1) + " として保存」で v" + (item.revision + 1) + " ができます。");
          } }));
        // SS.append は null を飛ばす（Element.append は null を「null」という文字で入れてしまう）
        SS.append(SS.clear(detail), [call, text("p", "v" + row.revision + " の内容", "small muted"),
          block("基本", doc.name + (doc.description ? "\n" + doc.description : "")),
          block("構成", doc.structure, true), block("追加指示", doc.instructions, true),
          h("div", null, text("h4", "リソース"), resources.length ? h("div", { class: "template-version-resources" }, resources) : text("p", "（なし）", "small muted"))]);
      }
    }
    // 保存した版のリソースが、このパソコンにあるかを確かめて、見つからないカードに印を付ける（取り込んだファイルのパスは別のパソコンのもの）
    function checkResources() {
      if (!item.revision || !item.resources.length) { return; }
      var revision = item.revision;
      io.compiled(item.modes[0], revision).then(function (result) {
        if (!page.isConnected || revision !== item.revision) { return; }
        var next = {};
        (result.resources || []).forEach(function (r) {
          var saved = item.resources.filter(function (x) { return x.id === r.id; })[0];
          if (!r.exists && saved) { next[r.id] = saved.path; }
        });
        if (JSON.stringify(next) !== JSON.stringify(missing)) { missing = next; refs.redraw(); }
      }).catch(function () { /* 確かめられなければ印は出さない */ });
    }
    SS.clear(root).appendChild(page); activateTab("basic", false); showCompiled(); checkResources();
    if (calledFrom) { dirty(); state.textContent = "v" + calledFrom + " をコピーしました"; }
  };
})();
