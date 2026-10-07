// 縦スクロールの実況・編集画面（UI.md §6 の本実装。P6）。
// 左: サムネイル一覧（固定幅・独立スクロール・状態の印・件数） / 右: カードが縦 1 列（ヘッダ 1 行 / スライド + 版 / 修正指示 / 記録）/
// 一番上: デッキ全体への指示（スクロールで 1 行に縮む） / 読む情報は畳んだ器へ入れ、その場に展開する。
// P6 では生成・改稿はできない: 編集ボックスと「作り直す」は置くが無効化し、理由（API の capabilities.reason）を見せる（U7）。
// 状態名・件数はすべて API の display / counts をそのまま出す（U1 / U9 / U11）。
(function () {
  "use strict";
  var SS = window.SS;
  var h = SS.h;

  // 件数の 4 区分。並びだけを画面が持ち、表示名は API の counts.labels から取る（U9）
  var GROUP_ORDER = ["ready", "generating", "waiting", "failed"];
  function groupLabels(counts) { return (counts && counts.labels) || (SS.vocab && SS.vocab.groups) || {}; }

  // 並べ替えの語も画面では作らない（正本は server/vocabulary.py の REORDER_MESSAGES）
  function reorderWords() { return (SS.vocab && SS.vocab.reorder) || {}; }
  // カードの中の見出し語・版の語も同じ（正本は CARD_WORDS / VERSION_WORDS）
  function cardWords() { return (SS.vocab && SS.vocab.card) || {}; }
  function versionWords() { return (SS.vocab && SS.vocab.versions) || {}; }
  // 修正指示の欄の語も同じ（正本は INSTRUCTION_WORDS と送信の口の断りの表。すべて文字列）。
  function instructionWords() { return (SS.vocab && SS.vocab.instruction) || {}; }
  // 上限（文の長さ・添付の枚数）はサーバの定数がそのまま降ってくる。画面に数を書かない。
  // 語（文字列）とは型が違うので `vocabulary.instruction_limits()` という別の口から配る
  function instructionLimits() { return (SS.vocab && SS.vocab.instruction_limits) || {}; }
  // 隙間にページを足すときの語（P11。正本は INSERT_WORDS）。ボタンの語だけは
  // `capabilities.insert_page.label` から取る（押せるかどうかと同じ答えに乗せ、語と可否を食い違わせない）
  function insertWords() { return (SS.vocab && SS.vocab.insert) || {}; }
  // 書き出し（P9-b）の確認文・進行中・完了の語も同じ（正本は EXPORT_WORDS / EXPORT_FORMAT_LABELS）
  function exportWords() { return (SS.vocab && SS.vocab.export) || {}; }
  function exportFormatLabels() { return (SS.vocab && SS.vocab.export_formats) || {}; }
  // スライドの名前の長さの上限。home.js と同じ口から取る（画面に数を書かない）
  function deckLimits() { return (SS.vocab && SS.vocab.card_limits) || {}; }
  // 形式の表示名（`{format}` の確認文にも、ボタンの文言にも同じものを使う）
  function exportFormatLabel(format) { return exportFormatLabels()[format] || format.toUpperCase(); }

  // 見出しのページ数。**この文を組み立てるのはここ 1 か所だけにする。** 最初の描画と実況の
  // 当て直しの 2 か所で同じ文字列を組んでいたので、但し書きを足すと片方だけ直る形だった。
  // 非表示のページは構成に残っているので**総数からは引かず、括弧で添える**（2026-09-18、利用者の
  // 指示）。書き出しと実況が数える「表示中」の数とは別のものなので、同じ数に見せない。
  //
  // **構成の版番号（「構成 10」）はここに出さない**（同日、利用者「いらないね」）。ページを足す・
  // 消す・並べ替える・版を採用するたびに増える内部の通し番号で、見た人が何かを決める材料にならない。
  // 版の記録そのものは各ページの「▸ このページの記録」から辿れる
  function pageCountText(pages) {
    var hidden = (pages || []).filter(function (p) { return p.hidden; }).length;
    return (pages || []).length + " ページ" + (hidden ? "（非表示 " + hidden + "）" : "");
  }
  // 位置は「並びの序数」であって状態ではない。数だけ入れて語はサーバの型から取る
  function movedText(position) {
    var t = reorderWords().moved;
    return t ? t.replace("{n}", String(position)) : "";
  }

  // 版の状態（slide_versions.state）を、語彙の key へ寄せる表。**判断を `v.state === "draft"` のような
  // 生値の比較で画面に散らさない**（散らすと、状態が増えたときに直す場所が画面の中に増える）。
  // サーバが `versions[].display` を返すようになったらそちらが正本になり、この表は使われなくなる
  var VERSION_STATE_KEYS = { draft: "unstarted", pending: "generating", ready: "ready", failed: "failed", cancelled: "cancelled" };
  function versionStateKey(v) {
    if (v.display && v.display.key) { return v.display.key; }
    return VERSION_STATE_KEYS[v.state] || "unknown";
  }
  function versionStateLabel(v) {
    if (v.display && v.display.label) { return v.display.label; }
    var states = (SS.vocab && SS.vocab.page_states) || {};
    var s = states[versionStateKey(v)] || states.unknown;
    return (s && s.label) || "";
  }

  function mark(display) {
    var glyph = { ready: "■", failed: "✕", unknown: "?", cancelled: "—", generating: "◌", adjusting: "◌" }[display.key] || "○";
    return h("span", { class: "mark mark--" + display.key, title: display.label, "aria-label": display.label, text: glyph });
  }

  // カードの中身はヘッダの下に生えるので、印は中身が動く向きを指す。
  // 閉じている＝これから下へ伸びる、開いている＝これから上へ縮む
  function pageIcon(name) {
    var paths = {
      "eye-off": '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.8 10.8 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/>',
      "panel-top-close": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="m9 16 3-3 3 3"/>',
      "panel-bottom-close": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 15h18"/><path d="m15 8-3 3-3-3"/>',
      // 鉛筆は**先が左下**（2026-09-18、利用者の指示）。`✎` のような文字はフォントによって
      // 向きも太さも変わるので、ほかの印と同じ SVG（lucide の pencil）に揃える
      "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
      // 参考画像を添える口。**絵文字を置かない**（2026-09-24、利用者「ここだけカラーアイコンって
      // 異色に見える」）。鉛筆を `✎` にしなかったのと同じ理由で、ほかの印と同じ線の太さ・同じ色
      // （`currentColor`）に揃う SVG にする。絵文字はフォント任せで色まで持ってくる
      "image": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'
    };
    return h("span", { class: "page-icon page-icon--" + name, "aria-hidden": "true", html:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + paths[name] + "</svg>" });
  }

  // ---- 止まっている帯と再開（判断はサーバの limit。画面では推測しない）----
  // 帯を出すかは limit.stopped（止まっているか）だけで決める。枠切れに達したかどうかで決めると、
  // うまく作れずに終わったスライドに帯が出ず、なぜ止まったかの説明が 1 つも出ない。本文はサーバが決める
  function hourMinute(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return null; }
    return d.getHours() + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
  }

  function resumeButton(resume, ctx) {
    var b = h("button", { class: "btn btn--small" + (resume.available ? " btn--primary" : ""), type: "button",
      text: resume.label, disabled: !resume.available,
      "aria-disabled": resume.available ? null : "true", title: resume.available ? null : resume.reason });
    b.addEventListener("click", function () { ctx.resume(b); });
    return b;
  }

  // スライドに 1 つの帯。押すと「何を投げ直すか」はサーバが決める（ページごとのボタンは置かない）
  function limitBanner(limit, ctx) {
    if (!limit || !limit.stopped) { return null; }
    var box = h("div", { class: "notice notice--warn limit-band", role: "status" },
      h("p", { class: "limit-band__text", text: limit.message }));
    // 見込み時刻はサーバが出せたときだけ添える（無いときに時刻を作らない）
    var at = limit.hint_at ? hourMinute(limit.hint_at) : null;
    if (at) {
      box.appendChild(h("p", { class: "small", text: "復活の見込み " + at + " 頃"
        + (limit.hint_source_label ? "（" + limit.hint_source_label + "より）" : "") }));
    }
    box.appendChild(h("div", { class: "editbox__row" }, resumeButton(limit.resume, ctx)));
    if (!limit.resume.available && limit.resume.reason) {
      box.appendChild(h("p", { class: "reason", text: limit.resume.reason }));
    }
    return box;
  }

  // 帯が出ないが再開はできる、という状態のための**ただ 1 つのボタン**。
  // 帯の中のボタンと二重に出さないため、**同じ 1 つの値（limit.stopped）の反対側**で出す。
  // 置き場所は進みの帯のあたり（左のサムネ列には置かない。2026-09-11、利用者の決定で
  // 左は並べ替えだけの列にした。狭い画面で左が横帯になると脇のボタンは消えてしまう問題も併せて解く）
  function resumeRow(limit, ctx) {
    var showResume = limit && !limit.stopped && limit.resume.available;
    if (!showResume) { return null; }
    return h("div", { class: "deck-resume" }, resumeButton(limit.resume, ctx));
  }

  // ---- 「まだ使えない」の理由は、スライドに 1 か所だけ出す ----
  // 押せない部品はページごとに何十個も並ぶ（10 ページなら指示欄 10 個・版スタック 10 個）。
  // そこへ理由の文を添えると、**同じ 1 文が画面に何十回も出る**。132px の版スタックでは
  // 4 行に折り返して右側が文字で埋まり、要望の「情報がおおい」をこちらで生み直してしまう。
  // だから文は 1 か所にまとめ、押せない部品からは `aria-describedby` でその行を指す
  // （読み上げでは部品ごとに理由が付き、目で読む文は 1 回だけになる）。
  // `title`（サーバの reason）は部品ごとに付いたままなので、指した先を探さなくても分かる。
  // `pause` と `regenerate_failed` はここに無い: **画面にその部品がもう無い**ので
  // （2026-09-11、利用者の決定で左のサムネ列から降ろした）、指す先の無い理由を出さない
  var REASON_KEYS = ["deck_instruction", "page_instruction", "regenerate", "adopt_version", "export"];

  function screenReasons(caps) {
    // **同じ文は 1 行だけ。**7 つの操作の理由は、いまは 3 文しかない（多くが同じ 1 文）
    var seen = {};
    var ids = {};
    var box = h("div", { class: "screen-reasons" });
    var n = 0;
    REASON_KEYS.forEach(function (key) {
      var c = SS.cap(caps, key);
      if (c.available || !c.reason) { return; }
      if (!seen[c.reason]) {
        n += 1;
        seen[c.reason] = "unavailable-" + n;
        box.appendChild(h("p", { class: "reason", id: seen[c.reason], text: c.reason }));
      }
      ids[key] = seen[c.reason];
    });
    return { node: n ? box : null, ids: ids };
  }

  // 押せない部品は、その 1 行を指すだけにする（文は複製しない）。
  // **理由が無くなったら指すのもやめる**: 答えは開いている間に変わるので、押せるようになった部品が
  // 消えた行を指したままだと、読み上げが古い理由を読む（2026-09-14 の実害の 3 つ目と同じ病気）
  function describedBy(el, ids, key) {
    if (ids && ids[key]) { el.setAttribute("aria-describedby", ids[key]); }
    else { el.removeAttribute("aria-describedby"); }
    return el;
  }

  // ---- 修正指示の器（各ページのカードと、スライド全体の指示バーが同じ器を使う）----
  // 要望の並びをそのまま上から下へ: 指示文の入力 → 画像（貼り付け・ドラッグ&ドロップ・選ぶ）→
  // 直し方のチェックボックス → 送信ボタン（要望の原文はコミットの本文に残した）。
  //
  // 画面が自分で決めるのは**送る前に数えられること**だけ（文の長さ・添付の枚数・空かどうか）。
  // それ以外の可否（スライドの状態・画像を作っている間・止まっているとき・どの直し方が選べるか）は**サーバ**で、
  // 画面は `capabilities` と `pages[].can_minor` の真偽値と、サーバの文をそのまま出す。
  // 数えるものまでサーバの断りに任せると、押してから初めて「4000文字を超えています」と言われる。
  var nextFieldId = 0;

  function attachChips(box) {
    return box.querySelectorAll(".attach");
  }

  // ---- 参考画像を添える 3 経路（ファイルから選ぶ・貼り付け・ドラッグ&ドロップ）----
  // 修正指示の欄（`editbox`）と、隙間にページを足す欄（`insertGap`）が**同じ部品**を使う。
  // 2 つに書き分けると、片方だけ ⌘V が効かない・片方だけ枚数の数え方が違う、が必ず起きる。
  //
  // 呼び出し側は返ってきた `chips` / `file` / `pick` / `note` を好きな場所に置き、器が出来たら
  // `bind(box)` を呼ぶ（貼り付けとドラッグ&ドロップの受け皿は**器そのもの**なので、器が要る）。
  function attachArea(opts) {
    opts = opts || {};
    var words = instructionWords();
    var limit = instructionLimits().attachments || 0;
    var chips = h("div", { class: "editbox__chips", role: "group", "aria-label": words.attachment_label });
    var file = h("input", { type: "file", class: "editbox__file", multiple: true,
      accept: "image/png,image/jpeg,image/webp", "aria-label": words.attach_label });
    var pick = h("button", { class: "btn btn--icon btn--ghost editbox__pick", type: "button",
      "aria-label": words.attach_label, title: words.attach_label }, pageIcon("image"));
    // 貼り付けとドラッグ&ドロップの案内は**書いている間だけ見せる**（CSS の :focus-within）。
    // 常に出していると、10 ページのスライドで同じ 1 文が 11 回並ぶ（実ブラウザで撮って分かった）
    var note = h("p", { class: "editbox__note small", text: opts.hint || words.attach_hint });
    var box = null;
    var changed = opts.onChange || function () {};
    var locked = opts.locked || function () { return false; };
    var area = { chips: chips, file: file, pick: pick, note: note, over: false };

    // 上限に数えるのは「送れる添付」と「いま送っている添付」。送れなかった 1 枚は数えない
    area.counted = function () {
      var n = 0;
      attachChips(box).forEach(function (chip) { if (!chip.dataset.failed) { n += 1; } });
      return n;
    };

    function addOne(file_, method) {
      if (area.counted() >= limit) { area.over = true; changed(); return; }
      area.over = false;
      var chip = h("span", { class: "attach", dataset: { method: method }, "aria-busy": "true" },
        h("span", { class: "attach__state small", text: words.attachment_uploading }));
      var drop = h("button", { class: "btn btn--small btn--ghost attach__remove", type: "button",
        text: words.attachment_remove, "aria-label": words.attachment_remove });
      drop.addEventListener("click", function () { chip.remove(); area.over = false; changed(); });
      chips.appendChild(chip);
      changed();
      // 添付は**すぐ送る**（利用者にファイル保存を挟ませない）。検査の境界は既にある画像の口
      SS.uploadImage(file_).then(function (r) {
        chip.removeAttribute("aria-busy");
        chip.dataset.asset = r.asset_id;
        SS.clear(chip).appendChild(h("img", { class: "attach__pic", src: r.url, alt: "" }));
        chip.appendChild(drop);
        changed();
      }, function (e) {
        // 送れなかったのは**その 1 枚だけ**。ほかの添付と書いた文はそのまま生きる
        chip.removeAttribute("aria-busy");
        chip.dataset.failed = "1";
        SS.clear(chip).appendChild(h("span", { class: "attach__why small", text: e && e.message ? e.message : "" }));
        chip.appendChild(drop);
        changed();
      });
    }

    function addFiles(list, method) {
      var files = Array.prototype.filter.call(list || [], function (f) { return f && f.type.indexOf("image/") === 0; });
      files.forEach(function (f) { addOne(f, method); });
    }

    function hasFiles(e) {
      var types = e.dataTransfer && e.dataTransfer.types;
      return !!types && Array.prototype.indexOf.call(types, "Files") >= 0;
    }

    pick.addEventListener("click", function () { file.click(); });
    file.addEventListener("change", function () { addFiles(file.files, "file"); file.value = ""; });

    area.bind = function (el) {
      box = el;
      // ⌘V。画像が入っていない貼り付けは**そのまま文字として通す**
      box.addEventListener("paste", function (e) {
        if (locked()) { return; }
        var items = (e.clipboardData && e.clipboardData.items) || [];
        var files = [];
        for (var i = 0; i < items.length; i++) {
          if (items[i].kind !== "file") { continue; }
          var f = items[i].getAsFile();
          if (f) { files.push(f); }
        }
        if (!files.length) { return; }
        e.preventDefault();
        addFiles(files, "paste");
      });
      // 器そのものが受け皿。**外から来たファイルのときだけ**強調する（並べ替えは pointer なので混ざらない）
      box.addEventListener("dragover", function (e) {
        if (locked() || !hasFiles(e)) { return; }
        e.preventDefault();
        box.classList.add("editbox--drop");
      });
      box.addEventListener("dragleave", function (e) {
        if (e.target === box || !box.contains(e.relatedTarget)) { box.classList.remove("editbox--drop"); }
      });
      box.addEventListener("drop", function (e) {
        if (locked() || !hasFiles(e)) { return; }
        e.preventDefault();
        box.classList.remove("editbox--drop");
        addFiles(e.dataTransfer.files, "drop");
      });
      return box;
    };
    return area;
  }

  // 画面に出ている順のまま、登録できた添付だけを送る形にする（送っている途中・送れなかった枚は
  // `data-asset` を持たない）。サーバはこの順で参考画像を渡す
  function readAttachments(box) {
    var out = [];
    box.querySelectorAll(".attach[data-asset]").forEach(function (chip) {
      out.push({ asset_id: chip.dataset.asset, input_method: chip.dataset.method });
    });
    return out;
  }

  function editbox(caps, target, opts) {
    opts = opts || {};
    var vocab = SS.vocab || {};
    var modes = vocab.edit_modes || {};
    var words = instructionWords();
    var limits = instructionLimits();
    var textLimit = limits.text || 0;
    var capKey = opts.deck ? "deck_instruction" : "page_instruction";
    var c = SS.cap(caps, capKey);
    // 「今の絵を土台にして直せるか」はサーバが決める（`pages[].can_minor`）。**画面は状態から導かない。**
    // 絵の無いページでは土台が無いので、**チェックを入れた状態に固定して理由を添える**
    // （既定のまま送らせて、送ってから断るのは遅い）
    var canMinor = opts.canMinor || { available: false, reason: words.remake_forced_reason };
    var fieldId = "instruct-" + (++nextFieldId);

    var ta = h("textarea", { id: fieldId, "aria-label": target + " " + words.section_label,
      placeholder: opts.deck ? words.deck_placeholder : words.page_placeholder });
    // 3 つの経路（貼り付け・ドラッグ&ドロップ・ファイルから選ぶ）を**どれも隠したメニューに落とさない**
    var att = attachArea({ locked: function () { return locked(); }, onChange: function () { sync(); } });
    var chips = att.chips, file = att.file, pick = att.pick, note = att.note;
    // このページの事情（空・長すぎ・枚数の超過）を出す 1 行。スライドの事情は上に 1 か所だけ出る
    var why = h("p", { class: "editbox__why small", role: "status" });
    // 送った答え（サーバの文）と、断られた理由を出す場所。**上の `why` とは別の器**にする:
    // 送信の答えを、画面が数えた理由で上書きしてしまわないため
    var said = h("div", { class: "editbox__said small", role: "status" });
    var send = h("button", { class: "btn" + (c.available ? " btn--primary" : ""), type: "button", text: c.label });
    var remake = h("input", { type: "checkbox", class: "editbox__remake-box", id: fieldId + "-remake" });
    remake.checked = !canMinor.available;
    remake.disabled = !canMinor.available;
    var remakeLabel = h("label", { class: "editbox__remake", for: remake.id,
      title: canMinor.available ? words.remake_description : canMinor.reason },
      remake, h("span", { text: words.remake_without_source || modes.remake || "" }),
      canMinor.available ? null : h("span", { class: "editbox__forced small", text: canMinor.reason }));
    var fs = h("fieldset", { disabled: !c.available, "aria-describedby": opts.reasonId || null },
      opts.deck ? h("div", { class: "editbox__row" }, h("span", { class: "target", text: target })) : null,
      h("div", { class: "editbox__prompt" }, ta,
        h("div", { class: "editbox__prompt-tools" }, pick, remakeLabel,
          h("span", { class: "grow" }), send)),
      h("div", { class: "editbox__row editbox__attach" }, chips, file),
      note,
      why, said);
    // 理由の文はここには出さない（スライドに 1 か所。上の `screenReasons`）。fieldset がその行を指す。
    // モードの助言も**カードごとには要らない**（どのページでも同じ 1 文）。デッキ全体への指示に 1 回だけ置く
    var box = h("div", { class: "editbox" }, fs);

    function locked() { return fs.disabled; }
    function counted() { return att.counted(); }
    function sync() {
      var text = ta.value;
      // 送れない理由は 1 つだけ出す（上から順に、最初に当たったもの）。文はすべてサーバのもの
      var reason = "";
      if (att.over) { reason = words.too_many_attachments_reason; }
      else if (text.length > textLimit) { reason = words.text_too_long_reason; }
      else if (!text.trim() && !counted()) { reason = words.empty_reason; }
      if (why.textContent !== reason) { why.textContent = reason; }
      // **何も書いていないだけの行は、書き始めるまで静かにしておく。**押せない理由は
      // 押せないボタン自身（title）といつでも読める場所に残すが、10 ページのスライドで同じ 1 文が
      // 11 回並ぶのを避ける（間違えているとき＝長すぎ・枚数超過の行は、焦点が無くても出す）
      why.classList.toggle("editbox__why--quiet", reason === words.empty_reason);
      send.title = reason || (c.available ? null : c.reason) || "";
      send.disabled = !!reason || box.dataset.sending === "1";
    }

    ta.addEventListener("input", sync);
    remake.addEventListener("change", sync);
    att.bind(box);
    if (opts.onSubmit) {
      send.addEventListener("click", function () { opts.onSubmit(box, send); });
    }
    box.__sync = sync;
    // ---- 「今なにができるか」が変わったときの当て直し（**器は作り直さない**）----
    // 作り直せば確実に揃うが、**書きかけの指示文と、送り終えた添付が消える**。修正指示を打っている
    // 最中に実況が 1 回当たっただけで打った文が消えるのは、直そうとしている不具合より悪い。
    // だから欄そのものは残し、**可否に関わる見た目だけ**をここで当てる（`ta.value` と `chips` は触らない）。
    box.__caps = function (nextCap, nextCanMinor, reasonId) {
      if (nextCap) { c = nextCap; }
      fs.disabled = !c.available;
      if (reasonId) { fs.setAttribute("aria-describedby", reasonId); }
      else { fs.removeAttribute("aria-describedby"); }
      if (send.textContent !== c.label) { send.textContent = c.label; }
      send.classList.toggle("btn--primary", !!c.available);
      if (nextCanMinor) {
        // **利用者が自分で入れたチェックは動かさない。** 固定されていた（押せなかった）ときだけ
        // 既定へ戻す: 絵ができて今の絵から直せるようになったのに、作り直す側で固まったままにしない
        if (remake.disabled) { remake.checked = !nextCanMinor.available; }
        remake.disabled = !nextCanMinor.available;
        remakeLabel.title = nextCanMinor.available ? words.remake_description : (nextCanMinor.reason || "");
        var forced = remakeLabel.querySelector(".editbox__forced");
        if (nextCanMinor.available) { if (forced) { forced.remove(); } }
        else if (forced) { forced.textContent = nextCanMinor.reason || ""; }
        else { remakeLabel.appendChild(h("span", { class: "editbox__forced small", text: nextCanMinor.reason || "" })); }
      }
      sync();
    };
    sync();
    return box;
  }

  // スライド全体の欄が「今の絵を土台にして直せるか」は、**サーバが決めた真偽値をページの数だけ集めるだけ**
  // にする（画面が状態から新しい判断を作らない）。1 ページでも絵が無ければ全体では土台にできず
  // 送信の口が断るので、先にチェックを固定して同じ理由を出す。文もサーバのもの（`can_minor.reason`）
  function deckCanMinor(pages) {
    var words = instructionWords();
    var blocked = pages.filter(function (p) { return !p.can_minor.available; });
    if (!blocked.length) { return { available: true, reason: null }; }
    return { available: false, reason: blocked[0].can_minor.reason || words.remake_forced_reason };
  }

  function deckInstruction(caps, reasonIds, opts) {
    var box = h("section", { class: "deck-instruction is-compact", "aria-label": "デッキ全体への指示" });
    var bar = h("button", { class: "deck-instruction__bar", type: "button", "aria-expanded": "false" },
      h("span", { class: "target", text: "デッキ全体への指示" }),
      // 何に使う欄かを常に書く。使えない理由はスライドに 1 か所（すぐ上の行）にあるので、ここには複製しない
      h("span", { class: "reason", text: "口調・配色などデッキ全体に効く指示" }),
      h("span", { class: "muted small", text: "▾" }));
    bar.addEventListener("click", function () {
      var open = box.classList.toggle("is-open");
      bar.setAttribute("aria-expanded", open ? "true" : "false");
    });
    box.appendChild(bar);
    opts = opts || {};
    box.appendChild(h("div", { class: "deck-instruction__body" },
      editbox(caps, "デッキ全体", { deck: true, reasonId: reasonIds && reasonIds.deck_instruction,
        canMinor: opts.canMinor, onSubmit: opts.onSubmit })));
    return box;
  }

  // ---- 画像のコピー（Clipboard API → 3 段の代替。ADR 0014 §2）----
  function recordCopy(method, ok, errorName, deckId) {
    return SS.api("/api/events/clipboard-copy", { method: "POST", body: { launch_id: SS.launchId, method: method, ok: !!ok, error_name: errorName || null } })
      .catch(function () { /* 記録できなくても操作は止めない */ });
  }
  function copyImage(page, article, position) {
    var url = page.image_url;
    if (!url) { SS.toast("この状態のページには、コピーできる画像がありません。", { kind: "error" }); return; }
    var fallback = function (errName) {
      recordCopy("api", false, errName);
      var old = article.querySelector(".copy-fallback");
      if (old) { old.remove(); }
      var abs = location.origin + location.pathname;
      var save = h("a", { href: url, download: "slide-" + position + ".png", text: "画像を保存", onclick: function () { recordCopy("save", true, null); } });
      var panel = h("div", { class: "notice notice--info copy-fallback", role: "status" },
        h("p", { text: "このブラウザではクリップボードへ直接コピーできませんでした。次のいずれかをお使いください。" }),
        h("ol", null,
          h("li", { text: "ページ画像を右クリック（またはロングプレス）→「画像をコピー」" }),
          h("li", null, "このURLを外部ブラウザで開いてコピー: ", h("code", { text: abs })),
          h("li", null, save)),
        h("p", null, h("button", { class: "btn btn--small", type: "button", text: "閉じる", onclick: function () { panel.remove(); } })));
      article.querySelector(".frame").insertAdjacentElement("afterend", panel);
      recordCopy("context_menu_hint", true, null);
    };
    if (!(navigator.clipboard && navigator.clipboard.write && typeof ClipboardItem !== "undefined")) { fallback("unsupported"); return; }
    fetch(url).then(function (r) { if (!r.ok) { throw new Error("fetch_" + r.status); } return r.blob(); }).then(function (blob) {
      var item = new ClipboardItem({ "image/png": blob });
      return navigator.clipboard.write([item]);
    }).then(function () { recordCopy("api", true, null); SS.toast("画像をコピーしました。他のアプリにそのまま貼り付けられます。"); })
      .catch(function (e) { fallback(e && e.name ? e.name : "unknown"); });
  }

  // ---- 畳んだ記録（その場に展開。名前はサーバの語彙から）----
  // 中身は「起きたことの記録」（狙い・元テキスト・出所・生成指示・版の表・生成の試行・見比べ）。
  // 要望の「情報が多い」に応えるためにここへ畳むが、**捨てない**。枠の中に残すのは直せることだけ
  function detailsBlock(page) {
    var words = versionWords();
    var box = h("div", { class: "record", hidden: true });
    box.appendChild(h("dl", null,
      h("dt", { text: "狙い" }), h("dd", { text: page.intent || "（未記入）" }),
      h("dt", { text: "元テキスト" }), h("dd", null, h("pre", { text: page.source_text || "（無し）" })),
      h("dt", { text: "表示本文" }), h("dd", null, h("pre", { text: page.body || "（無し）" })),
      h("dt", { text: "出所" }), h("dd", { text: page.source_kind_label })));
    // 生成指示は「詳細」の中のさらに折りたたみ（初見の人にプロンプト操作を強制しない。UI.md §5）
    var promptDetails = h("details", null, h("summary", { text: "生成指示（この版に使った指示文）" }),
      h("pre", { class: "prompt", text: page.prompt_text || "（この版には生成指示が記録されていません）" }));
    box.appendChild(promptDetails);

    // 版一覧（絵で見分けるのは版スタックの役目。ここは日時・指示・モードを読むための表として残す）。
    // **版を選ぶボタンはここには置かない**: 選ぶ操作は版スタックに 1 か所だけ置く（同じ操作を 2 か所に
    // 置くと、押せる・押せないの直列化を 2 通り書くことになる）。記録は「起きたこと」を読む場所である
    var vt = h("table", null, h("thead", null, h("tr", null,
      h("th", { text: "版" }), h("th", { text: "状態" }), h("th", { text: "出所" }), h("th", { text: "日時" }), h("th", { text: "指示" }), h("th", { text: "モード" }))));
    var vb = h("tbody");
    page.versions.forEach(function (v) {
      vb.appendChild(h("tr", null,
        h("td", { text: "v" + v.version + (v.active ? "（" + (words.active || "") + "）" : "") }),
        h("td", { text: versionStateLabel(v) + (v.image_missing ? "（" + (words.image_missing || "") + "）" : "") }),
        h("td", { text: v.source_kind_label }),
        h("td", { text: SS.fmtDate(v.created_at) }),
        // 生成指示そのものは「生成指示」の折りたたみの中だけに置く。表には利用者が書いた指示だけを出す
        h("td", { text: v.revision_note || "—" }),
        h("td", { text: v.edit_mode_label || "—" })));
    });
    vt.appendChild(vb);
    box.appendChild(h("div", null, h("h3", { text: "版（" + page.versions.length + "）" }), h("div", { class: "table-wrap" }, vt)));

    // 見比べはここには置かない（版スタックの直下の常設の入口に格上げした。UI.md §6）。
    // 選んだら即その版が使われるので、**構成版を増やさずに新旧を並べて見られる唯一の手段**であり、
    // 畳んだ器の中に隠すと「切り替えて目で追う」たびに構成版が増える形しか残らない

    // 試行（実際に起きたことだけ。件数は行から）
    if (page.attempts.length) {
      var at = h("table", null, h("thead", null, h("tr", null, h("th", { text: "試行" }), h("th", { text: "結果" }), h("th", { text: "理由" }), h("th", { text: "開始" }), h("th", { text: "終了" }))));
      var ab = h("tbody");
      page.attempts.forEach(function (a) {
        ab.appendChild(h("tr", null, h("td", { text: a.attempt_no + " 回目" }), h("td", { text: a.state_label }), h("td", { text: a.reason || "—" }),
          h("td", { text: SS.fmtDate(a.started_at) }), h("td", { text: SS.fmtDate(a.ended_at) })));
      });
      at.appendChild(ab);
      box.appendChild(h("div", null, h("h3", { text: "生成の試行（" + page.attempts.length + " 回）" }), h("div", { class: "table-wrap" }, at)));
    } else {
      box.appendChild(h("p", { class: "reason", text: "このページには生成の試行の記録がありません。" }));
    }
    return box;
  }

  // ---- 版の縦スタック（スライドの右。絵で見分ける）----
  // 利用者「修正したあとにバージョン管理というか新旧の画像ができると思いますが、右側にサムネイルを
  // 縦にスタックするようなUIにしたくて、そのサムネを選択すると左側のプレビューも同期する」。
  // いまは**見えるところまで**（押せない）。押せるようにするのは次の段で、押したら即その版が使われる。
  //
  // 押せるかの判断は 2 つだけ:
  //   (1) サーバの `capabilities.adopt_version`（まだ使えない、の口。U7）
  //   (2) **その版に絵の URL があるか**という事実。「状態が draft だから」という解釈では決めない
  //       （解釈で決めると、状態が増えたときに画面の中に直す場所が増える）
  var STACK_SHOWN = 6;   // これだけ出して、残りは畳む（改稿を繰り返すと版は素直に溜まる）

  // どれを畳むか。**並びは崩さず、畳むかどうかの印だけを付ける**（番号が飛んで見えないように）。
  // 畳む側に使用中の版が来たら、その 1 枚だけは畳まない:
  // 「使用中の印はどのページにも必ず 1 つ見える」を、畳み方の都合で壊さないため
  function foldedFlags(versions) {
    var shown = 0;
    return versions.map(function (v) {
      if (shown < STACK_SHOWN || v.active) { shown += 1; return false; }
      return true;
    });
  }

  function versionThumb(v, caps, words, newest, reasonIds, onAdopt, whyIds) {
    var canAdopt = SS.cap(caps, "adopt_version");
    var hasImage = !!v.image_url;
    // 絵の無い版の理由は版ごと・状態ごとにサーバが決める（正本は revision_routes.py の
    // version_not_adoptable_reason）。画面では組み立てない・状態から選び直さない
    var reason = hasImage ? (canAdopt.available ? null : canAdopt.reason) : (v.not_adoptable_reason || "");
    var flags = [];
    if (v.active) { flags.push(words.active || ""); }
    if (newest) { flags.push(words.newest || ""); }
    var pic = h("span", { class: "version__pic", "aria-hidden": "true" });
    if (hasImage) {
      pic.appendChild(h("img", { src: v.image_url, alt: "", loading: "lazy" }));
    } else {
      // 絵の無い版は、代わりに状態の印と状態名を出す（何を待っているのかが分かる）
      pic.appendChild(mark({ key: versionStateKey(v), label: versionStateLabel(v) }));
    }
    var meta = h("span", { class: "version__meta" }, h("span", { class: "version__no", text: "v" + v.version }));
    if (v.active) { meta.appendChild(h("span", { class: "version__flag version__flag--active", text: "● " + (words.active || "") })); }
    if (newest) { meta.appendChild(h("span", { class: "version__flag version__flag--newest", text: words.newest })); }
    if (!hasImage) { meta.appendChild(h("span", { class: "version__state", text: versionStateLabel(v) })); }
    if (v.image_missing) { meta.appendChild(h("span", { class: "version__state", text: words.image_missing })); }
    var b = h("button", { class: "version" + (v.active ? " version--active" : ""), type: "button",
      dataset: { version: v.id },
      disabled: !hasImage || !canAdopt.available,
      "aria-disabled": (!hasImage || !canAdopt.available) ? "true" : null,
      "aria-current": v.active ? "true" : null,
      "aria-label": "v" + v.version + (flags.length ? "（" + flags.join("・") + "）" : ""),
      title: reason }, pic, meta);
    if (hasImage) { describedBy(b, reasonIds, "adopt_version"); }
    // 絵の無い版は、スタックの下にまとめた**その 1 行**を指す（文をサムネの数だけ並べない）
    else if (whyIds && whyIds[v.not_adoptable_reason]) { b.setAttribute("aria-describedby", whyIds[v.not_adoptable_reason]); }
    // **押したら即その版が使われる。ただし絵は答えが返ってから替える**（楽観的に切り替えない）。
    // 切り替えて見せてから戻すのは、成果物について嘘をつくことになる（並べ替えの巻き戻しより害が大きい）。
    // だからここでは何も描き替えず、答えの当て込みは `ctx.adopt` の成功ハンドラ 1 か所に集める
    if (hasImage && canAdopt.available && onAdopt) {
      b.addEventListener("click", function () { onAdopt(v.id, b); });
    }
    // **理由の文はサムネの中に書かない。**132px の列では 1 文が 3 行に折り返すので、絵の無い版が
    // 2 つあるだけで右側が 6 行の文字で埋まる（実ブラウザで測った: 122px 幅・42px 高が 2 つ）。
    // 同じ文はスタックの下に 1 行だけ出し、サムネはそこを `aria-describedby` で指す（上）。
    // `title` には版ごとの文が入ったままなので、指した先を探さなくても読める
    return b;
  }

  function versionStack(page, ctx) {
    var caps = ctx.caps;
    var reasonIds = ctx.reasonIds;
    var words = versionWords();
    var headId = "versions-" + page.slide_id;
    // 押した結果（サーバの文）をここに出す。**黙って切り替えない・黙って断らない。**
    // 文はサーバのものをそのまま出す（画面では作らない）ので、器だけ先に置く
    var said = h("p", { class: "versions__said small", role: "status" });
    var box = h("div", { class: "versions" },
      h("p", { class: "versions__head", id: headId, text: words.stack_label }));
    var onAdopt = function (versionId, button) { ctx.adopt(page.slide_id, versionId, button, said); };
    // 並びはサーバが返した順（新しいものが上）。画面で並べ直さない
    var folded = foldedFlags(page.versions);
    var newestId = page.versions.length ? page.versions[0].id : null;
    // 「新しい」は、一番上が使用中でないときだけ出す（気づきの経路。使用中と二重に飾らない）
    var showNewest = !!(newestId && !page.versions[0].active);
    var list = h("ol", { class: "versions__list", "aria-labelledby": headId });
    // 押せない理由は**版の数だけ並べない。**同じ文は 1 行にまとめ、サムネはその行を指す
    // （サーバは版の状態ごとに違う文を返すので、違う理由は違う行になる）
    var whyBox = h("div", { class: "versions__why-box" });
    var whyIds = {};
    var whyCount = 0;
    page.versions.forEach(function (v) {
      var text = v.image_url ? null : v.not_adoptable_reason;
      if (!text || whyIds[text]) { return; }
      whyCount += 1;
      whyIds[text] = "version-why-" + page.slide_id + "-" + whyCount;
      whyBox.appendChild(h("p", { class: "version__why small", id: whyIds[text], text: text }));
    });
    page.versions.forEach(function (v, i) {
      list.appendChild(h("li", folded[i] ? { hidden: true, dataset: { older: "1" } } : null,
        versionThumb(v, caps, words, showNewest && v.id === newestId, reasonIds, onAdopt, whyIds)));
    });
    box.appendChild(list);
    box.appendChild(whyBox);
    if (folded.indexOf(true) >= 0) {
      var more = h("button", { class: "btn btn--small btn--ghost versions__more", type: "button",
        "aria-expanded": "false", text: words.show_older });
      more.addEventListener("click", function () {
        var open = more.getAttribute("aria-expanded") === "true";
        list.querySelectorAll('li[data-older="1"]').forEach(function (li) { li.hidden = open; });
        more.setAttribute("aria-expanded", open ? "false" : "true");
        more.textContent = open ? words.show_older : words.hide_older;
      });
      box.appendChild(more);
    }
    box.appendChild(said);
    return box;
  }

  // ---- 見比べる（使用中の版と、選んだ 1 枚を左右に）----
  // 器はカードの幅いっぱいを使う（132px の版スタックの中に入れると絵が親指の大きさになる）。
  // だから**入口は版スタックの中・器はスライドの下**に置き、押したときに同じカードの中から探す。
  // 画面がここで持つ状態は「相手の版 ID」1 つだけで、これは採用ではないので構成版を増やさない。
  function picturedVersions(page) {
    return page.versions.filter(function (v) { return !!v.image_url; });
  }

  function compareToggle(page, words, panel) {
    var b = h("button", { class: "btn btn--small btn--ghost versions__compare", type: "button",
      "aria-expanded": panel.hidden ? "false" : "true",
      text: panel.hidden ? words.compare_label : words.compare_hide });
    b.addEventListener("click", function () {
      var open = panel.hidden;
      panel.hidden = !open;
      panel.dataset.open = open ? "1" : "";
      b.setAttribute("aria-expanded", open ? "true" : "false");
      b.textContent = open ? words.compare_hide : words.compare_label;
    });
    return b;
  }

  function compareSide(v, words) {
    var flags = [];
    if (v.active) { flags.push(words.active || ""); }
    var meta = [v.source_kind_label, v.edit_mode_label].filter(Boolean).join(" · ");
    return h("figure", { class: "compare__side" },
      h("img", { src: v.image_url, alt: "v" + v.version, loading: "lazy" }),
      h("figcaption", null,
        h("span", { class: "compare__no", text: "v" + v.version + (flags.length ? "（" + flags.join("・") + "）" : "") }),
        h("span", { class: "small muted", text: meta }),
        v.revision_note ? h("span", { class: "small compare__note", text: v.revision_note }) : null));
  }

  // `state` は畳み直しのときだけ渡す（開いていたか・どの版と見比べていたか）。持ち越さないと
  // 版を選んだ瞬間に器が閉じ、見比べの最中に画面が勝手に片付く
  function comparePanel(page, state) {
    var words = versionWords();
    var pics = picturedVersions(page);
    var active = pics.filter(function (v) { return v.active; })[0] || null;
    var others = pics.filter(function (v) { return v !== active; });
    var panel = h("section", { class: "compare", hidden: true,
      "aria-label": words.compare_label });
    if (!active || !others.length) { return panel; }
    var want = state && state.partner;
    var partner = others.filter(function (v) { return v.id === want; })[0] || others[0];
    var pair = h("div", { class: "compare__pair" });
    var pick = h("div", { class: "compare__pick", role: "group", "aria-label": words.compare_pick });
    var draw = function () {
      panel.dataset.partner = partner.id;
      SS.clear(pair);
      pair.appendChild(compareSide(active, words));
      pair.appendChild(compareSide(partner, words));
      pick.querySelectorAll("button").forEach(function (btn) {
        btn.setAttribute("aria-pressed", btn.dataset.version === partner.id ? "true" : "false");
      });
    };
    others.forEach(function (v) {
      var btn = h("button", { class: "btn btn--small compare__choice", type: "button",
        dataset: { version: v.id }, text: "v" + v.version });
      // **見比べる相手を選ぶだけ。**ここから採用の口は呼ばない（使われる版は変わらない）
      btn.addEventListener("click", function () { partner = v; draw(); });
      pick.appendChild(btn);
    });
    if (others.length > 1) { panel.appendChild(pick); }
    panel.appendChild(pair);
    draw();
    if (state && state.open) { panel.hidden = false; panel.dataset.open = "1"; }
    return panel;
  }

  // 入口と器を 1 つの箱にする。**132px の版スタックの中には入れない**: 入口のラベルは
  // 「使われる版は変わりません」まで含めて読ませたいのに、あの幅では 4 行に折り返す（実ブラウザで測った）。
  // 箱はカードの幅を使い、入口だけ右へ寄せて版スタックの真下に置く（近さは保ち、折り返しは無くす）
  function compareBox(page, state) {
    if (picturedVersions(page).length < 2) { return null; }
    var words = versionWords();
    var panel = comparePanel(page, state);
    return h("div", { class: "compare-box" },
      h("div", { class: "compare-bar" }, compareToggle(page, words, panel)), panel);
  }

  // ---- 空枠の中身（画像がまだ無い状態。状態が変わったらここだけ差し替える）----
  function frameEmpty(d, caps, article, reasonIds) {
    var empty = h("div", { class: "frame__empty" }, mark(d), h("span", { class: "frame__label", text: d.label }));
    if (d.reason) { empty.appendChild(h("span", { class: "small", text: d.reason })); }
    if (d.key === "failed") {
      // 2 つの入口は行き先が違う。**どちらも「押せるのに何も起きない」にはしない**:
      // 前者はチェックを入れて（今の絵から離れる形にして）欄へ、後者は欄へ連れて行くだけ
      var again = describedBy(SS.disabledButton(caps, "regenerate", "btn--small"), reasonIds, "regenerate");
      again.addEventListener("click", function () {
        var box = article.querySelector(".editbox");
        var mode = box && box.querySelector(".editbox__remake-box");
        if (mode && !mode.disabled && !mode.checked) { mode.checked = true; mode.dispatchEvent(new Event("change")); }
        var ta = box && box.querySelector("textarea");
        if (ta) { ta.focus(); }
      });
      empty.appendChild(h("div", { class: "editbox__row" }, again,
        h("button", { class: "btn btn--small", type: "button", text: "指示を変えて作り直す", onclick: function () { var ta = article.querySelector("textarea"); if (ta) { ta.focus(); } } })));
    }
    return empty;
  }

  // ---- 隙間にページを足す（P11。ADR 0048）----
  // **押しただけでは何も作らない。** 入力欄が開くだけで、DB は 1 行も変わらない（決定①）。
  // 送ると企画タスクが 1 行できて、ページはワーカーが答えを得てから生む（決定②）。
  //
  // 使えないときは **null を返してボタンごと消す**（無効にして理由を添えない。決定⑦）。
  // 画面が「作っています」と言っている横に「ここに足せます」があると、同じ画面が 2 つのことを
  // 同時に言うことになる（ADR 0043 と同じ判断）。**理由はサーバが持っている**（口は必ず断る。決定⑧）。
  function insertGap(afterSlideId, ctx, data, reload) {
    var words = insertWords();
    var waiting = (data.pending_inserts || []).filter(function (t) {
      return (t.after_slide_id || null) === afterSlideId;
    })[0];
    if (waiting) {
      // 押した隙間が、企画が返るまでの間も画面に残る（利用者の選択 A）。
      return h("div", { class: "gap gap--waiting", "data-after": afterSlideId || "" },
        h("span", { class: "gap__spinner", "aria-hidden": "true" }),
        h("span", { class: "gap__waiting", role: "status", text: words.waiting || "考えています…" }));
    }
    var cap = (ctx.caps && ctx.caps.insert_page) || {};
    if (!cap.available) { return null; }
    var gap = h("div", { class: "gap", "data-after": afterSlideId || "" });
    // 「＋」だけだと何のボタンか分からない（2026-09-24、利用者が実物を見て指摘）。
    // **語は出す。小さくするのは字の大きさと枠であって、意味ではない。**
    var label = cap.label || "ページを追加";
    var openBtn = h("button", { class: "gap__add", type: "button", text: "＋" + label });
    // **修正指示の欄と同じ作りにする**（2026-09-24、利用者「個別ページの修正版みたいなレイアウトで」）。
    // 器の class も同じ（`editbox` 系）: 見た目を似せるために別の CSS を書き足すと、片方だけ直して
    // 片方が取り残される。違うのは中身だけ——直し方のチェックは無く（まだ絵が無い）、
    // 「やめる」が送信の左に並ぶ。
    var textLimit = instructionLimits().text || 0;
    var ta = h("textarea", { class: "gap__text", "aria-label": words.section_label || "足すページの指示",
                             placeholder: words.placeholder || "" });
    var att = attachArea({ hint: words.attach_hint, onChange: function () { sync(); } });
    var why = h("p", { class: "editbox__why small", role: "status" });
    var send = h("button", { class: "btn btn--primary", type: "button", text: words.submit || "送信" });
    var cancel = h("button", { class: "btn btn--ghost", type: "button", text: words.cancel || "キャンセル" });
    // 送信は右端。やめるはその左（同上）。`.grow` は畳まれる決まりなので、真ん中は素の span で空ける
    var form = h("div", { class: "gap__form editbox", hidden: true },
      h("div", { class: "editbox__prompt" }, ta,
        h("div", { class: "editbox__prompt-tools" }, att.pick, h("span", {}),
          h("span", { class: "gap__actions" }, cancel, send))),
      h("div", { class: "editbox__row editbox__attach" }, att.chips, att.file),
      att.note, why);
    att.bind(form);

    var sending = false;
    // 送れない理由は 1 つだけ、上から順に出す（修正指示の欄と同じ順・同じ文）。
    // **本文が空なだけの行は静かにしておく**（隙間ごとに同じ 1 文が並ぶのを避ける）
    function sync() {
      var text = ta.value;
      var reason = "";
      if (att.over) { reason = instructionWords().too_many_attachments_reason; }
      else if (text.length > textLimit) { reason = instructionWords().text_too_long_reason; }
      else if (!text.trim()) { reason = words.empty_reason; }
      if (why.textContent !== reason) { why.textContent = reason || ""; }
      why.classList.toggle("editbox__why--quiet", reason === words.empty_reason);
      send.title = reason || "";
      send.disabled = !!reason || sending;
    }

    var close = function () {
      form.hidden = true; openBtn.hidden = false; ta.value = "";
      att.chips.querySelectorAll(".attach").forEach(function (chip) { chip.remove(); });
      att.over = false;
      sync();
    };
    openBtn.addEventListener("click", function () {
      openBtn.hidden = true; form.hidden = false; ta.focus();
    });
    cancel.addEventListener("click", close);
    var submit = function () {
      if (sending || send.disabled) { return; }
      sending = true;
      send.disabled = true;
      cancel.disabled = true;
      SS.api("/api/decks/" + data.deck.id + "/pages/insert", { method: "POST", body: {
        after_slide_id: afterSlideId, text: ta.value.trim(), attachments: readAttachments(form),
        lock_version: data.deck.lock_version
      } }).then(function (r) {
        if (r && r.message) { SS.toast(r.message); }
        reload();  // 待っている姿はサーバの pending_inserts から描く（画面側で状態を作らない）
      }).catch(function (e) {
        // 断られたら**入力を消さない**（書いた文と添えた画像を失わせない。修正指示の欄と同じ約束）
        sending = false;
        cancel.disabled = false;
        sync();
        SS.toastError(e);
      });
    };
    send.addEventListener("click", submit);
    ta.addEventListener("input", sync);
    // Ctrl/⌘ + Enter で送る（修正指示の欄と同じ約束）
    ta.addEventListener("keydown", function (ev) {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") { ev.preventDefault(); submit(); }
      if (ev.key === "Escape") { close(); }
    });
    sync();
    gap.appendChild(openBtn);
    gap.appendChild(form);
    return gap;
  }

  // ---- 1 ページ ----
  function pageArticle(page, ctx) {
    var caps = ctx.caps;
    var d = page.display;
    var collapseKey = "slide-card-collapsed:" + page.slide_id;
    var savedCollapse = sessionStorage.getItem(collapseKey);
    var collapsed = savedCollapse === null ? !!page.hidden : savedCollapse === "1";
    var article = h("article", { class: "page" + (page.hidden ? " page--hidden" : "") + (collapsed ? " page--collapsed" : ""), id: "page-" + page.slide_id,
      "aria-label": "ページ " + page.position + (page.title ? ": " + page.title : ""),
      dataset: { slide: page.slide_id, position: String(page.position), version: page.version_id } });

    var menuBtn = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "ページの操作", "aria-haspopup": "menu", text: "⋮" });
    var menuWrap = h("span", { class: "menu-anchor" }, menuBtn);
    menuBtn.addEventListener("click", function () {
      // 位置と件数は「いま画面に出ている並び」から取る（描いた時点の page.position は並べ替えで古くなる）
      var pos = ctx.positionOf(page.slide_id);
      SS.menu(menuBtn, [
        { label: "画像をコピー", disabled: !page.image_url, reason: "コピーできる画像がありません", onSelect: function () { copyImage(page, article, pos); } },
        { label: "画像を保存", disabled: !page.image_url, reason: "保存できる画像がありません", onSelect: function () {
          var a = h("a", { href: page.image_url, download: "slide-" + pos + ".png" }); document.body.appendChild(a); a.click(); a.remove(); recordCopy("save", true, null);
        } },
        "-",
        { label: "このページを複製", onSelect: function () { ctx.duplicatePage(page.slide_id); } },
        { label: page.hidden ? "このページを表示にする" : "このページを非表示にする", danger: !page.hidden,
          disabled: !page.hidden && ctx.visibleCount() <= 1, reason: "最後の 1 ページは非表示にできません", onSelect: function () {
          ctx.setPageHidden(page.slide_id, !page.hidden);
        } }
      ]);
    });

    // ヘッダは 1 行。**状態の印と状態名はここに常設する**（枠の中に置くと、絵が出た瞬間に空枠ごと
    // 消えて、絵ができ上がったのかどうかが読めなくなる）。版は「v3」の文字だけ（一覧は右の版スタックが見せる）
    var collapseButton = h("button", { class: "btn btn--icon btn--ghost page__collapse", type: "button",
      "aria-label": collapsed ? "ページを開く" : "ページを閉じる", "aria-expanded": collapsed ? "false" : "true",
      title: collapsed ? "ページを開く" : "ページを閉じる" },
      pageIcon(collapsed ? "panel-bottom-close" : "panel-top-close"));
    collapseButton.addEventListener("click", function () {
      collapsed = !collapsed;
      article.classList.toggle("page--collapsed", collapsed);
      sessionStorage.setItem(collapseKey, collapsed ? "1" : "0");
      collapseButton.setAttribute("aria-expanded", collapsed ? "false" : "true");
      collapseButton.setAttribute("aria-label", collapsed ? "ページを開く" : "ページを閉じる");
      collapseButton.title = collapsed ? "ページを開く" : "ページを閉じる";
      collapseButton.replaceChildren(pageIcon(collapsed ? "panel-bottom-close" : "panel-top-close"));
    });

    article.appendChild(h("header", { class: "page__head" },
      h("span", { class: "page__num", text: String(page.position) }),
      h("span", { class: "page__title", text: page.title || "" }),
      page.hidden ? h("span", { class: "page__hidden-mark", "aria-label": "非表示", title: "非表示" }, pageIcon("eye-off")) : null,
      h("span", { class: "page__head-spacer" }),
      collapseButton,
      page.source_kind !== "generated" ? h("span", { class: "chip page__origin", text: page.source_kind_label }) : null,
      h("span", { class: "page__state" }, mark(d), h("span", { class: "page__state-label", text: d.label })),
      h("span", { class: "page__ver muted small", text: "v" + page.version }),
      menuWrap));

    var frame = h("figure", { class: "frame frame--" + d.key, "aria-label": "ページ " + page.position + "（" + d.label + "）" });
    if (page.image_url) {
      frame.appendChild(h("img", { src: page.image_url, alt: "ページ " + page.position + ": " + (page.title || ""), loading: page.position <= 2 ? "eager" : "lazy" }));
    } else {
      frame.appendChild(frameEmpty(d, caps, article, ctx.reasonIds));
    }
    // スライドと版スタックを横に並べる器。列の幅は CSS が決める（画面に幅の数字を書かない）
    article.appendChild(h("div", { class: "page__stage" }, frame, versionStack(page, ctx)));
    // 比較ビューは画像のある版が 2 つ以上あるページだけに出す。
    var comparison = compareBox(page, null);
    if (comparison) { article.appendChild(comparison); }

    article.appendChild(editbox(caps, "ページ " + page.position,
      { reasonId: ctx.reasonIds && ctx.reasonIds.page_instruction,
        canMinor: page.can_minor, currentImage: page.image_url,
        onSubmit: function (box, btn) { ctx.submit(page.slide_id, box, btn); } }));

    // 「情報が多い」への答えは**捨てる**ではなく**畳む**。名前もサーバから貰う
    var det = detailsBlock(page);
    var recordLabel = cardWords().record_label || "";
    var toggle = h("button", { class: "btn btn--small btn--ghost record-toggle", type: "button", "aria-expanded": "false",
      text: "▸ " + recordLabel });
    toggle.addEventListener("click", function () {
      det.hidden = !det.hidden;
      toggle.setAttribute("aria-expanded", det.hidden ? "false" : "true");
      toggle.textContent = (det.hidden ? "▸ " : "▾ ") + recordLabel;
    });
    article.appendChild(toggle);
    article.appendChild(det);
    return article;
  }

  // ---- 掴んで並べ替える（Pointer Events）----
  // HTML5 の drag events を使わない理由: (1) 修正指示の欄が「外から来た画像」の受け皿になるので、
  // 並べ替えも dataTransfer を使うと全ドロップ先で「ファイルかページか」を判別する分岐が生える。
  // pointer なら **dataTransfer が絡む drop は外から来た画像だけ** という不変条件が保てる。
  // (2) タッチで dragstart が発火しない。(3) 落ちる先の見せ方をブラウザに任せられない。
  // (4) 一覧の端での自動スクロールが無い。
  //
  // 実測して決めた 2 点:
  // - **指に付いてくる分身は作らない。** 分身を動かすには要素に位置を書き込むしかないが、この画面の
  //   CSP は `default-src 'self'` で `style-src` を緩めていないので、要素に書いた見た目はブラウザが
  //   捨てる（実ブラウザで「Applying inline style violates ...」を確認）。代わりに**掴んでいる 1 枚
  //   そのものが指の下で動く**（掴んだ印を付けたまま、落ちる先へその場で入る）。分身より曖昧さが少ない:
  //   見えているものが、そのまま落ちる場所である。
  // - **pointer capture に頼らない。** 落ちる先へ入れ替えるために節点を動かすと、その時点で capture が
  //   外れて以後の pointermove / pointerup が別の要素へ行く（1 段動いたところで固まった）。
  //   捕まえる代わりに **document で拾って pointerId で絞る**。
  var DRAG_THRESHOLD = 6;    // これだけ動いたら掴んだことにする（押しただけのクリックを潰さない）
  var DRAG_HOLD_MS = 250;    // 動かさずに押し続けても掴める（タッチ）
  var EDGE_ZONE = 36;        // 端からこの内側でポインタを止めると自動スクロール
  function edgeStep() { return SS.reducedMotion ? 5 : 12; }  // 動きは消さず遅くする

  function enableDragReorder(cfg) {
    var list = cfg.list;         // .thumbs__list
    var scroller = cfg.scroller; // .thumbs（縦でも横でもここがスクロールする器）
    var st = null;

    function horizontal() {
      // 狭い画面で横帯になるかは CSS が決める。画面側に幅の数字を持たない
      return (window.getComputedStyle(list).flexDirection || "").indexOf("row") === 0;
    }

    function onDown(e) {
      if (st || (e.button !== undefined && e.button !== 0)) { return; }
      if (!cfg.canReorder()) { return; }
      var thumb = e.target.closest ? e.target.closest(".thumb") : null;
      if (!thumb || !list.contains(thumb)) { return; }
      st = { id: thumb.dataset.slide, thumb: thumb, li: thumb.parentNode, pointerId: e.pointerId,
             x: e.clientX, y: e.clientY, on: false, hold: null, raf: null };
      st.hold = setTimeout(function () { begin(); move(st.x, st.y); }, DRAG_HOLD_MS);
      // 節点を動かすと pointer capture が外れるので、document で拾って pointerId で絞る
      document.addEventListener("pointermove", onMove, true);
      document.addEventListener("pointerup", onUp, true);
      document.addEventListener("pointercancel", onCancel, true);
      document.addEventListener("keydown", onKey, true);
    }

    function mine(e) { return st && (e.pointerId === undefined || e.pointerId === st.pointerId); }

    function begin() {
      if (!st || st.on) { return; }
      clearTimeout(st.hold);
      st.hold = null;
      st.on = true;
      cfg.onGrab(st.id);
      st.li.classList.add("thumb-slot");          // 掴んでいる 1 枚（指の下で動く）
      list.classList.add("thumbs__list--dragging");
      st.thumb.setAttribute("aria-grabbed", "true");
      cfg.say(reorderWords().grabbed, reorderWords().drop_here);
    }

    function move(cx, cy) {
      if (!st || !st.on) { return; }
      var across = horizontal();
      var at = across ? cx : cy;
      var target = null;
      var items = list.children;
      for (var i = 0; i < items.length; i++) {
        if (items[i] === st.li) { continue; }
        var b = items[i].getBoundingClientRect();
        var mid = across ? b.left + b.width / 2 : b.top + b.height / 2;
        if (at < mid) { target = items[i]; break; }
      }
      if (target) {
        if (target.previousSibling !== st.li) { list.insertBefore(st.li, target); cfg.onSlot(); }
      } else if (list.lastChild !== st.li) {
        list.appendChild(st.li); cfg.onSlot();
      }
      autoScroll(cx, cy, across);
    }

    function autoScroll(cx, cy, across) {
      var box = scroller.getBoundingClientRect();
      var at = across ? cx : cy;
      var lo = (across ? box.left : box.top) + EDGE_ZONE;
      var hi = (across ? box.right : box.bottom) - EDGE_ZONE;
      var by = at < lo ? -edgeStep() : (at > hi ? edgeStep() : 0);
      if (st.raf) { cancelAnimationFrame(st.raf); st.raf = null; }
      if (!by) { return; }
      st.raf = requestAnimationFrame(function () {
        if (!st || !st.on) { return; }
        if (across) { scroller.scrollLeft += by; } else { scroller.scrollTop += by; }
        move(cx, cy);  // 器が動いたので落ちる先を測り直す
      });
    }

    function onMove(e) {
      if (!mine(e)) { return; }
      if (!st.on) {
        if (Math.abs(e.clientX - st.x) < DRAG_THRESHOLD && Math.abs(e.clientY - st.y) < DRAG_THRESHOLD) { return; }
        begin();
      }
      move(e.clientX, e.clientY);
    }

    function finish(commit) {
      if (!st) { return; }
      var s = st;
      st = null;
      clearTimeout(s.hold);
      if (s.raf) { cancelAnimationFrame(s.raf); }
      document.removeEventListener("pointermove", onMove, true);
      document.removeEventListener("pointerup", onUp, true);
      document.removeEventListener("pointercancel", onCancel, true);
      document.removeEventListener("keydown", onKey, true);
      if (!s.on) { return; }             // 閾値未満。押しただけなのでクリックに任せる
      s.li.classList.remove("thumb-slot");
      list.classList.remove("thumbs__list--dragging");
      s.thumb.removeAttribute("aria-grabbed");
      cfg.onRelease();
      if (commit) { cfg.commit(s.id); } else { cfg.cancel(); }
    }

    function onUp(e) {
      if (!mine(e)) { return; }
      var dragged = st.on;
      finish(true);
      if (dragged) { swallowClick(); }
    }
    function onCancel(e) { if (mine(e)) { finish(false); } }  // 掴みを奪われたまま固まらせない
    function onKey(e) { if (e.key === "Escape") { e.preventDefault(); finish(false); } }

    // 掴んだあとの pointerup は click を連れてくる。1 回だけ食べる（中央が飛ばないように）
    function swallowClick() {
      var eat = function (ev) { ev.stopPropagation(); ev.preventDefault(); };
      list.addEventListener("click", eat, true);
      setTimeout(function () { list.removeEventListener("click", eat, true); }, 0);
    }

    list.addEventListener("pointerdown", onDown);
    // サムネの縮小画像はブラウザ標準の「画像をドラッグ」を持っている。放っておくとそれが始まって
    // pointer の流れが pointercancel で切られ、1 段動いたところで掴みが外れる（実ブラウザで確認）。
    // ここで断つ。**画面が dataTransfer を使うのは「外から来た画像」を受けるときだけ**という
    // 切り分けは保ったまま（並べ替えは何も setData しない）
    list.addEventListener("dragstart", function (e) { e.preventDefault(); });
  }

  // ---- 左のサムネイル一覧 ----
  // Alt + 矢印で 1 段動かす。素の矢印は焦点の移動とスクロールで埋まっているので使わない
  // （見ようとしただけで構成が変わるのを避ける）。掴む操作は enableDragReorder が足す。
  var KEY_STEP = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 };

  function thumbLabel(position, title, stateLabel) {
    return "ページ " + position + " " + title + "（" + stateLabel + "）";
  }

  function thumbsAside(pages, caps, ctx) {
    var canReorder = SS.cap(caps, "reorder_pages");
    var aside = h("aside", { class: "thumbs", "aria-label": "ページ一覧" });
    // **掴めることは案内しない**（2026-09-11、利用者の決定）: プレゼンソフトと操作感が同じなので、
    // 案内文も取っ手の印も置かず、操作そのもので分かるようにする。掴む仕組み・Alt + 矢印は残す。
    // 並べ替えられないスライドでは理由を 1 行だけ出し、一覧からその行を指す（黙って動かないのを避ける）
    var ol = h("ol", { class: "thumbs__list" });
    if (!canReorder.available && canReorder.reason) { ol.setAttribute("aria-describedby", "reorder-why"); }
    pages.forEach(function (p) {
      // 絵だけ。**状態の印はここには置かない**（2026-09-11、利用者の決定: この列では操作しないので、
      // 状態はカードのヘッダで見せる）。読み上げのラベルには状態の語を残す（目に見えず、うるさくない）
      var pic = h("span", { class: "thumb__pic", "aria-hidden": "true" });
      if (p.image_url) { pic.appendChild(h("img", { src: p.image_url, alt: "", loading: "lazy" })); }
      var b = h("button", { class: "thumb" + (p.hidden ? " thumb--hidden" : ""), type: "button", dataset: { slide: p.slide_id },
        title: canReorder.available ? null : (canReorder.reason || null),
        "aria-label": thumbLabel(p.position, p.title || "", p.display.label),
        onclick: function () { ctx.scrollTo(p.slide_id); } },
        // 番号は絵の**左の欄**に置く（重ねない。重ねると見出しの頭を隠す。2026-09-18）。
        // 振り直しは renumber() が同じ節点を書き換える。狭い画面では絵が消えて札だけが並ぶ（CSS が決める）
        h("span", { class: "thumb__num", text: String(p.position) }), pic);
      b.addEventListener("keydown", function (e) {
        if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) { return; }
        var step = KEY_STEP[e.key];
        if (!step) { return; }
        e.preventDefault();  // ブラウザの戻る/進むやスクロールに取られない
        ctx.moveBy(p.slide_id, step);
      });
      ol.appendChild(h("li", null, b));
    });
    aside.appendChild(ol);
    // 並べ替えの途中と結果を言う場所（黙って保存しない・黙って戻さない）。
    // **件数の 4 区分と、`pause` / `regenerate_failed` のボタンはここには置かない**
    // （件数は上の進みの帯が出す。押せないボタンを並べると要望の「情報がおおい」を生み直す）
    aside.appendChild(h("div", { class: "thumbs__reorder" },
      h("p", { class: "thumbs__status small", role: "status", "aria-live": "polite" }),
      h("p", { class: "thumbs__why reason", id: "reorder-why",
               text: canReorder.available ? "" : (canReorder.reason || "") })));
    return aside;
  }

  // ---- 全体の進み（上部の帯。UI.md §6）----
  // 件数は counts の値と labels をそのまま出す。画面では数え直さない・率や見込みの時間を作らない（U11）
  // ---- 企画の進み（待っている人のための帯）----
  // 会話から頼んだ人は、この画面を開いたまま企画が終わるのを待つ。その間ページは 1 枚も無いので、
  // ページの状態からは「動いているのか止まっているのか」が一切読めない（2026-09-14 の実害:
  // 企画に 71 秒かかる間、画面は「まだページがありません」としか言わなかった）。
  // **文言はサーバだけが持つ**（`planning.message` をそのまま出す。`limit.message` と同じ作法で、
  // 画面には文を書かない）。**あと何秒かは出さない**（企画にかかる長さは測れないので断定しない）。
  function planningBand(planning) {
    var band = h("div", { class: "notice planning-band", role: "status", "aria-live": "polite" },
      h("p", { class: "planning-band__text" }));
    paintPlanning(band, planning);
    return band;
  }

  function paintPlanning(band, planning) {
    if (!band) { return; }
    var p = planning || {};
    var message = p.message || "";
    // 出すことが無いとき（企画が終わっている・そもそも企画タスクが無い）は器ごと畳む。
    // 器を作り直さないのは、実況が 2 秒ごとに当て直すため（帯が消えたり出たりしても位置が動かない）
    band.hidden = !message;
    // 動きのある表現は「動いている間」だけ（prefers-reduced-motion では CSS が振幅を小さく・遅くする）
    band.classList.toggle("planning-band--active", !!p.active);
    // 企画できなかったときは色だけに頼らず、文（サーバの 1 文）と警告の器で示す。**黙って止まらない**
    band.classList.toggle("notice--warn", p.state === "failed");
    var head = band.querySelector(".planning-band__text");
    if (head.textContent !== message) { head.textContent = message; }
  }

  function progressBand(counts) {
    var band = h("div", { class: "notice progress-band", role: "status", "aria-live": "polite" },
      h("p", { class: "progress-band__text" }),
      h("p", { class: "progress-band__groups small" }));
    paintBand(band, counts);
    return band;
  }

  function paintBand(band, counts) {
    if (!band || !counts) { return; }
    var labels = groupLabels(counts);
    var total = counts.total || 0;
    var head = band.querySelector(".progress-band__text");
    // 1 行目の**文はサーバが作る**（「画像を生成しています。…」を含む。正本は vocabulary.progress_headline）。
    // 画面は出すだけで、いま作っている最中かどうかの判断も文の組み立てもしない（U9 / U11）
    var text = counts.headline
      || (total ? total + " ページ中 " + (counts.ready || 0) + " ページが" + (labels.ready || "ready") : "ページなし");
    if (head.textContent !== text) { head.textContent = text; }
    var line = band.querySelector(".progress-band__groups");
    var parts = GROUP_ORDER.map(function (g) { return (labels[g] || g) + " " + (counts[g] || 0); }).join(" · ");
    if (line.textContent !== parts) { line.textContent = parts; }
  }

  // ---- 取り直した状態を、その場のページに当てる（画面は作り直さない: スクロール位置と選択を保つ）----
  // **版スタックは実況では触らない。**/progress はまだでき上がっていない候補だけを返すので、でき上がった版の
  // 番号と絵の URL は載ってこない。スタックへ行を足すのは、押せるようにする段で
  // `GET /api/decks/<id>` を読み直して当てる。ここで中途半端に触ると、開いている畳みが閉じる
  // `live` は「2 秒ごとの実況から呼ばれた」の意味。採用の答えを当てるときは偽で呼ぶ。
  // **採用の答えを当てた直後の 1 回だけ、そのページを実況で塗らない**（`justAdopted`）:
  // 採用の前に飛んでいた /progress の答えが後から届くと、選ぶ前の絵を描き戻してしまう
  // （2 秒ごとの取り直しと採用は競走する）。次の取り直しでは新しい絵が来るので、抑えるのは 1 回でよい。
  // **抑制を見るのは実況の経路だけ**にする: 自分の当て込みで消費すると、自分の絵が塗られない
  // （実ブラウザで撮って見つけた: 絵が purple のまま「使用中」だけ v1 に移っていた）
  // 空枠の中身を作り直すかどうかの印。**状態の文だけでは足りない**: 空枠の中の「作り直す」が
  // 押せるかは `capabilities` が決めるので、状態が同じままでも答えが変われば当て直す必要がある。
  // だから「今なにができるか」の版（`capsGen`）を印に混ぜる（当て直すたびに 1 つ上がる）。
  function emptySig(d, gen) { return d.key + "|" + d.label + "|" + (d.reason || "") + "|" + gen; }

  function paintPage(pctx, page, live) {
    var n = pctx.nodes[page.slide_id];
    if (!n) { return; }  // この描画に無いページ（構成が変わった）は触らない。変化は別に知らせる
    if (live && pctx.justAdopted[page.slide_id]) { delete pctx.justAdopted[page.slide_id]; return; }
    var d = page.display;
    var frame = n.frame;
    var cls = "frame frame--" + d.key;
    if (frame.className !== cls) { frame.className = cls; }
    var pos = pctx.positionOf(page.slide_id);
    frame.setAttribute("aria-label", "ページ " + pos + "（" + d.label + "）");
    // ヘッダの状態は常設なので、実況でもここを追う（絵が出たら印だけ消える、をなくした代わり）
    if (n.headKey !== d.key + "|" + d.label) {
      var state = n.article.querySelector(".page__state");
      if (state) {
        SS.clear(state).appendChild(mark(d));
        state.appendChild(h("span", { class: "page__state-label", text: d.label }));
        n.headKey = d.key + "|" + d.label;
      }
    }
    if (page.image_url) {
      var img = frame.querySelector("img");
      if (!img) {
        // 空枠 → 画像。枠の位置は動かさず、出来た順に埋まる（UI.md §6）
        SS.clear(frame).appendChild(h("img", { src: page.image_url, alt: "ページ " + pos + ": " + n.title, loading: "lazy" }));
        n.sig = null;
      } else if (img.getAttribute("src") !== page.image_url) {
        img.setAttribute("src", page.image_url);
      }
    } else {
      var sig = emptySig(d, pctx.capsGen);
      // 枠の中を操作している間は差し替えない（押しかけたボタンをその場で消さない）
      if (n.sig !== sig && !frame.contains(document.activeElement)) {
        SS.clear(frame).appendChild(frameEmpty(d, pctx.caps, n.article, pctx.reasonIds));
        n.sig = sig;
      }
    }
    paintThumb(pctx, page, d, n, pos);
  }

  function paintThumb(pctx, page, d, n, position) {
    if (page.slide_id === pctx.grabbed) { return; }  // 掴んでいる間はその 1 枚を差し替えない
    var thumb = pctx.aside.querySelector('.thumb[data-slide="' + page.slide_id + '"]');
    if (!thumb) { return; }
    n.stateLabel = d.label;
    thumb.setAttribute("aria-label", thumbLabel(position, n.title, d.label));
    var pic = thumb.querySelector(".thumb__pic");
    var img = pic.querySelector("img");
    if (page.image_url) {
      if (!img) { pic.insertBefore(h("img", { src: page.image_url, alt: "", loading: "lazy" }), pic.firstChild); }
      else if (img.getAttribute("src") !== page.image_url) { img.setAttribute("src", page.image_url); }
    } else if (img) {
      img.remove();
    }
  }

  // ---- 実況の取り直し（R6。UI.md §6）----
  // 働いている間は 2 秒ごと、働いていない間は 30 秒ごとに /progress を読み直す。
  // last_event_seq が動いたときだけ描き直す。タブが隠れている間は取りに行かず、戻ったらすぐ 1 回取る。
  // 「ほかの場所で構成が変わった」を言う場所は 2 つ（実況で気づいたとき / 並べ替えを断られたとき）
  var REVISION_CHANGED = "このスライドの構成が、ほかの場所で変わりました。並びを最新にするには再読み込みしてください。";

  var TICK_MS = 2000;        // 基本の間隔（これより短くしない）
  var TICK_MAX_MS = 30000;   // つながらないときに広げる上限
  var GIVE_UP_AFTER = 5;     // 連続でこれだけ取れなければ、画面に知らせて止める
  // **働いていない間も、ゆっくり見に行く**（2026-09-18）。ここが止まると、ほかの場所で起きた変化に
  // 気づけない。会話から修正指示を送れるようになった（ROADMAP P10 の `revise`）ので、開いたままの
  // 画面の外で仕事が始まることが普通になった。止まったままだと「リロードしないと進まない」に戻る。
  var IDLE_MS = 30000;
  var watcher = null;        // 画面に 1 つだけ（デッキを切り替えても二重に走らせない）

  // 取り直しを続ける条件。**画面は数えない。サーバの 1 つの答え（`live.working`）に従うだけ**。
  // かつてここは `unfinished(data.batch) || data.planning.active` だった。どちらも**無いことを根拠に
  // した推論**で、企画ができてから batch が作られるまでの間（実測 30 ミリ秒。監督が詰まればもっと開く）に
  // 取り直しが 1 回当たると「もう働いていない」と結論して止まり、待っている人の画面は二度と
  // 動かなかった（2026-09-14 の実害。利用者「リロードしないと進まないんですか？」）。
  // 継ぎ目の長さを当てにした直し方（間隔を縮める・数回は余分に取る）はどれも当て推量なので採らない。
  // 正本は `server/liveness.py`。終端の batch で止まる約束はそこが引き継いでいる。
  function busy(data) { return !!(data.live && data.live.working); }

  function stopWatch() { if (watcher) { watcher.stop(); } watcher = null; }

  function startWatch(cfg) {
    stopWatch();
    var timer = null, mo = null, dead = false, fails = 0, delay = TICK_MS, seq = cfg.seq;
    var idle = false;          // 「いま働いていない」= ゆっくり見に行く番。断りの帯も出さない
    var w = { stop: stop };

    function clearTimer() { if (timer) { clearTimeout(timer); timer = null; } }

    function stop() {
      dead = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", stop);
      if (mo) { mo.disconnect(); mo = null; }
      if (watcher === w) { watcher = null; }
    }

    function arm(ms) {
      clearTimer();
      if (dead || document.visibilityState === "hidden") { return; }
      timer = setTimeout(tick, ms);
    }

    function giveUp(key, message) {
      var alive = cfg.guard();
      stop();
      if (alive) { cfg.notify(key, message); }
    }

    function tick() {
      timer = null;
      if (dead) { return; }
      if (!cfg.guard()) { stop(); return; }                   // 画面が入れ替わった
      if (document.visibilityState === "hidden") { return; }  // 見えていない間は取りに行かない
      SS.api("/api/decks/" + cfg.deckId + "/progress").then(function (data) {
        if (dead) { return; }
        if (!cfg.guard()) { stop(); return; }
        fails = 0;
        delay = TICK_MS;                                      // つながったので間隔を戻す
        if (data.last_event_seq !== seq) {                    // 動いていなければ描き直さない（ちらつかせない）
          seq = data.last_event_seq;
          cfg.paint(data);
        }
        idle = !busy(data);
        arm(idle ? IDLE_MS : delay);                          // 働いていない間もゆっくり見に行く
      }, function (e) {
        if (dead) { return; }
        if (!cfg.guard()) { stop(); return; }
        if (e && e.status === 404) {
          giveUp("gone", "このスライドは開けなくなりました。ほかの場所で削除されたか、URL が変わった可能性があります。");
          return;
        }
        if (idle) {
          // **待っていない画面に、つながらないことを知らせない。** 何も起きていないときの通信断は
          // 利用者の用事と無関係で、黙って次の様子見に回るのが正しい（帯を出すと驚かせるだけ）
          arm(IDLE_MS);
          return;
        }
        fails += 1;
        if (fails >= GIVE_UP_AFTER) {
          giveUp("stalled", "この画面の自動更新を止めました。サーバとやり取りできないままです。つながったら再読み込みしてください。");
          return;
        }
        delay = Math.min(delay * 2, TICK_MAX_MS);             // つながらない間は間隔を広げる
        arm(delay);
      });
    }

    function onVisibility() {
      if (dead) { return; }
      if (!cfg.guard()) { stop(); return; }
      if (document.visibilityState === "hidden") { clearTimer(); return; }
      tick();  // 戻ってきたらすぐ 1 回取る（会話で頼んだ直後にタブへ戻る、がいちばん多い）
    }

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", stop);
    if (window.MutationObserver && cfg.screen.parentNode) {
      // 画面が差し替わったら（別のデッキ・別の画面へ移ったら）タイマーを残さずその場で止める
      mo = new MutationObserver(function () { if (!cfg.screen.isConnected) { stop(); } });
      mo.observe(cfg.screen.parentNode, { childList: true });
    }
    watcher = w;
    // **働いていなくても始める。** 始めないと、開いたままの画面はほかの場所で起きたことに永久に気づかない
    idle = !cfg.active;
    arm(idle ? IDLE_MS : delay);
    return w;
  }

  // ---- 画面 ----
  SS.views.deck = function (root, route, guard) {
    SS.clear(root).appendChild(h("p", { class: "muted", text: "読み込んでいます…" }));
    var deckId = route.deckId;
    return SS.api("/api/decks/" + deckId).then(function (data) {
      if (!guard()) { return; }
      render(data);
    });

    function render(data) {
      var caps = data.capabilities || {};
      var deck = data.deck;
      var reasons = screenReasons(caps);
      var ctx = { caps: caps, pages: data.pages, reasonIds: reasons.ids };
      var reload = function () { if (guard()) { SS.views.deck(root, route, guard); } };
      // 並べ替えが握る 4 つ。答えが返るたびに握り直す（握りっぱなしにすると 2 回目が必ず断られる）
      var order = data.pages.map(function (p) { return p.slide_id; });  // いま画面に出ている並び
      var confirmed = order.slice();                                   // サーバが返した最後の並び
      var lockVersion = deck.lock_version;
      var revisionId = data.revision ? data.revision.id : null;
      // 自分の操作が作った構成版を覚えておく。並べ替えも採用も新しい構成版を作るので、**送る前に
      // 飛んでいた実況の答えが後から届くと、自分の操作を「ほかの場所で変わりました」と誤報する**
      var ownRevisions = {};
      var seenRevision = function (id) { if (id) { revisionId = id; ownRevisions[id] = true; } };
      seenRevision(revisionId);
      ctx.order = function () { return order; };
      ctx.visibleCount = function () { return data.pages.filter(function (p) { return !p.hidden; }).length; };
      ctx.positionOf = function (slideId) { return order.indexOf(slideId) + 1; };
      var mutate = function (p) { return p.then(function () { reload(); }).catch(SS.toastError); };
      // 連打で batch が 2 つ起きないように、この描画の間は 1 回だけ通す
      var resuming = false;
      ctx.resume = function (btn) {
        if (resuming) { return; }
        resuming = true;
        btn.disabled = true;
        SS.api("/api/decks/" + deckId + "/resume", { method: "POST" }).then(function (r) {
          if (r && r.message) { SS.toast(r.message); }
          reload();  // 結果はサーバから読み直す（画面側で状態を作らない）
        }).catch(function (e) {
          resuming = false;
          btn.disabled = false;
          SS.toastError(e);
        });
      };
      ctx.duplicatePage = function (slideId) { mutate(SS.api("/api/decks/" + deckId + "/pages/" + slideId + "/duplicate", { method: "POST", body: { lock_version: lockVersion } })); };
      ctx.setPageHidden = function (slideId, hidden) {
        if (hidden) { sessionStorage.setItem("slide-card-collapsed:" + slideId, "1"); }
        mutate(SS.api("/api/decks/" + deckId + "/pages/" + slideId + "/visibility", { method: "POST", body: { hidden: hidden, lock_version: lockVersion } }).then(function (r) { if (r.message) { SS.toast(r.message); } }));
      };
      ctx.scrollTo = function (slideId) {
        var el = document.getElementById("page-" + slideId);
        if (el) { el.scrollIntoView({ behavior: SS.reducedMotion ? "auto" : "smooth", block: "start" }); }
      };
      // 押す前に確認できること（UI.md §6）: 形式・書き出すページ数（非表示は除く）・各ページは使用中の版を
      // 集めて使うこと・PPTX は画像として入ること。**4 つとも同じ 1 つの確認文にまとめてある**
      // （正本は `vocabulary.EXPORT_WORDS.confirm`。画面は {format}/{n} を埋めるだけ）。
      // 待っている間はボタン自身の文言を変えて黙って固まらせない（数秒で終わるが、何もしていない
      // ように見えるのを避ける）。連打で二重に投げないよう、応答が返るまでボタンを掴む。
      // **確認のダイアログは挟まない**（2026-09-18、利用者の指示）。押したら書き出す。
      // 形式・枚数・使用中の版は押す前から画面に出ているので、もう一度読ませる意味がない。
      // 終わったときの知らせも 1 行だけ（同日の指示。文は `vocabulary.EXPORT_WORDS`）
      ctx.exportDeck = function (btn, format) {
        var words = exportWords();
        var original = btn.textContent;
        btn.disabled = true;
        btn.textContent = words.building || original;
        SS.api("/api/decks/" + deckId + "/exports", { method: "POST", body: { format: format } }).then(function (r) {
          if (words.success) { SS.toast(words.success); }
          // ダウンロードはサーバの `Content-Disposition: attachment` が実行する（新しいウィンドウ・
          // タブは開かない。既に版の画像保存で使っている手口と同じ。line 693 相当）
          var a = h("a", { href: r.export.download_url, download: true });
          document.body.appendChild(a);
          a.click();
          a.remove();
        }).catch(SS.toastError).finally(function () {
          btn.disabled = false;
          btn.textContent = original;
        });
      };

      SS.clear(root);
      var screen = h("div", { class: "deck-screen" });
      var deckMenuBtn = h("button", { class: "btn btn--icon", type: "button", "aria-label": "スライドの操作", "aria-haspopup": "menu", text: "⋮" });
      // 形式ごとの**独立したボタン**を横に並べる（2026-09-18、利用者の指示。メニューをやめた。
      // 1 回押せば書き出しが始まるものを、2 回押させる形にしていた）。承認済みの初期提供範囲は
      // 全体のみで、ページを選ぶ書き出しはまだ無いので、出すのは形式の 2 つだけである。
      // **可否は 1 つ（`caps.export`）を 2 つのボタンが分け合う。** 文言だけが形式ごとに変わる
      // （その正本もサーバの `vocabulary.export_formats`）。名前を付けて持つのは、答えが変わったら
      // `paintCaps` が同じボタンに当て直すためで、作り直さない
      var exportButtons = {};
      ["pdf", "pptx"].forEach(function (format) {
        var b = SS.disabledButton(caps, "export", "btn--export btn--export-" + format, exportFormatLabel(format));
        b.addEventListener("click", function () { ctx.exportDeck(b, format); });
        exportButtons[format] = describedBy(b, reasons.ids, "export");
      });
      // タイトルはその場で直せる（2026-09-18、利用者の指示）。**モーダルを開かない。**
      // 名前を変える口は前からあり（`POST /api/decks/<id>/title`。カードのメニュー「名前を変更」が使う）、
      // ここが足すのは入口だけである。断りの文・長さの上限・lock_version の扱いは口の側のまま変えない。
      //
      // 見出しの h1 と入力欄を入れ替えるのではなく、**同じ箱の中で 2 つを出し入れする**（`hidden`）。
      // 入れ替えると、実況の当て直しが掴んでいる要素が途中で消えて、書きかけの題が飛ぶ。
      var titleText = h("h1", { text: deck.title });
      var titleInput = h("input", { class: "deck-title-input", type: "text", value: deck.title,
                                    "aria-label": "スライドの名前", maxlength: String(deckLimits().title || 200) });
      var titlePencil = h("button", { class: "btn btn--icon btn--ghost deck-title-edit", type: "button",
                                      "aria-label": "名前を変える", title: "名前を変える" }, pageIcon("pencil"));
      titleInput.hidden = true;
      var titleBox = h("span", { class: "deck-title" }, titleText, titleInput, titlePencil);
      var showTitleInput = function (editing) {
        titleText.hidden = editing;
        titleInput.hidden = !editing;
        titlePencil.hidden = editing;
        if (editing) { titleInput.value = deck.title; titleInput.focus(); titleInput.select(); }
      };
      var savingTitle = false;
      var commitTitle = function () {
        if (savingTitle) { return; }
        var value = titleInput.value.trim();
        if (value === deck.title || !value) { showTitleInput(false); return; }  // 空欄で消させない（口も断る）
        savingTitle = true;
        titleInput.disabled = true;
        SS.api("/api/decks/" + deckId + "/title", { method: "POST", body: { title: value, lock_version: lockVersion } })
          .then(function (r) {
            deck.title = r.deck.title;          // サーバが整えた形を正とする（前後の空白など）
            lockVersion = r.deck.lock_version;
            titleText.textContent = deck.title;
            showTitleInput(false);
          })
          .catch(function (e) { SS.toastError(e); showTitleInput(true); })   // 断られたら書きかけのまま残す
          .finally(function () { savingTitle = false; titleInput.disabled = false; });
      };
      titlePencil.addEventListener("click", function () { showTitleInput(true); });
      titleInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); commitTitle(); }
        else if (e.key === "Escape") { e.preventDefault(); showTitleInput(false); }
      });
      titleInput.addEventListener("blur", commitTitle);
      // この画面は名前を鉛筆でその場で直せるので、メニューには「名前を変更」を出さない（2026-09-18）
      deckMenuBtn.addEventListener("click", function () { SS.menu(deckMenuBtn, SS.deckActions(deck, caps, reload, { renameInPlace: true })); });
      // 3 段に積む（2026-09-18、利用者の指示）: パンくず / **大きいタイトルと同じ段の右端に書き出し** /
      // 状態・構成・ブランド。副題（`subtitle` / `purpose`）はここに出さないことにした
      // （企画の言い直しで、タイトルのすぐ下で二度読ませる価値がない）。値は API に残っている
      screen.appendChild(h("div", { class: "deck-head" },
        h("p", { class: "crumbs" }, h("a", { href: "/", "data-nav": true, text: "ホーム" }), " / ", h("a", { href: "/slides", "data-nav": true, text: "スライド" })),
        h("div", { class: "deck-title-row" },
          titleBox,
          h("span", { class: "deck-actions" },
            exportButtons.pdf,
            exportButtons.pptx,
            h("span", { class: "menu-anchor" }, deckMenuBtn))),
        h("div", { class: "deck-meta" },
          // スライドの状態の印は出さない（2026-09-18、利用者「いらないね」）。状態は 3 つあるが
          // （語の正本は vocabulary.py）、**切り替える口が画面のどこにも無い**ので、どのスライドも
          // 永久に同じ 1 つを表示するだけだった。状態を切り替える機能を作る日に戻す
          deck.pinned ? h("span", { class: "chip", text: "ピン留め" }) : null,
          data.revision ? h("span", { class: "muted small deck-revision", text: pageCountText(data.pages) }) : h("span", { class: "muted small", text: "ページなし" }),
          h("span", { class: "muted small", text: deck.brand ? "ブランド: " + deck.brand.name : "" }))));

      // 企画の帯はいちばん上。待っている人がまず読むのは「いま何をしているか」である
      var planBand = planningBand(data.planning);
      screen.appendChild(planBand);
      var band = limitBanner(data.limit, ctx);
      if (band) { screen.appendChild(band); }
      var pband = data.pages.length ? progressBand(data.counts) : null;
      if (pband) { screen.appendChild(pband); }
      // 再開のボタンは進みの帯のすぐ下（止まっている帯の中のボタンと同じ「状態の文のすぐ下」に置く）
      var rrow = resumeRow(data.limit, ctx);
      if (rrow) { screen.appendChild(rrow); }
      // まだ使えない操作の理由は、ここに 1 か所だけ（同じ文が押せない部品の数だけ並ぶのを避ける）
      if (reasons.node) { screen.appendChild(reasons.node); }

      var body = h("div", { class: "deck-body" });
      var aside = thumbsAside(data.pages, caps, ctx);
      body.appendChild(aside);
      var instr = deckInstruction(caps, reasons.ids, { canMinor: deckCanMinor(data.pages),
        onSubmit: function (box, btn) { ctx.submitDeck(box, btn); } });
      body.appendChild(instr);
      var pagesEl = h("section", { class: "pages", "aria-label": "ページ" });
      if (!data.pages.length) {
        pagesEl.appendChild(h("div", { class: "empty", text: "このスライドにはまだページがありません。" }));
      }
      // 実況で差分を当てるための参照（slide_id → 枠・サムネ・いま出している状態）
      var nodes = {};
      // 隙間に足す（P11。ADR 0048）。**先頭の隙間は after_slide_id = null**
      SS.append(pagesEl, insertGap(null, ctx, data, reload));
      data.pages.forEach(function (p) {
        var art = pageArticle(p, ctx);
        nodes[p.slide_id] = { article: art, frame: art.querySelector(".frame"), title: p.title || "",
          stateLabel: p.display.label, headKey: p.display.key + "|" + p.display.label,
          sig: p.image_url ? null : emptySig(p.display, 0) };
        pagesEl.appendChild(art);
        SS.append(pagesEl, insertGap(p.slide_id, ctx, data, reload));
      });
      body.appendChild(pagesEl);
      screen.appendChild(body);
      root.appendChild(screen);

      // 右のスクロールに合わせて、見えているページのサムネイルを強調する
      var current = null;
      var setCurrent = function (slideId) {
        if (slideId === current) { return; }
        current = slideId;
        aside.querySelectorAll(".thumb").forEach(function (b) {
          var on = b.dataset.slide === slideId;
          b.classList.toggle("thumb--current", on);
          if (on) { b.setAttribute("aria-current", "true"); b.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" }); } else { b.removeAttribute("aria-current"); }
        });
      };
      if ("IntersectionObserver" in window) {
        var ratios = {};
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (en) { ratios[en.target.dataset.slide] = en.isIntersecting ? en.intersectionRatio : 0; });
          var best = null, bestR = 0;
          data.pages.forEach(function (p) { var r = ratios[p.slide_id] || 0; if (r > bestR) { bestR = r; best = p.slide_id; } });
          if (best) { setCurrent(best); }
        }, { threshold: [0, 0.25, 0.5, 0.75, 1], rootMargin: "-" + (document.querySelector(".topbar").offsetHeight + 44 + 16) + "px 0px -20% 0px" });
        pagesEl.querySelectorAll(".page").forEach(function (el) { io.observe(el); });
      }
      if (data.pages.length) { setCurrent(data.pages[0].slide_id); }

      // 全体指示: スクロールで 1 行に縮む（先頭付近では開いた状態）
      // 狭い画面（900px 未満）では 1 行に縮んだ状態を既定にする（UI.md §6「狭い画面」）
      var onScroll = function () {
        var compact = window.scrollY > 24 || window.innerWidth < 900;
        instr.classList.toggle("is-compact", compact);
      };
      window.addEventListener("resize", onScroll, { passive: true });
      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();

      // ---- 実況をつなぐ（開いた瞬間の静止画にしない）----
      var noticed = {};
      // 差し込みを頼んだ控え（task_id → true）。**この画面が押したものだけ**を入れる。
      // 別のタブや会話から足されたものは入らないので、そちらは今までどおり「ほかの場所で」と言う。
      // 描き直した時点で飛んでいたものを控える（送信の直後は `reload()` が走るので、
      // **押した本人の依頼はここに必ず載る**）。別のタブが頼んだものも入るが、
      // 差し込みは「ページが増える」だけなので、黙って反映して困らない。
      var ownInserts = {};
      (data.pending_inserts || []).forEach(function (t) { ownInserts[t.task_id] = true; });
      var ownInsertLanded = function (prog) {
        var still = {};
        (prog.pending_inserts || []).forEach(function (t) { still[t.task_id] = true; });
        var landed = Object.keys(ownInserts).filter(function (id) { return !still[id]; });
        if (!landed.length) { return false; }
        landed.forEach(function (id) { delete ownInserts[id]; });  // 一度きり
        return true;
      };
      // 書きかけがあるか（指示欄・隙間の入力欄）。**あるなら黙って作り直さない。**
      var nothingIsBeingTyped = function () {
        var boxes = screen.querySelectorAll("textarea");
        for (var i = 0; i < boxes.length; i++) {
          if (boxes[i].value.trim()) { return false; }
        }
        return true;
      };
      var notify = function (key, message) {
        if (noticed[key]) { return; }  // 同じことは一度だけ言う（黙って止まらない）
        noticed[key] = true;
        screen.insertBefore(h("div", { class: "notice notice--warn live-notice", role: "status" },
          h("p", { text: message }),
          h("p", null, h("button", { class: "btn btn--small", type: "button", text: "再読み込み", onclick: function () { SS.render(); } }))), body);
      };
      var pctx = { caps: caps, nodes: nodes, aside: aside, positionOf: ctx.positionOf, grabbed: null,
                   reasonIds: reasons.ids, justAdopted: {}, capsGen: 0 };
      var watchCfg = null;      // 実況の設定（下で作る。送信のあとに乗せ直すために名前を付けて持つ）
      var candidates = {};      // 前の取り直しで「まだ絵になっていない候補版」があったページ
      var grew = false;         // 0 枚だった画面にページが出来たので描き直しを頼んだ（二度頼まない）

      // ---- 版を選ぶ（押したらその版が使われる。**答えを待ってから絵を替える**）----
      // 並べ替えは楽観、採用は待つ。理由が違うので結論が違う: 落とすのは手の動きの終わりなので待つと
      // 「戻ってから跳ぶ」が見えるが、採用はボタン 1 回で、切り替えて見せてから戻すのは
      // **成果物について嘘をつく**ことになる。断られ方も 4 通りあって珍しくない。
      var adopting = {};   // slide_id → 答えを待っている（そのページのスタック全体を押せなくする）
      // 押している間はそのページの版を 1 つも押せなくする。**見た目の都合ではない**:
      // 採用は毎回 lock_version を上げるので、答えを待たずに次を押すと 2 回目が必ず断られる
      var stackBusy = function (slideId, busy, pressed) {
        var n = nodes[slideId];
        var stack = n && n.article.querySelector(".versions");
        if (stack) {
          stack.querySelectorAll(".version").forEach(function (b) {
            if (busy && !b.disabled) { b.dataset.held = "1"; b.disabled = true; }
            else if (!busy && b.dataset.held === "1") { b.disabled = false; delete b.dataset.held; }
          });
        }
        if (pressed) {
          if (busy) { pressed.setAttribute("aria-busy", "true"); } else { pressed.removeAttribute("aria-busy"); }
        }
      };
      // 版スタックとヘッダの版番号を当て直す。**絵（paintPage）と揃えて呼ぶ**:
      // 絵だけ替わって印が残ると、画面が「どの版が使われているか」について嘘をつく
      var paintVersions = function (page) {
        var n = nodes[page.slide_id];
        if (!n) { return; }
        var old = n.article.querySelector(".versions");
        var more = old && old.querySelector(".versions__more");
        var older = !!(more && more.getAttribute("aria-expanded") === "true");
        var focused = old && old.contains(document.activeElement) ? document.activeElement.dataset.version : null;
        var said = old ? old.querySelector(".versions__said").textContent : "";
        var next = versionStack(page, ctx);
        if (older) { var m2 = next.querySelector(".versions__more"); if (m2) { m2.click(); } }
        next.querySelector(".versions__said").textContent = said;   // 押した結果の 1 行は消さない
        if (old) { old.replaceWith(next); } else { n.article.querySelector(".page__stage").appendChild(next); }
        if (focused) {
          var back = next.querySelector('.version[data-version="' + focused + '"]');
          if (back && !back.disabled) { back.focus({ preventScroll: true }); }   // 続けて選べるようにする
        }
        var ver = n.article.querySelector(".page__ver");
        if (ver) { ver.textContent = "v" + page.version; }
        // 土台にする版は「いま映している版」。採用で動くので、ここで握り直す（送信の照合に使う）
        n.article.dataset.version = page.version_id;
        // 見比べの器も同じ版の並びから作り直す（絵だけ替わって器が古い版を映すと嘘になる）。
        // **開いていたか・どの版と見比べていたかは持ち越す**（見比べの最中に勝手に片付けない）
        var oldBox = n.article.querySelector(".compare-box");
        var oldPanel = oldBox && oldBox.querySelector(".compare");
        if (oldBox) {
          oldBox.replaceWith(compareBox(page, { open: !oldPanel.hidden, partner: oldPanel.dataset.partner }));
        }
      };
      var paintPageCount = function (revision, pages) {
        var el = screen.querySelector(".deck-revision");
        if (el && revision) { el.textContent = pageCountText(pages); }
      };
      // ---- 「今なにができるか」を画面に当てる、ただ 1 つの場所 ----
      // 送れるかは**スライドごとに倒れる**（生成が 1 本走ると、どのページも送れなくなる）。だから
      // 送信が通った直後に当て直す。当て直さないと、ほかのカードに「押せるのに断られるだけ」の
      // ボタンが残る。**理由の文はスライドに 1 か所のまま**（まとめ役を入れ替えて、各欄はその行を指し直す）。
      //
      // **`capabilities` に触る当て直しは、すべてこの関数を通す。** 2026-09-14 の実害の 3 つ目が
      // 「最初に開いた瞬間の答えを持ち続け、変わったことに気づかない」だったので、当て直す先を
      // 1 か所に集めておかないと、また片方だけ動いて凍る（利用者「すべて完了したあとにページの
      // 修正などができなかった。リロードするとできそうでした」）。当て直す先は 6 つある:
      //   1. 握っている答えそのもの（`caps` / `ctx.caps` / `pctx.caps`）。あとから開くメニュー・
      //      あとから作り直す版スタック・空枠は、押されたその時にここを読む
      //   2. 理由の文のまとめ役（スライドに 1 行）と、それを指す id
      //   3. 書き出しのボタン
      //   4. 並べ替え（サムネの列の理由と、各サムネの title）
      //   5. 修正指示の欄（デッキ全体と各ページ）。**器は作り直さない**（書きかけの文が消える）
      //   6. 空枠の中の「作り直す」（`capsGen` を上げて、次の `paintPage` に作り直させる）
      var paintCaps = function (freshCaps, freshPages) {
        caps = freshCaps || {};
        ctx.caps = caps;
        pctx.caps = caps;
        pctx.capsGen += 1;
        var next = screenReasons(freshCaps);
        if (reasons.node && next.node) { reasons.node.replaceWith(next.node); }
        else if (reasons.node) { reasons.node.remove(); }
        else if (next.node) { screen.insertBefore(next.node, body); }
        reasons = next;
        ctx.reasonIds = next.ids;
        pctx.reasonIds = next.ids;
        // 2 つのボタンが 1 つの可否を分け合うので、当て直すのも 2 つとも（文言は形式のまま保つ）
        Object.keys(exportButtons).forEach(function (format) {
          describedBy(SS.applyCap(exportButtons[format], caps, "export", exportFormatLabel(format)), next.ids, "export");
        });
        // 並べ替えの可否は列全体に効く（理由は列の下に 1 行。サムネは title で読める）
        var canReorder = SS.cap(caps, "reorder_pages");
        var why = aside.querySelector(".thumbs__why");
        if (why) { why.textContent = canReorder.available ? "" : (canReorder.reason || ""); }
        var list = aside.querySelector(".thumbs__list");
        if (list) {
          if (!canReorder.available && canReorder.reason) { list.setAttribute("aria-describedby", "reorder-why"); }
          else { list.removeAttribute("aria-describedby"); }
        }
        aside.querySelectorAll(".thumb").forEach(function (b) {
          if (canReorder.available) { b.removeAttribute("title"); }
          else if (canReorder.reason) { b.setAttribute("title", canReorder.reason); }
        });
        // 「今の絵を土台にして直せるか」（`can_minor`）も、絵ができた瞬間に変わる**可否**である。
        // どのカードの欄かは器から分かる（欄はカードの中にある）。どれでもない欄がデッキ全体の欄
        var minor = {};
        (freshPages || []).forEach(function (p) { minor[p.slide_id] = p.can_minor; });
        var setBox = function (box, key) {
          if (!box) { return; }
          var card = box.closest(".page");
          var canMinor = card ? minor[card.dataset.slide] : (freshPages ? deckCanMinor(freshPages) : null);
          box.__caps(SS.cap(freshCaps, key), canMinor || null, next.ids[key]);
        };
        setBox(instr.querySelector(".editbox"), "deck_instruction");
        Object.keys(nodes).forEach(function (sid) {
          setBox(nodes[sid].article.querySelector(".editbox"), "page_instruction");
        });
      };

      // 断られたとき（ほかの場所で構成が変わっていた）の読み直し。**画面は作り直さない**
      // （スクロール位置・開いた記録・書きかけの入力を残す）
      var refresh = function () {
        return SS.api("/api/decks/" + deckId).then(function (fresh) {
          if (!guard()) { return; }
          lockVersion = fresh.deck.lock_version;
          seenRevision(fresh.revision ? fresh.revision.id : null);
          var known = fresh.pages.length === order.length && fresh.pages.every(function (p) { return nodes[p.slide_id]; });
          if (!known) { notify("revision", REVISION_CHANGED); return; }   // ページ集合が変わった: 勝手に直さない
          // **可否を先に当ててから絵を当てる。** 空枠の中の「作り直す」は `paintPage` が作るので、
          // 順番が逆だと 1 回ぶん古い答えのボタンが残る
          paintCaps(fresh.capabilities || {}, fresh.pages);
          fresh.pages.forEach(function (p) { paintPage(pctx, p); paintVersions(p); });
          paintBand(pband, fresh.counts);
          paintPageCount(fresh.revision && fresh.revision.revision, fresh.pages);
        });
      };
      ctx.adopt = function (slideId, versionId, button, said) {
        if (adopting[slideId]) { return; }
        adopting[slideId] = true;
        stackBusy(slideId, true, button);
        said.textContent = "";
        var choices = {};
        choices[slideId] = versionId;
        SS.api("/api/decks/" + deckId + "/adopt",
          { method: "POST", body: { choices: choices, lock_version: lockVersion } }).then(function (r) {
          adopting[slideId] = false;
          // 答えで握り直す。**lock_version は毎回上がる**ので、握り直さないと 2 回目が必ず断られる
          lockVersion = r.deck.lock_version;
          seenRevision(r.revision_id);
          // 絵・左のサムネ・使用中の印・ヘッダの版番号を**揃えて**替える
          r.pages.forEach(function (p) { paintPage(pctx, p); paintVersions(p); });
          // 抑えるのは**この後に届く実況**だけ。先に立てると自分の当て込みが飛ぶ（実ブラウザで踏んだ）
          pctx.justAdopted[slideId] = true;
          paintBand(pband, r.counts);
          paintPageCount(r.revision, r.pages);
          // 何が起きたかはサーバの文をそのまま出す（同じ版をもう一度押したときも本文が違う）
          var back = nodes[slideId] && nodes[slideId].article.querySelector(".versions__said");
          if (back) { back.textContent = r.message || ""; }
        }, function (e) {
          adopting[slideId] = false;
          stackBusy(slideId, false, button);
          // **絵は動かしていない**（切り替えていないので戻す必要が無い）。理由だけをその場に出す
          said.textContent = e && e.message ? e.message : "";
          if (e && e.status === 409) { refresh().catch(function () {}); }
        });
      };

      // ---- 並べ替え（掴む / Alt + 矢印 / ⋮メニューの「前へ・後へ」の 3 経路が同じ 1 本の道を通る）----
      // 送るのは必ず **slide_ids（全順序）**。相対の 1 段（direction）は、送っている間に別の並べ替えが
      // 保存され終わっていると違う結果になる。全順序はべき等で、サーバが集合の同一性を検証してくれる。
      var thumbList = aside.querySelector(".thumbs__list");
      var statusEl = aside.querySelector(".thumbs__status");
      var whyEl = aside.querySelector(".thumbs__why");
      var saving = false, queued = null;
      var say = function (text, why) {
        statusEl.textContent = text || "";
        whyEl.textContent = why || "";
      };

      var renumber = function () {
        order.forEach(function (sid, i) {
          var pos = String(i + 1);
          var n = nodes[sid];
          var thumb = aside.querySelector('.thumb[data-slide="' + sid + '"]');
          if (thumb) {
            var tn = thumb.querySelector(".thumb__num");
            if (tn && tn.textContent !== pos) { tn.textContent = pos; }
            if (n) { thumb.setAttribute("aria-label", thumbLabel(pos, n.title, n.stateLabel)); }
          }
          if (!n) { return; }
          if (n.article.dataset.position !== pos) { n.article.dataset.position = pos; }
          var pn = n.article.querySelector(".page__num");
          if (pn && pn.textContent !== pos) { pn.textContent = pos; }
          var target = n.article.querySelector(".editbox .target");
          if (target) { target.textContent = "ページ " + pos; }
          n.frame.setAttribute("aria-label", "ページ " + pos + "（" + n.stateLabel + "）");
        });
      };

      var orderInList = function () {
        return Array.prototype.map.call(thumbList.querySelectorAll(".thumb"), function (b) { return b.dataset.slide; });
      };
      var orderInPages = function () {
        return Array.prototype.map.call(pagesEl.querySelectorAll(".page"), function (a) { return a.dataset.slide; });
      };

      // 節点を動かすだけ（作り直さない）。書きかけの指示・開いた詳細・IntersectionObserver が生き残る。
      // (1) もう並んでいるなら動かさない（節点を動かすと、その中にある焦点が外れる）
      // (2) いま見ているページが画面の中で動かないように、測ってから動かし差分だけ戻す
      // (3) 焦点が外れたら戻す（Alt + 矢印を続けて押せるようにするため）
      var applyOrder = function (next) {
        var key = next.join("|");
        if (orderInList().join("|") === key && orderInPages().join("|") === key) { renumber(); return; }
        var active = document.activeElement;
        var anchor = current ? document.getElementById("page-" + current) : null;
        var before = anchor ? anchor.getBoundingClientRect().top : 0;
        next.forEach(function (sid) {
          var thumb = aside.querySelector('.thumb[data-slide="' + sid + '"]');
          if (thumb && thumb.parentNode) { thumbList.appendChild(thumb.parentNode); }
          if (nodes[sid]) { pagesEl.appendChild(nodes[sid].article); }
        });
        renumber();
        if (active && active.focus && document.activeElement !== active && document.contains(active)) {
          active.focus({ preventScroll: true });
        }
        if (anchor) { window.scrollBy(0, anchor.getBoundingClientRect().top - before); }
      };

      var send = function () {
        saving = true;
        SS.api("/api/decks/" + deckId + "/pages/reorder",
          { method: "POST", body: { slide_ids: order.slice(), lock_version: lockVersion } }).then(function (r) {
          saving = false;
          lockVersion = r.deck.lock_version;
          // 自分の並べ替えは別の構成版を作る。握り直さないと、次の実況で
          // 「ほかの場所で構成が変わりました」を**自分に対して**誤報する
          seenRevision(r.revision_id);
          confirmed = r.pages.map(function (p) { return p.slide_id; });
          paintBand(pband, r.counts);
          if (queued) { order = queued; queued = null; applyOrder(order); send(); return; }
          order = confirmed.slice();
          applyOrder(order);
          say(reorderWords().saved);
        }, function (e) {
          saving = false;
          queued = null;
          order = confirmed.slice();   // 保存できている並びへ戻す（押した直前の並びではない）
          applyOrder(order);
          // 何をしたか（元へ戻した）は画面が言い、なぜ断られたかはサーバの文をそのまま出す
          say(reorderWords().reverted, e && e.message ? e.message : "");
          // 断られたのは、ほかの場所で構成が変わっていたときだけ（画面は必ず全順序を送るので形は正しい）
          if (e && (e.status === 409 || e.status === 400)) { notify("revision", REVISION_CHANGED); }
        });
      };

      // 動かした 1 枚を短く光らせる（どれを動かしたかを見せる。位置は勝手に追わない）
      var flash = function (slideId) {
        var thumb = aside.querySelector('.thumb[data-slide="' + slideId + '"]');
        if (!thumb) { return; }
        thumb.classList.remove("thumb--moved");
        void thumb.offsetWidth;   // 続けて動かしても毎回光らせる
        thumb.classList.add("thumb--moved");
      };

      ctx.reorderTo = function (next, movedId) {
        if (next.length !== order.length || next.join("|") === order.join("|")) { return; }
        order = next.slice();
        applyOrder(order);
        var at = movedId ? order.indexOf(movedId) + 1 : 0;
        say(at ? movedText(at) : reorderWords().saving);
        if (movedId) { flash(movedId); }
        if (saving) { queued = order.slice(); return; }
        send();
      };

      ctx.moveBy = function (slideId, step) {
        var i = order.indexOf(slideId);
        var j = i + step;
        if (i < 0 || j < 0 || j >= order.length) { return; }
        var next = order.slice();
        next.splice(j, 0, next.splice(i, 1)[0]);
        ctx.reorderTo(next, slideId);
      };

      if (thumbList) {
        enableDragReorder({
          list: thumbList, scroller: aside,
          canReorder: function () { return SS.cap(caps, "reorder_pages").available; },
          onGrab: function (id) { pctx.grabbed = id; },
          onRelease: function () { pctx.grabbed = null; },
          onSlot: function () {
            // 掴んでいる間も番号を振り直す（落とすまで結果が分からないのを避ける）。
            // 中央は動かさない: 落としたときに 1 度だけ動く
            orderInList().forEach(function (sid, i) {
              var thumb = aside.querySelector('.thumb[data-slide="' + sid + '"]');
              var tn = thumb && thumb.querySelector(".thumb__num");
              if (tn && tn.textContent !== String(i + 1)) { tn.textContent = String(i + 1); }
            });
          },
          commit: function (id) {
            var next = orderInList();
            if (next.join("|") === order.join("|")) { renumber(); say(""); return; }
            ctx.reorderTo(next, id);
          },
          cancel: function () {
            applyOrder(order);           // 掴む前の並びへ戻す（API は呼ばない）
            say(reorderWords().cancelled);
          },
          say: say
        });
      }

      // ---- 修正指示を送る（結線）----
      // **1 ページはそのまま生成に入る。スライド全体は枚数を見せて確認を 1 つ挟む**（利用者の決定）。
      // 確認に見せた枚数をそのまま `confirm_pages` に echo するので、見せた数と実際の対象が
      // 食い違ったらサーバが 1 枚も投げない。1 ページのときは echo しない。
      //
      // 断られたら**入力を消さない**（書いた文と添付を失わせない）。消すのは受け付けられたときだけ。
      // 送っている間はその器を押せなくする（連打で画像づくりが 2 本走らない）。
      var readBox = function (box) {
        var ta = box.querySelector("textarea");
        var mode = box.querySelector(".editbox__remake-box");
        // 直し方の値は語彙の key（サーバの enums と同じ値）。**表示名は画面に持たない**
        return { text: ta ? ta.value : "", edit_mode: mode && mode.checked ? "remake" : "minor",
                 attachments: readAttachments(box) };
      };
      var clearBox = function (box) {
        var ta = box.querySelector("textarea");
        if (ta) { ta.value = ""; }
        box.querySelectorAll(".attach").forEach(function (chip) { chip.remove(); });
        if (box.__sync) { box.__sync(); }
      };
      // 「送信は通ったが画像づくりが始まらなかった」の**唯一の回収口**。スライドに 1 本の帯では拾えない
      // ので、そのカードの中に置く（帯には足さない。帯はスライド全体の話で、これはこの指示の話）
      var recoverButton = function (instructionId, said) {
        var b = h("button", { class: "btn btn--small btn--primary", type: "button",
          text: instructionWords().generate_label });
        b.addEventListener("click", function () {
          b.disabled = true;
          SS.api("/api/decks/" + deckId + "/instructions/" + instructionId + "/generate", { method: "POST" })
            .then(function (r) {
              said.appendChild(h("p", { text: r.message || "" }));
              if (r.started) {
                b.remove();
                refresh().catch(function () {});
                watchCfg.active = true;
                startWatch(watchCfg);
              } else {
                b.disabled = false;   // 始めるものが無かった（何も変わっていない）。押し直せるままにする
              }
            }, function (e) {
              b.disabled = false;
              said.appendChild(h("p", { class: "editbox__refused", text: e && e.message ? e.message : "" }));
            });
        });
        return b;
      };
      var postInstruction = function (box, btn, body) {
        if (box.dataset.sending === "1") { return; }
        box.dataset.sending = "1";
        btn.disabled = true;
        btn.setAttribute("aria-busy", "true");
        var said = box.querySelector(".editbox__said");
        SS.clear(said);
        SS.api("/api/decks/" + deckId + "/instructions", { method: "POST", body: body }).then(function (r) {
          box.dataset.sending = "";
          btn.removeAttribute("aria-busy");
          clearBox(box);   // 受け付けられた。**ここで初めて**書いた文と添付を消す
          said.appendChild(h("p", { text: r.message || "" }));
          if (r.started) {
            // 候補版の進みは実況が持ってくる。読み直しは 1 回だけ（以降は 2 秒ごとの取り直しに任せる）
            refresh().catch(function () {});
            watchCfg.active = true;
            startWatch(watchCfg);
          } else {
            said.appendChild(h("p", { text: r.not_started_reason || "" }));
            said.appendChild(recoverButton(r.instruction_id, said));
          }
        }, function (e) {
          box.dataset.sending = "";
          btn.disabled = false;
          btn.removeAttribute("aria-busy");
          // **入力はそのまま**。理由はサーバの文をそのまま出す（画面で文を作らない）
          said.appendChild(h("p", { class: "editbox__refused", text: e && e.message ? e.message : "" }));
        });
      };
      ctx.submit = function (slideId, box, btn) {
        var read = readBox(box);
        var n = nodes[slideId];
        // 土台にする版は「いま映している版」。採用で動くので、送る瞬間の値を照合に渡す
        // （画面が映していた版とサーバが土台にする版が違っていたら、サーバが断る）
        postInstruction(box, btn, { scope: "page", slide_id: slideId, edit_mode: read.edit_mode,
          text: read.text, attachments: read.attachments, lock_version: lockVersion,
          expected_base_version_id: n ? n.article.dataset.version : null });
      };
      ctx.submitDeck = function (box, btn) {
        var read = readBox(box);
        var words = instructionWords();
        var shown = order.length;   // **画面が見せている枚数**。確認にもこの数を出す
        var body = { scope: "deck", edit_mode: read.edit_mode, text: read.text,
                     attachments: read.attachments, lock_version: lockVersion };
        if (shown >= 2) {
          var t = read.edit_mode === "remake" ? words.remake_confirm : words.minor_confirm;
          if (!SS.confirm(String(t || "").replace("{n}", String(shown)))) { return; }
          body.confirm_pages = shown;   // 見せた枚数をそのまま echo する
        }
        postInstruction(box, btn, body);
      };

      // 止まっている帯（サーバの limit が正本）は、判断が変わったときだけ作り直す
      var limitSig = function (l) {
        return !l ? "" : [l.stopped, l.reached, l.message, l.hint_at, l.resume && l.resume.available, l.resume && l.resume.reason].join("|");
      };
      var shownLimit = limitSig(data.limit);
      var paintLimit = function (l) {
        var sig = limitSig(l);
        if (sig === shownLimit) { return; }
        // 操作中の帯・ボタンは差し替えない（押した指の下から消さない）
        if (band && band.contains(document.activeElement)) { return; }
        if (rrow && rrow.contains(document.activeElement)) { return; }
        shownLimit = sig;
        var next = limitBanner(l, ctx);
        if (band) { band.remove(); }
        if (next) { screen.insertBefore(next, pband || body); }
        band = next;
        // 帯と脇のボタンは**同じ 1 つの値の反対側**なので、必ず一緒に作り直す（二重にも 0 にもしない）
        var nextRow = resumeRow(l, ctx);
        if (rrow) { rrow.remove(); }
        if (nextRow) { screen.insertBefore(nextRow, reasons.node || body); }
        rrow = nextRow;
      };
      // 送った指示から出た候補版の進み。**/progress の `candidates` はまだ出来ていない版だけ**を返すので、
      // 出来たことは「一覧から消えたこと」で分かる。消えたページだけ読み直してスタックに絵を入れる
      // （全体を作り直さない。開いた記録・書きかけの入力・スクロール位置を動かさないため）
      var paintCandidates = function (list) {
        var now = {};
        (list || []).forEach(function (c) {
          now[c.slide_id] = true;
          var n = nodes[c.slide_id];
          var row = n && n.article.querySelector('.version[data-version="' + c.version_id + '"]');
          var state = row && row.querySelector(".version__state");
          // 状態の語はサーバの display をそのまま出す（画面では作らない）
          if (state && state.textContent !== c.display.label) { state.textContent = c.display.label; }
        });
        var done = Object.keys(candidates).filter(function (sid) { return !now[sid]; });
        candidates = now;
        return done.length > 0;   // 読み直すかどうかは `paint` が 1 か所で決める（二重に読まない）
      };
      // ---- 「今なにができるか」を当て直す唯一の判断 ----
      // **実況（/progress）が `capabilities` を運ぶ**（2026-09-18）。以前は運んでおらず、画面は
      // 「状況の値（構成版・`live.reason`・`limit`・件数）が動いたら `GET /api/decks/<id>` を
      // 聞き直す」という**当て推量の合図**を持っていた。合図が 1 つでも足りないと、画面は最初に
      // 開いた瞬間の答えを持ち続けて凍る（2026-09-14 の実害の 3 つ目。利用者「すべて完了した
      // あとにページの修正などができなかった。リロードするとできそうでした」）。合図が正しいことは
      // 試験で押さえていたが、**押さえなければ正しくない仕組み**だった。
      //
      // いまは**答えそのものを比べる**。変わったら当てる、変わらなければ何もしない。合図が要らない
      // ので、抜けようがない。**画面では可否を組み立てない**（U7）のは変わらない: 比べるだけで、
      // 作らない。
      //
      // `can_minor`（今の絵を土台に直せるか）はページごとに付いてくる値で、`capabilities` とは
      // 別に動く（絵ができた瞬間に変わる）。`paintCaps` が両方を当てるので、**両方を合わせて**
      // 1 本にする。
      //
      // **毎回当てない**のは、`paintCaps` が空枠を組み直させる（`capsGen`）からである。2 秒ごとに
      // 組み直すと、いちばん触れたくないもの — 画像を作っている最中の空枠 — がちらつく。
      var capsSig = function (caps, pages) {
        return JSON.stringify([caps || {}, (pages || []).map(function (p) { return [p.slide_id, !!p.can_minor]; })]);
      };
      var shownCaps = capsSig(data.capabilities, data.pages);
      var capsMoved = function (prog) {
        var sig = capsSig(prog.capabilities, prog.pages);
        if (sig === shownCaps) { return false; }
        shownCaps = sig;
        return true;
      };
      // **版スタックだけは実況に載っていない。** 載っているのは「いま使っている版の絵」だけで、
      // その下に積む版の一覧（`versions`）は入っていない。絵が届いた瞬間にスタックの中の絵も
      // 変わるので、**絵が増えた / 差し替わったときは読み直す**（実ブラウザで、これが無いと
      // 版スタックが空のままになるのを見た。2026-09-18）。
      var picturesSig = function (pages) {
        return (pages || []).map(function (p) { return p.slide_id + ":" + (p.image_url || ""); }).join("|");
      };
      var shownPictures = picturesSig(data.pages);
      var picturesMoved = function (prog) {
        var sig = picturesSig(prog.pages);
        if (sig === shownPictures) { return false; }
        shownPictures = sig;
        return true;
      };
      // **自分が作っていない候補が居たら、版スタックを取り直す。**（2026-09-18）
      // 画面から送ったときは、送った本人が版の行を先に置くので取り直しは要らない。会話から
      // 修正指示を送れるようになった（ROADMAP P10 の `revise`）ので、**画面が知らない候補**が
      // 現れるようになった。置き場が無いと `paintCandidates` は黙って読み飛ばし、出来上がるまで
      // 画面には何も出ない（作っている最中がいちばん見たい時間なのに）。
      var unknownCandidate = function (prog) {
        return (prog.candidates || []).some(function (c) {
          return c.version_id && !screen.querySelector('.version[data-version="' + c.version_id + '"]');
        });
      };
      var paint = function (prog) {
        paintPlanning(planBand, prog.planning);
        paintBand(pband, prog.counts);
        // **企画の結果できたページが、この画面にはまだ 1 枚も無い。**
        // 企画を待つ人はページが 0 枚の画面を開いたまま待つ。そのとき `nodes` は空で、進みの帯も
        // サムネの列も器ごと存在しない（0 枚のときは作られない）。`paintPage` は知らないページを
        // 黙って読み飛ばすので、実況が生きていても**3 枚は永遠に現れなかった**（2026-09-14 の実害の
        // 後半。利用者「全部が随時進んでいく感じを見せないといけないです」）。差分では当てられない
        // ので、**ここだけは画面を作り直す**。0 枚の画面には守るもの（書きかけの入力・開いた記録・
        // スクロール位置）が無いので、描き直して失うものが無い。描き直したあとは `nodes` が埋まり、
        // 1 枚ずつ絵が入るのは今までどおり差分で当たる（この枝は二度と通らない）。
        if (prog.pages.length && !order.length) {
          if (!grew) { grew = true; reload(); }
          return;
        }
        // **可否を先に当ててから絵を当てる。** 空枠の中の「作り直す」は `paintPage` が作るので、
        // 順番が逆だと 1 回ぶん古い答えのボタンが残る（`refresh()` と同じ順序）
        if (capsMoved(prog)) { paintCaps(prog.capabilities, prog.pages); }
        prog.pages.forEach(function (p) { paintPage(pctx, p, true); });
        var finished = paintCandidates(prog.candidates);
        paintLimit(prog.limit);
        // 読み直す理由は**版スタックだけ**である（絵が届いた / 候補が出来上がった / 画面が知らない
        // 候補が現れた）。可否はもう実況が運んでいるので、そのために聞き直すことはしない。
        // `refresh()` は**画面を作り直さない**（書きかけの指示文・スクロール位置・開いた畳みが残る）
        if (picturesMoved(prog) || finished || unknownCandidate(prog)) { refresh().catch(function () {}); }
        // **自分が頼んだページが出来たのなら、自分の操作である**（2026-09-24）。
        // 差し込みの構成版はワーカーが作るので `ownRevisions` に入りようがなく、放っておくと
        // 自分で押した結果を「ほかの場所で変わりました」と誤報する。頼んだ控え（`ownInserts`）が
        // 実況の `pending_inserts` から消えたことを、出来上がりの合図に使う（候補版と同じ読み方）。
        if (ownInsertLanded(prog)) {
          // **押させない。** ページが増える変化なので描き直しは要るが、書きかけを捨てるのは
          // 頼んだ本人の操作としては乱暴なので、何も打っていないときだけ黙って作り直す
          if (nothingIsBeingTyped()) { reload(); return; }
          notify("insert", insertWords().landed || REVISION_CHANGED);
          return;
        }
        // 送る前に飛んでいた答えが後から届くと、自分が作った 1 つ前の構成版が載っている。
        // 自分の操作を「ほかの場所で変わりました」と誤報しない
        if (revisionId && prog.revision_id && prog.revision_id !== revisionId && !ownRevisions[prog.revision_id]) {
          notify("revision", REVISION_CHANGED);
        }
      };
      // 実況の設定は名前を付けて持つ（送信で画像づくりが始まったら、同じ設定で乗せ直す）
      watchCfg = { deckId: deckId, guard: guard, screen: screen, seq: data.last_event_seq,
        active: busy(data), paint: paint, notify: notify };
      startWatch(watchCfg);
    }
  };
})();
