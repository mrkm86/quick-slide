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

  // 「設定を替えて組み直す」の器（P5）。ブランドと見せ方を選んで送る。選べるもの・いまの値・注意の 1 文は
  // サーバ（`GET /api/decks/<id>/look`）から読む。送ると直しと同じ仕事になり、画面は実況で替わる
  function openRebuild(deckId, onDone) {
    SS.api("/api/decks/" + deckId + "/look").then(function (look) {
      function select(id, items, current) {
        var sel = h("select", { id: id });
        items.forEach(function (it) {
          var o = h("option", { value: it.id, text: it.name });
          if (it.id === current.id) { o.selected = true; }
          sel.appendChild(o);
        });
        if (!items.some(function (it) { return it.id === current.id; })) {
          var gone = h("option", { value: "", text: current.name + (current.deleted ? "（削除済み・いまのまま）" : "（いまのまま）") });
          gone.selected = true;
          sel.insertBefore(gone, sel.firstChild);
        }
        return sel;
      }
      var brandSel = select("rebuild-brand", look.brands, look.brand);
      // 見せ方は付属の 2 つだけ（docs/adr/0009）。モーションは見せ方を持たないので出さない（サーバが空で返す）
      var styleSel = look.styles.length ? select("rebuild-style", look.styles, look.style) : null;
      var errors = h("p", { class: "reason", role: "alert" });
      var submit = h("button", { class: "btn btn--primary", type: "button", text: "組み直す" });
      var cancel = h("button", { class: "btn", type: "button", text: "やめる" });
      var box = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": "設定を替えて組み直す" },
        h("h3", { text: "設定を替えて組み直す" }),
        h("p", { class: "muted small", text: look.notice }),
        h("label", { class: "setting-field", for: "rebuild-brand" }, h("span", { text: "ブランド" }), brandSel),
        styleSel ? h("label", { class: "setting-field", for: "rebuild-style" }, h("span", { text: "見せ方" }), styleSel) : null,
        errors,
        h("div", { class: "modal-actions" }, cancel, submit));
      var backdrop = h("div", { class: "modal-backdrop" }, box);
      function close() { document.removeEventListener("keydown", onEscape, true); if (backdrop.parentNode) { backdrop.parentNode.removeChild(backdrop); } }
      function onEscape(e) { if (e.key === "Escape") { e.preventDefault(); close(); } }
      submit.addEventListener("click", function () {
        var body = {};
        if (brandSel.value) { body.brand_id = brandSel.value; }
        if (styleSel && styleSel.value) { body.style_id = styleSel.value; }
        submit.disabled = true;
        SS.api("/api/decks/" + deckId + "/rebuild", { method: "POST", body: body }).then(function (r) {
          close();
          if (r && r.message) { SS.toast(r.message); }
          if (onDone) { onDone(); }
        }, function (e) {
          submit.disabled = false;
          errors.textContent = (e && e.message) ? e.message : "処理できませんでした。もう一度お試しください。";
        });
      });
      cancel.addEventListener("click", close);
      backdrop.addEventListener("click", function (e) { if (e.target === backdrop) { close(); } });
      document.addEventListener("keydown", onEscape, true);
      document.body.appendChild(backdrop);
      brandSel.focus();
    }).catch(SS.toastError);
  }
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
    var glyph = { ready: "■", failed: "✕", unknown: "?", cancelled: "—", generating: "◌", adjusting: "◌", editing: "◌" }[display.key] || "○";
    return h("span", { class: "mark mark--" + display.key, title: display.label, "aria-label": display.label, text: glyph });
  }

  // カードの中身はヘッダの下に生えるので、印は中身が動く向きを指す。
  // 閉じている＝これから下へ伸びる、開いている＝これから上へ縮む
  // 印はすべて Material Symbols（SS.icon。core.js）。名前は呼ぶ側の意味のまま、絵だけを DADS の組に揃える。
  // 鉛筆は**先が左下**（2026-09-18、利用者の指示）。`✎` のような文字はフォントによって向きも太さも変わるので SVG にする。
  // 参考画像を添える口に**絵文字を置かない**（2026-09-24、利用者「ここだけカラーアイコンって異色に見える」）。
  // どれも `currentColor` で、ほかの印と同じ色に揃う
  function pageIcon(name) {
    var names = { "eye-off": "visibility_off", "panel-top-close": "expand_less", "panel-bottom-close": "expand_more",
                  "pencil": "edit", "image": "image" };
    return h("span", { class: "page-icon page-icon--" + name, "aria-hidden": "true" }, SS.icon(names[name]));
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
  var REASON_KEYS = ["deck_instruction", "page_instruction", "pages_instruction", "regenerate", "adopt_version", "export"];

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
  var TEXT_ROWS_MIN = 1, TEXT_ROWS_MAX = 6;   // 欄の高さ（行）。1 行（「＋」・送信と同じ 48px）で始まり、書くと 6 行まで

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
    // URL を貼り付けて画像を取り込んだ・取り込めなかったの知らせ（小さく 1 行）
    var urlNote = h("p", { class: "editbox__url-note small", role: "status" });
    var box = null;
    var changed = opts.onChange || function () {};
    var locked = opts.locked || function () { return false; };
    var area = { chips: chips, file: file, pick: pick, note: note, urlNote: urlNote, over: false, role: "" };

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
        fill(chip, drop, r);
      }, function (e) {
        // 送れなかったのは**その 1 枚だけ**。ほかの添付と書いた文はそのまま生きる
        chip.removeAttribute("aria-busy");
        chip.dataset.failed = "1";
        SS.clear(chip).appendChild(h("span", { class: "attach__why small", text: e && e.message ? e.message : "" }));
        chip.appendChild(drop);
        changed();
      });
    }

    function fill(chip, drop, r) {
      chip.removeAttribute("aria-busy");
      chip.dataset.asset = r.asset_id;
      SS.clear(chip).appendChild(h("img", { class: "attach__pic", src: r.url, alt: "" }));
      // 渡した画像の役割（入れる・参考）。決めるのは欄の上で選んだ札（`area.paintRoles`）
      chip.appendChild(h("span", { class: "attach__role small", text: area.role }));
      chip.appendChild(drop);
      changed();
    }
    area.paintRoles = function (role) {
      area.role = role || "";
      chips.querySelectorAll(".attach__role").forEach(function (el) { el.textContent = area.role; el.hidden = !area.role; });
    };
    // 画像の URL を欄に貼り付けたら、画像として取り込んでみる（サーバが取りに行く。docs/adr/0008-edit-kinds.md）。
    // 取り込めたら URL の文字を欄から消して札にする。取り込めなければ、文字のまま残して理由を小さく出す
    function fetchUrl(url) {
      urlNote.textContent = "URL の画像を取り込んでいます…";
      SS.api("/api/images/fetch", { method: "POST", body: { url: url } }).then(function (r) {
        if (!area.addAsset(r, "url")) { urlNote.textContent = words.too_many_attachments_reason || ""; return; }
        var ta = box && box.querySelector("textarea");
        if (ta && ta.value.indexOf(url) >= 0) {
          ta.value = ta.value.replace(url, "").replace(/^\s+|\s+$/g, "");
          ta.dispatchEvent(new Event("input"));
        }
        urlNote.textContent = "";
      }, function (e) {
        urlNote.textContent = "URL を画像として取り込めませんでした（" + (e && e.message ? e.message : "") + "）。文字のまま送ります。";
      });
    }
    // 置き場に登録済みの画像（URL から取り込んだ画像。`POST /api/images/fetch` の答え）を札にする
    area.addAsset = function (r, method) {
      if (area.counted() >= limit) { area.over = true; changed(); return false; }
      area.over = false;
      var chip = h("span", { class: "attach", dataset: { method: method } });
      var drop = h("button", { class: "btn btn--small btn--ghost attach__remove", type: "button",
        text: words.attachment_remove, "aria-label": words.attachment_remove });
      drop.addEventListener("click", function () { chip.remove(); area.over = false; changed(); });
      chips.appendChild(chip);
      fill(chip, drop, r);
      return true;
    };

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
        if (!files.length) {
          var pasted = ((e.clipboardData && e.clipboardData.getData("text/plain")) || "").trim();
          if (/^https?:\/\/\S+$/.test(pasted) && pasted.length <= 2048) { setTimeout(function () { fetchUrl(pasted); }, 0); }
          return;   // 文字はそのまま欄へ入る
        }
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

  // ---- 「何を直しますか？」（docs/adr/0008-edit-kinds.md）----
  // 出す種類と選択肢は**サーバが決める**（デッキの答えの `edit_kinds`。モードと機械でできるものだけ）。
  // 画面は渡されたものだけを並べ、できない種類は出さない。選ばなければ、これまでどおり書いた指示だけで送る。
  // 文字以外は書かなくても送れる（選んだ方針がそのまま指示になる）
  function kindPicker(kinds, onChange) {
    var state = { kind: null, options: [] };
    var picker = { node: null, entry: function () { return null; }, read: function () { return { kind: null, options: [] }; },
                   reset: function () {} };
    if (!kinds || !kinds.length) { return picker; }
    var row = h("div", { class: "editkind__kinds" });
    var lead = h("p", { class: "editkind__lead small" });
    var options = h("div", { class: "editkind__options", role: "group" });
    var extra = h("div", { class: "editkind__extra" });
    // 「？」の説明は、このスライドで出す種類の説明（サーバの文）から組む。出さない種類の話はしない
    var help = kinds.map(function (k) { return k.label + ": " + k.lead; })
      .concat(["文字以外は、選ぶだけで送れます。下の欄に補足を書き足すこともできます。",
               "どれも選ばずに、下の欄に書いて送ることもできます。もう一度押すと選ぶのをやめます。"]);
    picker.node = h("div", { class: "editkind", role: "group", "aria-label": "何を直しますか？" },
      h("div", { class: "title-row editkind__head" }, h("p", { class: "editkind__q small", text: "何を直しますか？" }),
        SS.helpTip("何を直しますか", help)),
      row, lead, options, extra);
    var kindButtons = [];

    function entryOf(kind) { return kinds.filter(function (k) { return k.kind === kind; })[0] || null; }
    picker.entry = function () { return entryOf(state.kind); };
    function choose(kind) {
      var e = entryOf(kind);
      state.kind = kind;
      // 1 つだけ選ぶ選択肢は先頭（おまかせ・生成するなど）を既定にする。いくつでも選べるもの（文字）は空から
      state.options = e && !e.many && e.options.length ? [e.options[0].value] : [];
      paint();
      onChange();
    }
    function paint() {
      var e = entryOf(state.kind);
      kindButtons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.kind === state.kind)); });
      lead.textContent = e ? e.lead : "";
      lead.hidden = !e;
      SS.clear(options);
      SS.clear(extra);
      if (!e) { options.hidden = true; return; }
      options.hidden = !e.options.length;
      options.setAttribute("aria-label", e.label + "の選び方");
      e.options.forEach(function (o) {
        var b = h("button", { class: "chip editkind__option", type: "button", text: o.label,
          "aria-pressed": String(state.options.indexOf(o.value) >= 0) });
        b.addEventListener("click", function () {
          var i = state.options.indexOf(o.value);
          if (e.many) { if (i >= 0) { state.options.splice(i, 1); } else { state.options.push(o.value); } }
          else { state.options = [o.value]; }
          paint();
          onChange();
        });
        options.appendChild(b);
      });
      // 画像を渡す入口は「＋」の 1 つ（貼り付け・ドラッグ・URL の貼り付けも同じ）。ここでは案内を小さく出すだけ
      if (state.kind === "image" && state.options[0] === "attached") {
        extra.appendChild(h("p", { class: "editkind__hint small",
          text: "「＋」から画像を渡してください。貼り付け・ドラッグや、画像の URL の貼り付けでも渡せます。" }));
      }
    }
    kinds.forEach(function (k) {
      var b = h("button", { class: "chip editkind__kind", type: "button", "aria-pressed": "false", dataset: { kind: k.kind } },
        SS.icon(k.icon || "image", "editkind__icon"), h("span", { text: k.label }));
      // もう一度押すと選ぶのをやめる（書いた指示だけで送る、に戻る）
      b.addEventListener("click", function () { choose(state.kind === k.kind ? null : k.kind); });
      kindButtons.push(b);
      row.appendChild(b);
    });
    picker.read = function () { return { kind: state.kind, options: state.options.slice() }; };
    picker.reset = function () { state.kind = null; state.options = []; paint(); };
    paint();
    return picker;
  }

  // 修正指示の欄。チャットの入力欄と同じ並び: 上に札（作り直す・添えた画像）、下に「＋」・文の欄・送信。
  // 「画像を添える」と「今の絵を使わずに作り直す」は「＋」のメニューに畳む。画像は貼り付け・
  // ドラッグ&ドロップでも添えられる（`attachArea`。受け皿は欄そのもの）
  function editbox(caps, target, opts) {
    opts = opts || {};
    var words = instructionWords();
    var limits = instructionLimits();
    var textLimit = limits.text || 0;
    // どの口の可否を読むか（1 ページ・スライド全体・選んだ数ページ。P3 で選んだ数ページを足した）
    var capKey = opts.capKey || (opts.deck ? "deck_instruction" : "page_instruction");
    var c = SS.cap(caps, capKey);
    // 「今の絵を土台にして直せるか」はサーバが決める（`pages[].can_minor`）。**画面は状態から導かない。**
    // 絵の無いページでは土台が無いので、**作り直す側に固定して理由を添える**
    // （既定のまま送らせて、送ってから断るのは遅い）
    var minorState = opts.canMinor || { available: false, reason: words.remake_forced_reason };
    // 画像の概念が無いモード（モーション）では、「作り直す」と画像の添付を出さない（サーバが `hidden` を付ける）
    var noImages = !!minorState.hidden;
    var fieldId = "instruct-" + (++nextFieldId);

    var ta = h("textarea", { id: fieldId, rows: String(TEXT_ROWS_MIN), "aria-label": target + " " + words.section_label,
      placeholder: opts.placeholder || (opts.deck ? "デッキ全体に効かせたいことを書く" : "直したいことを書く") });
    var att = attachArea({ locked: function () { return noImages || locked(); }, onChange: function () { sync(); } });
    var chips = att.chips, file = att.file, note = att.note;
    var basePlaceholder = ta.placeholder;
    var kind = kindPicker(opts.kinds, function () { sync(); });
    // このページの事情（空・長すぎ・枚数の超過）を出す 1 行。スライドの事情は上に 1 か所だけ出る
    var why = h("p", { class: "editbox__why small", role: "status" });
    // 送った答え（サーバの文）と、断られた理由を出す場所。**上の `why` とは別の器**にする:
    // 送信の答えを、画面が数えた理由で上書きしてしまわないため
    var said = h("div", { class: "editbox__said small", role: "status" });
    // 見える字は「送信」（入力の 1 行を狭めない）。口ごとの名前（サーバの文）は読み上げと title に残す
    var send = h("button", { class: "btn editbox__send" + (c.available ? " btn--primary" : ""), type: "button", text: "送信",
      "aria-label": c.label });
    // 「作り直す」の値の持ち主は見えない箱 1 つ（読む側・当て直す側・失敗した空枠の入口が同じ箱を見る）。
    // 見せるのは「今の絵をもとに直す」の切り替え（オン＝今の絵から直す・オフ＝作り直す）だけ。
    // 土台にできる絵が無いページでは、切り替えも理由の文も出さない（作り直すしかないので、選ぶものが無い）
    var remake = h("input", { type: "checkbox", class: "editbox__remake-box", id: fieldId + "-remake", hidden: true });
    remake.checked = !minorState.available && !noImages;
    remake.disabled = !minorState.available;
    var keep = h("input", { type: "checkbox", role: "switch", class: "switch__input", id: fieldId + "-keep" });
    var keepRow = h("label", { class: "switch editbox__keep", for: keep.id }, keep,
      h("span", { class: "switch__track", "aria-hidden": "true" }), h("span", { class: "switch__label", text: "今の絵をもとに直す" }));
    // 「＋」は画像を添えるだけ（作り直すは上の切り替えに移した）
    var plus = h("button", { class: "btn btn--icon editbox__plus", type: "button",
      "aria-label": words.attach_label || "画像を添える", title: words.attach_hint || "" }, SS.icon("add"));
    // 貼り付け・ドロップの案内はパネルでは出さない。「＋」が読み上げでも案内を指す
    note.id = fieldId + "-hint";
    if (!noImages) { plus.setAttribute("aria-describedby", note.id); }
    var plusAnchor = h("span", { class: "editbox__plus-anchor" }, plus);
    var fs = h("fieldset", { disabled: !c.available, "aria-describedby": opts.reasonId || null },
      kind.node,
      remake,   // 見えない箱も欄の中に置く（送るとき・空枠の入口が `.editbox__remake-box` で読む）
      noImages ? null : keepRow,
      h("div", { class: "editbox__chiprow" }, noImages ? null : chips),
      why, said,
      h("div", { class: "editbox__input" }, noImages ? null : plusAnchor, ta, send),
      noImages ? null : att.urlNote,
      noImages ? null : file,
      noImages ? null : note);
    // 理由の文はここには出さない（スライドに 1 か所。上の `screenReasons`）。fieldset がその行を指す。
    // モードの助言も**カードごとには要らない**（どのページでも同じ 1 文）。デッキ全体への指示に 1 回だけ置く
    var box = h("div", { class: "editbox" }, fs);

    function locked() { return fs.disabled; }
    function counted() { return att.counted(); }
    function paintRemake() {
      keepRow.hidden = noImages || remake.disabled;
      keep.checked = !remake.checked;
      box.querySelector(".editbox__chiprow").hidden = !chips.children.length;
    }
    // 欄は 2 行で始め、書くと 6 行まで伸びる（`rows` で伸ばす。要素に見た目の値は書かない）
    function grow() {
      ta.rows = TEXT_ROWS_MIN;
      while (ta.rows < TEXT_ROWS_MAX && ta.scrollHeight > ta.clientHeight + 1) { ta.rows += 1; }
    }
    function sync() {
      var text = ta.value;
      // 送れない理由は 1 つだけ出す（上から順に、最初に当たったもの）。文はすべてサーバのもの
      var reason = "";
      var picked = kind.read(), entry = kind.entry();
      var source = entry && entry.kind === "image" ? entry.options.filter(function (o) { return o.value === picked.options[0]; })[0] : null;
      if (att.over) { reason = words.too_many_attachments_reason; }
      else if (text.length > textLimit) { reason = words.text_too_long_reason; }
      // 種類を選んだら、足りないものは種類ごとの文（サーバの文）。文字以外は選ぶだけで送れる
      else if (entry && entry.empty_reason && !text.trim() && !picked.options.length && !counted()) { reason = entry.empty_reason; }
      else if (source && source.attachment_reason && !counted()) { reason = source.attachment_reason; }
      else if (!entry && !text.trim() && !counted()) { reason = words.empty_reason; }
      ta.placeholder = entry ? "補足（任意）" : basePlaceholder;
      // 渡した画像の役割は、選んだ札で決まる（画像 → 渡した画像を入れる なら「入れる」、ほかは「参考」）
      att.paintRoles(source && source.attachment_reason ? "入れる" : "参考");
      if (why.textContent !== reason) { why.textContent = reason; }
      // **何も書いていないだけの行は、書き始めるまで静かにしておく。**押せない理由は
      // 押せないボタン自身（title）といつでも読める場所に残すが、毎回同じ 1 文を出さない
      // （間違えているとき＝長すぎ・枚数超過の行は、焦点が無くても出す）
      why.classList.toggle("editbox__why--quiet", reason === words.empty_reason || (!!entry && reason === entry.empty_reason)
        || (!!source && reason === source.attachment_reason));   // 画像の案内は札の下に出している
      send.title = reason || (c.available ? null : c.reason) || "";
      send.disabled = !!reason || box.dataset.sending === "1";
      paintRemake();
    }

    plus.addEventListener("click", function () { if (!locked()) { file.click(); } });
    keep.addEventListener("change", function () {
      if (remake.disabled) { return; }
      remake.checked = !keep.checked;
      remake.dispatchEvent(new Event("change"));
    });
    ta.addEventListener("input", function () { grow(); sync(); });
    remake.addEventListener("change", sync);
    att.bind(box);   // 画像の無いモードでは `locked` が真なので、貼り付け・ドロップは何も受けない
    // Ctrl/⌘ + Enter で送る（隙間にページを足す欄と同じ約束）
    ta.addEventListener("keydown", function (ev) {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter" && !send.disabled) { ev.preventDefault(); send.click(); }
    });
    if (opts.onSubmit) {
      send.addEventListener("click", function () { opts.onSubmit(box, send); });
    }
    box.__sync = function () { grow(); sync(); };
    box.__kind = function () { return kind.read(); };
    box.__resetKind = function () { kind.reset(); };
    // 見出しの対象が変わったとき（並べ替えで番号が動いた・選び方が変わった）の読み上げの名前
    box.__target = function (text) { ta.setAttribute("aria-label", text + " " + words.section_label); };
    // ---- 「今なにができるか」が変わったときの当て直し（**器は作り直さない**）----
    // 作り直せば確実に揃うが、**書きかけの指示文と、送り終えた添付が消える**。修正指示を打っている
    // 最中に実況が 1 回当たっただけで打った文が消えるのは、直そうとしている不具合より悪い。
    // だから欄そのものは残し、**可否に関わる見た目だけ**をここで当てる（`ta.value` と `chips` は触らない）。
    box.__caps = function (nextCap, nextCanMinor, reasonId) {
      if (nextCap) { c = nextCap; }
      fs.disabled = !c.available;
      if (reasonId) { fs.setAttribute("aria-describedby", reasonId); }
      else { fs.removeAttribute("aria-describedby"); }
      send.setAttribute("aria-label", c.label);
      send.classList.toggle("btn--primary", !!c.available);
      if (nextCanMinor) {
        minorState = nextCanMinor;
        // **利用者が自分で入れたチェックは動かさない。** 固定されていた（押せなかった）ときだけ
        // 既定へ戻す: 絵ができて今の絵から直せるようになったのに、作り直す側で固まったままにしない
        if (remake.disabled) { remake.checked = !nextCanMinor.available && !noImages; }
        remake.disabled = !nextCanMinor.available;
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
    return { available: false, reason: blocked[0].can_minor.reason || words.remake_forced_reason,
             hidden: !!blocked[0].can_minor.hidden };
  }

  // デッキ全体への指示の欄。デッキ全体を直すときにパネルへ出す（バーも畳みも持たない）
  function deckInstruction(caps, reasonIds, opts) {
    opts = opts || {};
    return editbox(caps, "デッキ全体", { deck: true, reasonId: reasonIds && reasonIds.deck_instruction,
      canMinor: opts.canMinor, kinds: opts.kinds, onSubmit: opts.onSubmit });
  }

  // ---- 選んだ数ページへの指示（P3）----
  // 各ページの「選ぶ」で印を付け、帯の「直す」でパネルに出す欄。器は 1 つだけ作り、選び方が変わったら
  // 読み上げの名前だけを当て直す（書きかけの文を消さない）。対象の見出しはパネルの頭が出す
  function pagesInstruction(caps, reasonIds, opts) {
    var words = instructionWords();
    var box = editbox(caps, "", { capKey: "pages_instruction", reasonId: reasonIds && reasonIds.pages_instruction,
      placeholder: "選んだページ全部に効かせたいことを書く", canMinor: { available: true, reason: null, hidden: !!opts.noImages },
      kinds: opts.kinds, onSubmit: opts.onSubmit });
    box.__set = function (positions) {
      box.__target((words.pages_target || "{pages}").replace("{pages}", positions.join("・")));
    };
    box.__box = box;
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

  // ---- ページの記録（ページの ⋮ →「このページの記録…」のダイアログの中身。名前はサーバの語彙から）----
  // 中身は「起きたことの記録」（狙い・元テキスト・出所・生成指示・版の表・生成の試行）。
  // 要望の「情報が多い」に応えるためにメニューへ畳むが、**捨てない**。枠の中に残すのは直せることだけ
  function detailsBlock(page) {
    var words = versionWords();
    var box = h("div", { class: "record" });
    box.appendChild(h("dl", null,
      h("dt", { text: "狙い" }), h("dd", { text: page.intent || "（未記入）" }),
      h("dt", { text: "元テキスト" }), h("dd", null, h("pre", { text: page.source_text || "（無し）" })),
      h("dt", { text: "表示本文" }), h("dd", null, h("pre", { text: page.body || "（無し）" })),
      h("dt", { text: "出所" }), h("dd", { text: page.source_kind_label })));
    // 生成指示は「詳細」の中のさらに折りたたみ（初見の人にプロンプト操作を強制しない。UI.md §5）
    var promptDetails = h("details", null, h("summary", { text: "生成指示（この版に使った指示文）" }),
      h("pre", { class: "prompt", text: page.prompt_text || "（この版には生成指示が記録されていません）" }));
    box.appendChild(promptDetails);

    // 版一覧（絵で見分けるのは「版を選ぶ…」の役目。ここは日時・指示・モードを読むための表として残す）。
    // **版を選ぶボタンはここには置かない**: 選ぶ操作は「版を選ぶ…」に 1 か所だけ置く（同じ操作を 2 か所に
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

  // ---- 版を選ぶ（ページの ⋮ →「版を選ぶ…」のダイアログの中身。絵で見分ける）----
  // 利用者「修正したあとにバージョン管理というか新旧の画像ができると思いますが、右側にサムネイルを
  // 縦にスタックするようなUIにしたくて、そのサムネを選択すると左側のプレビューも同期する」。
  // 版の小さな絵を並べ、押した版を大きく見る。PC は使用中の版と並べて見比べる。
  // **見るだけでは何も採用しない**（画面が持つのは「いま見ている版」1 つ。構成版を増やさない）。
  // 採用するのは「この版にする」だけで、押せるかの判断は 2 つだけ:
  //   (1) サーバの `capabilities.adopt_version`（まだ使えない、の口。U7）
  //   (2) **その版に絵の URL があるか**という事実。「状態が draft だから」という解釈では決めない
  //       （解釈で決めると、状態が増えたときに画面の中に直す場所が増える）
  function picturedVersions(page) {
    return page.versions.filter(function (v) { return !!v.image_url; });
  }

  function compareSide(v, words, tag) {
    var flags = [];
    if (v.active) { flags.push(words.active || ""); }
    var meta = [v.source_kind_label, v.edit_mode_label].filter(Boolean).join(" · ");
    return h("figure", { class: "vdlg__side" },
      tag ? h("span", { class: "vdlg__tag", text: tag }) : null,
      h("img", { src: v.image_url, alt: "v" + v.version, loading: "lazy" }),
      h("figcaption", null,
        h("span", { class: "vdlg__no", text: "v" + v.version + (flags.length ? "（" + flags.join("・") + "）" : "") }),
        h("span", { class: "small muted", text: meta }),
        v.revision_note ? h("span", { class: "small vdlg__note", text: v.revision_note }) : null));
  }

  // `state.pick` は見ている版の ID（ダイアログを開いている間、実況で作り直されても持ち越す）。
  // 返す `update(page)` は、実況で版が増えた・絵が入ったときの当て直し（見ている版と焦点は保つ）
  function versionsView(ctx, page, state) {
    var words = versionWords();
    var el = h("div", { class: "vdlg" });
    var said = h("p", { class: "vdlg__said small", role: "status" });
    var view = { el: el, said: said, page: page };

    function chosen(p) {
      var pics = picturedVersions(p);
      var hit = pics.filter(function (v) { return v.id === state.pick; })[0];
      if (hit) { return hit; }
      // 既定はいちばん新しい「使用中でない」絵のある版（切り替えたい先は、たいていそこ）。無ければ使用中
      var other = pics.filter(function (v) { return !v.active; })[0];
      return other || pics.filter(function (v) { return v.active; })[0] || null;
    }

    function paint(p) {
      view.page = p;
      var focused = el.contains(document.activeElement) ? document.activeElement.dataset.version : null;
      var canAdopt = SS.cap(ctx.caps, "adopt_version");
      var pick = chosen(p);
      state.pick = pick ? pick.id : null;
      var active = p.versions.filter(function (v) { return v.active; })[0] || null;
      var list = h("ol", { class: "vdlg__list", "aria-label": words.stack_label || "ページの版" });
      p.versions.forEach(function (v) {
        var hasImage = !!v.image_url;
        // 絵の無い版の理由は版ごと・状態ごとにサーバが決める（正本は revision_routes.py の
        // version_not_adoptable_reason）。画面では組み立てない・状態から選び直さない
        var reason = hasImage ? "" : (v.not_adoptable_reason || "");
        var pic = h("span", { class: "version__pic", "aria-hidden": "true" });
        if (hasImage) { pic.appendChild(h("img", { src: v.image_url, alt: "", loading: "lazy" })); }
        else { pic.appendChild(mark({ key: versionStateKey(v), label: versionStateLabel(v) })); }
        var meta = h("span", { class: "version__meta" },
          h("span", { class: "version__no", text: "v" + v.version }),
          v.active ? h("span", { class: "version__flag version__flag--active", text: "● " + (words.active || "") }) : null,
          hasImage ? null : h("span", { class: "version__state", text: versionStateLabel(v) }),
          v.image_missing ? h("span", { class: "version__state", text: words.image_missing }) : null,
          h("span", { class: "version__date small muted", text: SS.fmtDate(v.created_at) }),
          v.revision_note ? h("span", { class: "version__note small", text: v.revision_note }) : null);
        var b = h("button", { class: "version" + (v.active ? " version--active" : ""), type: "button",
          dataset: { version: v.id }, disabled: !hasImage, "aria-disabled": hasImage ? null : "true",
          "aria-pressed": hasImage ? (pick && pick.id === v.id ? "true" : "false") : null,
          "aria-current": v.active ? "true" : null, title: reason }, pic, meta);
        if (hasImage) {
          b.addEventListener("click", function () { state.pick = v.id; said.textContent = ""; paint(view.page); });
        }
        list.appendChild(h("li", null, b));
      });
      var stage = h("div", { class: "vdlg__stage" });
      if (pick && active && pick.id !== active.id && active.image_url) {
        stage.appendChild(compareSide(active, words, "いまの版"));
        stage.appendChild(compareSide(pick, words, "選んだ版"));
        stage.classList.add("vdlg__stage--pair");
      } else if (pick) {
        stage.appendChild(compareSide(pick, words, pick.active ? "いまの版" : "選んだ版"));
      }
      var already = !!(pick && pick.active);
      var why = !pick ? "絵のある版がありません。" : already ? "この版はすでに使われています。" : (canAdopt.available ? "" : canAdopt.reason);
      var adopt = h("button", { class: "btn btn--primary vdlg__adopt", type: "button", text: "この版にする",
        disabled: !pick || already || !canAdopt.available, title: why || "" });
      if (pick && !already && canAdopt.available) {
        adopt.addEventListener("click", function () {
          ctx.adopt(p.slide_id, pick.id, adopt, said, function () { if (ctx.closeDialog) { ctx.closeDialog(); } });
        });
      }
      // 部品を残していない前の回の版に切り替えたページ（html-slide）。画面と発表はその版の絵のまま、
      // 書き出しだけ今の作りで描き直す。文はサーバのもの（`export_note`）
      if (p.export_note) { stage.appendChild(h("p", { class: "vdlg__export-note small", text: p.export_note })); }
      var foot = h("div", { class: "vdlg__foot" },
        h("p", { class: "vdlg__why small", text: already || !canAdopt.available || !pick ? why : "" }), said, adopt);
      SS.clear(el).appendChild(list);
      el.appendChild(stage);
      el.appendChild(foot);
      if (focused) {
        var back = el.querySelector('.version[data-version="' + focused + '"]');
        if (back && !back.disabled) { back.focus({ preventScroll: true }); }   // 続けて選べるようにする
      }
    }
    view.update = paint;
    paint(page);
    return view;
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
        if (article.__open) { article.__open(); }   // 直す欄はパネルにある。そのページの分を出してから触る
        var box = article.__tools && article.__tools.querySelector(".editbox");
        var mode = box && box.querySelector(".editbox__remake-box");
        if (mode && !mode.disabled && !mode.checked) { mode.checked = true; mode.dispatchEvent(new Event("change")); }
        var ta = box && box.querySelector("textarea");
        if (ta) { ta.focus(); }
      });
      empty.appendChild(h("div", { class: "editbox__row" }, again,
        h("button", { class: "btn btn--small", type: "button", text: "指示を変えて作り直す", onclick: function () { if (article.__open) { article.__open(); } var ta = article.__tools && article.__tools.querySelector("textarea"); if (ta) { ta.focus(); } } })));
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
  function insertGap(afterSlideId, ctx, data, reload, onlyWaiting) {
    var words = insertWords();
    // 末尾の口だけは「いまの最後のページ」を送る瞬間に読む（並べ替えで最後が替わるため）。ほかは固定の ID
    var afterOf = typeof afterSlideId === "function" ? afterSlideId : function () { return afterSlideId; };
    var waiting = (data.pending_inserts || []).filter(function (t) {
      return (t.after_slide_id || null) === afterOf();
    })[0];
    if (waiting) {
      // 押した隙間が、企画が返るまでの間も画面に残る（利用者の選択 A）。
      return h("div", { class: "gap gap--waiting", "data-after": afterOf() || "" },
        h("span", { class: "gap__spinner", "aria-hidden": "true" }),
        h("span", { class: "gap__waiting", role: "status", text: words.waiting || "考えています…" }));
    }
    if (onlyWaiting) { return null; }   // 待っていない隙間は、末尾の口と各ページのメニューからだけ開く
    var cap = (ctx.caps && ctx.caps.insert_page) || {};
    if (!cap.available) { return null; }
    var gap = h("div", { class: "gap", "data-after": afterOf() || "" });
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
    // 画像の概念が無いモード（モーション）は、画像の添付を出さず、貼り付け・ドロップも受けない
    var noImages = data.pages.length > 0 && data.pages.every(function (p) { return p.can_minor && p.can_minor.hidden; });
    var att = attachArea({ hint: words.attach_hint, locked: function () { return noImages; }, onChange: function () { sync(); } });
    var why = h("p", { class: "editbox__why small", role: "status" });
    var send = h("button", { class: "btn btn--primary", type: "button", text: words.submit || "送信" });
    var cancel = h("button", { class: "btn btn--ghost", type: "button", text: words.cancel || "キャンセル" });
    // 送信は右端。やめるはその左（同上）。`.grow` は畳まれる決まりなので、真ん中は素の span で空ける
    var form = h("div", { class: "gap__form editbox", hidden: true },
      h("div", { class: "editbox__prompt" }, ta,
        h("div", { class: "editbox__prompt-tools" }, noImages ? null : att.pick, h("span", {}),
          h("span", { class: "gap__actions" }, cancel, send))),
      noImages ? null : h("div", { class: "editbox__row editbox__attach" }, att.chips, att.file),
      noImages ? null : att.note, why);
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
        after_slide_id: afterOf(), text: ta.value.trim(), attachments: readAttachments(form),
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
  // 中央の列に出すのは**スライドと 1 行だけ**（選ぶ・番号・題・直す・⋮）。直す指示の欄は
  // `article.__tools` に作り、パネルが「いま直しているページ」の分だけを出す（節点をそのまま移すので、
  // 書きかけの文・添えた画像は、パネルを閉じても別のページに替わっても消えない）。版と記録は ⋮ のダイアログ。
  // ページを畳む（タイトル行だけにする。v1 にあった・2026-10-14）。確認し終えたページを畳んでスクロールを減らす使い方。
  // 畳んだかはこのブラウザに覚える（localStorage。値は畳んだときの版）。**直して版が替わったら、また開いて出す**
  // （確認し終えたのは前の姿なので）。読めなくても開いて出す
  var FOLD_PREFIX = "qs.page-folded:";
  function isFolded(page) {
    try { return localStorage.getItem(FOLD_PREFIX + page.slide_id) === String(page.version); } catch (e) { return false; }
  }
  function setFolded(slideId, versionId, folded) {
    try {
      if (folded) { localStorage.setItem(FOLD_PREFIX + slideId, String(versionId)); }
      else { localStorage.removeItem(FOLD_PREFIX + slideId); }
    } catch (e) { /* 覚えられなくてもその場では畳む */ }
  }

  function pageArticle(page, ctx) {
    var caps = ctx.caps;
    var d = page.display;
    var folded = isFolded(page);
    var article = h("article", { class: "page" + (page.hidden ? " page--hidden" : "") + (folded ? " page--folded" : ""), id: "page-" + page.slide_id,
      "aria-label": "ページ " + page.position + (page.title ? ": " + page.title : ""),
      dataset: { slide: page.slide_id, position: String(page.position), version: page.version_id } });

    var menuBtn = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "ページの操作", "aria-haspopup": "menu" }, SS.icon("more_vert"));
    var menuWrap = h("span", { class: "menu-anchor" }, menuBtn);
    menuBtn.addEventListener("click", function () {
      // 位置と件数は「いま画面に出ている並び」から取る（描いた時点の page.position は並べ替えで古くなる）
      var pos = ctx.positionOf(page.slide_id);
      var ins = SS.cap(ctx.caps, "insert_page");
      var presentHere = SS.cap(ctx.caps, "present_from_here");
      // 版の数は「いま握っているページの答え」から数える（描いた時点の `page` は実況・採用で古くなる）
      var hasVersions = (ctx.pageOf(page.slide_id) || page).versions.length > 1;
      SS.menu(menuBtn, [
        { label: presentHere.label, disabled: !presentHere.available || !ctx.presentNumber(page.slide_id),
          reason: presentHere.available ? "非表示のページは発表に出ません" : presentHere.reason,
          onSelect: function () { ctx.present(page.slide_id); } },
        { label: "版を選ぶ…", disabled: !hasVersions, reason: "このページの版は 1 つだけです",
          onSelect: function () { ctx.openVersions(page.slide_id, menuBtn); } },
        { label: "このページの記録…", onSelect: function () { ctx.openRecord(page.slide_id, menuBtn); } },
        "-",
        { label: "画像をコピー", disabled: !page.image_url, reason: "コピーできる画像がありません", onSelect: function () { copyImage(page, article, pos); } },
        { label: "画像を保存", disabled: !page.image_url, reason: "保存できる画像がありません", onSelect: function () {
          var a = h("a", { href: page.image_url, download: "slide-" + pos + ".png" }); document.body.appendChild(a); a.click(); a.remove(); recordCopy("save", true, null);
        } },
        "-",
        { label: "このページの後ろにページを追加", disabled: !ins.available, reason: ins.reason,
          onSelect: function () { ctx.addAfter(page.slide_id); } },
        { label: "このページを複製", disabled: !SS.cap(ctx.caps, "duplicate_page").available,
          reason: SS.cap(ctx.caps, "duplicate_page").reason, onSelect: function () { ctx.duplicatePage(page.slide_id); } },
        { label: page.hidden ? "このページを表示にする" : "このページを非表示にする", danger: !page.hidden,
          disabled: !SS.cap(ctx.caps, "page_visibility").available || (!page.hidden && ctx.visibleCount() <= 1),
          reason: SS.cap(ctx.caps, "page_visibility").available ? "最後の 1 ページは非表示にできません"
            : SS.cap(ctx.caps, "page_visibility").reason, onSelect: function () {
          ctx.setPageHidden(page.slide_id, !page.hidden);
        } }
      ]);
    });

    // 選ぶ（P3: 選んだ数ページに 1 つの指示）。押せるかは指示の口と同じ答え（`pages_instruction`）。
    // 目立たせない: 行の左に小さく置き、選んだら上にまとめての操作の帯が出る
    var pick = h("input", { type: "checkbox", class: "page__select-box", dataset: { slide: page.slide_id },
      "aria-label": "ページ " + page.position + " " + (instructionWords().select_label || ""),
      disabled: !SS.cap(caps, "pages_instruction").available });
    pick.checked = !!(ctx.isSelected && ctx.isSelected(page.slide_id));
    pick.addEventListener("change", function () { if (ctx.toggleSelect) { ctx.toggleSelect(page.slide_id, pick.checked); } });
    var pickLabel = h("label", { class: "page__select", title: instructionWords().select_label || "" }, pick);

    // 行の操作は鉛筆（直す）・▶（再生）・⋮ の 3 つ。どれも字を持たない薄いボタンで、名前は aria-label
    var fixBtn = h("button", { class: "btn btn--icon btn--ghost page__edit", type: "button", title: "直す",
      "aria-label": "ページ " + page.position + " を直す" }, SS.icon("edit"));
    fixBtn.addEventListener("click", function () { ctx.openTools(page.slide_id, fixBtn); });
    // そのページだけを動きつきで再生する（html-slide・モーション。非表示のページには出さない）。
    // 絵がまだ無いページは押せない（`paintPlay` が実況で当て直す）
    var playBtn = null;
    if (ctx.canPlay && !page.hidden) {
      playBtn = h("button", { class: "btn btn--icon btn--ghost page__play", type: "button",
        "aria-label": "ページ " + page.position + " を再生" }, SS.icon("play_arrow"));
      playBtn.addEventListener("click", function () { ctx.openPlay(page.slide_id, playBtn); });
    }

    var foldBtn = h("button", { class: "btn btn--icon btn--ghost page__fold", type: "button", "aria-controls": "stage-" + page.slide_id });
    var paintFold = function () {
      var label = "ページ " + page.position + (folded ? " を開く" : " を畳む");
      SS.clear(foldBtn).appendChild(pageIcon(folded ? "panel-bottom-close" : "panel-top-close"));
      foldBtn.setAttribute("aria-label", label);
      foldBtn.setAttribute("title", folded ? "開く" : "畳む（タイトル行だけにする）");
      foldBtn.setAttribute("aria-expanded", folded ? "false" : "true");
      article.classList.toggle("page--folded", folded);
    };
    foldBtn.addEventListener("click", function () {
      folded = !folded;
      setFolded(page.slide_id, page.version, folded);
      paintFold();
    });
    article.__fold = function (on) { folded = !!on; setFolded(page.slide_id, page.version, folded); paintFold(); };

    var frame = h("figure", { class: "frame frame--" + d.key, "aria-label": "ページ " + page.position + "（" + d.label + "）" });
    if (page.image_url) {
      frame.appendChild(h("img", { src: page.image_url, alt: "ページ " + page.position + ": " + (page.title || ""), loading: page.position <= 2 ? "eager" : "lazy" }));
    } else {
      frame.appendChild(frameEmpty(d, caps, article, ctx.reasonIds));
    }
    // スライドの上の 1 行（題 → スライドの順。下に置くと、次のページの題と前のスライドが組に見えた・2026-10-11）。
    // **状態の印と状態名は枠の中と実況が持つ**（絵が出ても追う）ので、ここでは隠してある（CSS）
    article.appendChild(h("header", { class: "page__head" },
      pickLabel,
      h("span", { class: "page__num", text: String(page.position) }),
      h("span", { class: "page__title", text: page.title || "" }),
      // 非表示の印はアイコンだけ（題と番号が灰色なので語は要らない・2026-10-14）。語は読み上げと指を置いたときの名前に残す
      page.hidden ? h("span", { class: "page__hidden-mark", title: "非表示" }, pageIcon("eye-off"),
        h("span", { class: "visually-hidden", text: "非表示" })) : null,
      page.source_kind !== "generated" ? h("span", { class: "chip page__origin", text: page.source_kind_label }) : null,
      h("span", { class: "page__state" }, mark(d), h("span", { class: "page__state-label", text: d.label })),
      h("span", { class: "page__ver muted small", text: "v" + page.version }),
      h("span", { class: "page__head-spacer" }),
      playBtn,
      fixBtn,
      menuWrap,
      foldBtn));
    article.appendChild(h("div", { class: "page__stage", id: "stage-" + page.slide_id }, frame));
    paintFold();

    // ---- パネルに出す分（直す欄だけ。版と記録はこのページの ⋮ のダイアログ）----
    var tools = h("div", { class: "page-tools" + (page.hidden ? " page-tools--hidden" : ""), dataset: { slide: page.slide_id } });
    tools.appendChild(editbox(caps, "ページ " + page.position,
      { reasonId: ctx.reasonIds && ctx.reasonIds.page_instruction,
        canMinor: page.can_minor, currentImage: page.image_url, kinds: ctx.editKinds,
        onSubmit: function (box, btn) { ctx.submit(page.slide_id, box, btn); } }));
    article.__tools = tools;
    article.__open = function () { ctx.openTools(page.slide_id, fixBtn); };
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

  // ---- ページを再生するダイアログの中身 ----
  // 発表の画面をそのまま埋め込む（`/slides/<deck>/present?s=<slide_id>&embed=1`。そのページ 1 枚・段ごとの再生・操作は
  // クリック・→・Space）。再生の仕組みを作り直さない。同じオリジンの発表の画面とだけ postMessage する:
  // 閉じて（Esc を中で押したとき）を受け、最初からを送る。差出人の window とオリジンを確かめる
  function playView(deckId, slideId) {
    var frame = h("iframe", { class: "play__frame", title: "ページの再生", allow: "autoplay",
      src: "/slides/" + deckId + "/present?s=" + encodeURIComponent(slideId) + "&embed=1" });
    var replay = h("button", { class: "btn", type: "button" }, SS.icon("replay"), h("span", { text: "最初から" }));
    var el = h("div", { class: "play" },
      h("div", { class: "play__stage" }, frame),
      h("div", { class: "play__bar" },
        h("p", { class: "play__hint small", text: "クリック・→・Space で進みます" }), replay));
    var focusFrame = function () { try { frame.contentWindow.focus(); } catch (e) { /* 中に入れない */ } };
    replay.addEventListener("click", function () {
      if (frame.contentWindow) { frame.contentWindow.postMessage({ qs: "present-embed", v: 1, type: "replay" }, location.origin); }
      focusFrame();
    });
    frame.addEventListener("load", focusFrame);
    var onMessage = function (e) {
      var d = e.data;
      if (e.source !== frame.contentWindow || e.origin !== location.origin) { return; }
      if (d && typeof d === "object" && d.qs === "present-embed" && d.v === 1 && d.type === "close" && el.__close) { el.__close(); }
    };
    window.addEventListener("message", onMessage);
    return { el: el, focus: focusFrame, dispose: function () { window.removeEventListener("message", onMessage); },
             setClose: function (fn) { el.__close = fn; } };
  }

  // ---- ダイアログの殻（題・閉じる・中身の置き場）。出し入れと焦点は `makeSheet` が持つ ----
  // PC は中央の枠、スマホは下からの板（形は CSS の `.dlg`）。`role="dialog"`・`aria-modal`・題を読み上げの名前にする
  function dialogShell(title, cls) {
    var titleEl = h("h2", { class: "dlg__title", id: "dlg-title-" + (++nextFieldId), text: title });
    var closeBtn = h("button", { class: "btn btn--icon btn--ghost dlg__close", type: "button", "aria-label": "閉じる" }, SS.icon("close"));
    var body = h("div", { class: "dlg__body" });
    var el = h("div", { class: "dlg " + cls, role: "dialog", "aria-modal": "true", "aria-labelledby": titleEl.id },
      h("div", { class: "dlg__head" }, titleEl, closeBtn), body);
    return { el: el, body: body, closeBtn: closeBtn, backdrop: h("div", { class: "sheet-backdrop", hidden: true }) };
  }

  // ---- 引き出す板（タブレットの引き出し・スマホの下からの板。DADS のドロワー／モーダルダイアログの作法）----
  // 開いている間: フォーカスを板の中に閉じ込める（Tab は端で折り返す）・Esc と背景の押下で閉じる・背景はスクロールさせない・
  // 閉じたら開いたボタンへフォーカスを戻す（そのボタンが消えていれば `fallback()` が返す先へ）。
  // 板そのものの出し入れ（位置・動き）は CSS の `.is-open` が決める。ここは振る舞いだけ。
  function makeSheet(el, backdrop, cfg) {
    cfg = cfg || {};
    var trigger = null, opened = false;
    function focusables() {
      var sel = 'a[href],button:not(:disabled),input:not(:disabled):not([type="hidden"]):not([type="file"]),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
      return Array.prototype.filter.call(el.querySelectorAll(sel), function (n) { return !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length); });
    }
    el.addEventListener("keydown", function (e) {
      if (!opened) { return; }
      if (e.key === "Escape") {
        if (document.querySelector(".menu")) { return; }   // 開いているメニューを閉じるのが先
        e.preventDefault(); e.stopPropagation(); close(true); return;
      }
      if (e.key !== "Tab") { return; }
      var f = focusables();
      if (!f.length) { e.preventDefault(); return; }
      var first = f[0], last = f[f.length - 1], at = document.activeElement;
      if (e.shiftKey && (at === first || !el.contains(at))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (at === last || !el.contains(at))) { e.preventDefault(); first.focus(); }
    });
    backdrop.addEventListener("click", function () { close(true); });
    function open(from) {
      if (opened) { return; }
      opened = true;
      trigger = from || document.activeElement;
      el.classList.add("is-open");
      backdrop.hidden = false;
      document.documentElement.classList.add("is-locked");
      if (cfg.onOpen) { cfg.onOpen(); }
      var f = focusables();
      if (f.length) { f[0].focus({ preventScroll: true }); }
    }
    function close(restore) {
      if (!opened) { return; }
      opened = false;
      el.classList.remove("is-open");
      backdrop.hidden = true;
      document.documentElement.classList.remove("is-locked");
      if (cfg.onClose) { cfg.onClose(); }
      if (restore) {
        var t = trigger && trigger.isConnected ? trigger : (cfg.fallback ? cfg.fallback() : null);
        if (t && t.focus) { t.focus({ preventScroll: true }); }
        // 開いたボタンが隠れていた（送って選びが外れた帯のボタンなど）ときは、焦点が宙に浮かない先へ
        if (cfg.fallback && document.activeElement !== t) {
          var f2 = cfg.fallback();
          if (f2 && f2.focus) { f2.focus({ preventScroll: true }); }
        }
      }
      trigger = null;
    }
    return { open: open, close: close, isOpen: function () { return opened; } };
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
    // タブレット・スマホでは左の列をやめ、この器が「ページ一覧」の板として出る（頭と閉じるボタンはそのとき見える）
    aside.appendChild(h("div", { class: "thumbs__head" }, h("h2", { text: "ページ一覧" }),
      h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "ページ一覧を閉じる",
        onclick: function () { if (ctx.closeThumbs) { ctx.closeThumbs(); } } }, SS.icon("close"))));
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
        // 番号は絵の**左上に重ねる**（2026-10-14。桁が違っても絵の左端がそろう）。
        // 振り直しは renumber() が同じ節点を書き換える
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

  // 進み具合の札（上の帯の 1 つ。「13/13 完成」）。語（完成・生成中・待機・要対応）はサーバの counts.labels のまま。
  // 色だけに頼らず、生成中・要対応があるときは語と件数も札の中に出す。件数の細かい並びは title、
  // 「いま何をしているか」の 1 文（サーバが作る headline）は読み上げ用に残す（画面では数え直さない・U11）
  function progressBand(counts) {
    var chip = h("span", { class: "deck-progress", role: "status", "aria-live": "polite" },
      h("span", { class: "deck-progress__text" }, h("span", { class: "deck-progress__count" }), h("span", { class: "deck-progress__rest" })),
      h("span", { class: "visually-hidden deck-progress__headline" }));
    paintBand(chip, counts);
    return chip;
  }

  function paintBand(chip, counts) {
    if (!chip || !counts) { return; }
    var labels = groupLabels(counts);
    var total = counts.total || 0;
    var count = (counts.ready || 0) + "/" + total;
    var parts = [count + " " + (labels.ready || "")];
    ["generating", "waiting", "failed"].forEach(function (g) { if (counts[g]) { parts.push((labels[g] || g) + " " + counts[g]); } });
    var text = parts.join("・");
    var kind = counts.failed ? "failed" : (((counts.generating || 0) + (counts.waiting || 0)) ? "working" : "done");
    var cls = "deck-progress deck-progress--" + kind;
    if (chip.className !== cls) { chip.className = cls; }
    var line = GROUP_ORDER.map(function (g) { return (labels[g] || g) + " " + (counts[g] || 0); }).join(" · ");
    if (chip.title !== line) { chip.title = line; }
    // 幅の足りない画面では「完成」の語を畳む（CSS）。読み上げには全文を残す
    var rest = text.slice(count.length);
    var c = chip.querySelector(".deck-progress__count"), r = chip.querySelector(".deck-progress__rest");
    if (c.textContent !== count) { c.textContent = count; }
    if (r.textContent !== rest) { r.textContent = rest; }
    if (chip.getAttribute("aria-label") !== text) { chip.setAttribute("aria-label", text); }
    var head = chip.querySelector(".deck-progress__headline");
    var headline = counts.headline || "";
    if (head.textContent !== headline) { head.textContent = headline; }
  }

  // ---- 取り直した状態を、その場のページに当てる（画面は作り直さない: スクロール位置と選択を保つ）----
  // **版の一覧は実況では触らない。**/progress はまだでき上がっていない候補だけを返すので、でき上がった版の
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

  // 直しているページは、絵の上に札（編集中・編集待ち）を重ねる。ページの枠・サムネ・直す欄の絵で同じ札
  var EDIT_KEYS = { editing: true, edit_queued: true };
  function editBadge(box, display) {
    var badge = box.querySelector(":scope > .edit-badge");
    if (!EDIT_KEYS[display.key]) { if (badge) { badge.remove(); } return; }
    if (!badge) { badge = h("span", { class: "edit-badge", "aria-hidden": "true" }); box.appendChild(badge); }
    badge.className = "edit-badge edit-badge--" + display.key;
    if (badge.textContent !== display.label) { badge.textContent = display.label; }
  }

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
    // 見出しは直しで変わる（P3。台本を差し替えた直し）。同じ節点に当てる
    if ((page.title || "") !== n.title) {
      n.title = page.title || "";
      var head = n.article.querySelector(".page__title");
      if (head) { head.textContent = n.title; }
    }
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
    editBadge(frame, d);
    n.display = d;
    paintThumb(pctx, page, d, n, pos);
    if (pctx.onPage) { pctx.onPage(page.slide_id); }   // パネルの頭（番号・題・小さな絵）
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
    editBadge(pic, d);
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
      var ctx = { caps: caps, pages: data.pages, reasonIds: reasons.ids, editKinds: data.edit_kinds || [] };
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
      ctx.canPlay = deck.mode === "presentation" || deck.mode === "motion";   // 画像で描くモード（pitch）には出さない
      ctx.visibleCount = function () { return data.pages.filter(function (p) { return !p.hidden; }).length; };
      ctx.positionOf = function (slideId) { return order.indexOf(slideId) + 1; };
      // 発表の画面の番号（非表示・絵の無いページを抜いた何番目か）。そのページが発表に出ないときは 0
      ctx.presentNumber = function (slideId) {
        var skip = {};
        data.pages.forEach(function (p) { skip[p.slide_id] = !!p.hidden || !p.image_url; });
        var n = 0;
        for (var k = 0; k < order.length; k++) {
          if (!skip[order[k]]) { n += 1; }
          if (order[k] === slideId) { return skip[slideId] ? 0 : n; }
        }
        return 0;
      };
      // 発表の画面は別の画面（/slides/<id>/present）。同じウィンドウで開く（DESIGN §1.2）
      ctx.present = function (slideId) {
        // 開始ページは番号ではなく slide_id で渡す（並べ替えの組み直しが終わる前は、発表の側の並びが古いため）
        location.assign("/slides/" + deckId + "/present" + (slideId ? "?s=" + encodeURIComponent(slideId) : ""));
      };
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
        if (hidden) {   // 非表示にしたページは畳む（開けば薄い絵で中身を見られる）
          var art = document.getElementById("page-" + slideId);
          if (art && art.__fold) { art.__fold(true); }
        }
        mutate(SS.api("/api/decks/" + deckId + "/pages/" + slideId + "/visibility", { method: "POST", body: { hidden: hidden, lock_version: lockVersion } }).then(function (r) { if (r.message) { SS.toast(r.message); } }));
      };
      ctx.scrollTo = function (slideId) {
        if (ctx.closeThumbs) { ctx.closeThumbs(false); }   // 一覧の板から選んだら、板を閉じてから動く（背景の固定を外す）
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
        var labelEl = btn.querySelector(".btn__label") || btn;   // 札の文字だけを替える（アイコンを消さない）
        var original = labelEl.textContent;
        btn.disabled = true;
        labelEl.textContent = words.building || original;
        SS.api("/api/decks/" + deckId + "/exports", { method: "POST", body: { format: format } }).then(function (r) {
          // 見せている回にその形式が無いときは、サーバが描き直してから配る（P5）。時間内に終わらなければ
          // URL は無く「作っています」の 1 文だけが来る（もう一度押してもらう）
          if (!r || !r.export || !r.export.download_url) { SS.toast((r && r.message) || words.building || ""); return; }
          if (words.success) { SS.toast(words.success); }
          // ダウンロードはサーバの `Content-Disposition: attachment` が実行する（新しいウィンドウ・
          // タブは開かない。既に版の画像保存で使っている手口と同じ。line 693 相当）
          var a = h("a", { href: r.export.download_url, download: true });
          document.body.appendChild(a);
          a.click();
          a.remove();
        }).catch(SS.toastError).finally(function () {
          btn.disabled = !SS.cap(caps, "export").available;
          labelEl.textContent = original;
        });
      };

      SS.clear(root);
      document.documentElement.classList.remove("is-locked");   // 前の画面で板が開いたままだったときの固定を外す
      var screen = h("div", { class: "deck-screen" });
      // 描画ごとに document / window / matchMedia / IntersectionObserver へ足したものは、画面が外れたら全部外す。
      // 外さないと、描き直しや一覧との往復のたびに溜まり、外された画面（全ページの入力欄を含む）を握り続ける
      var cleanups = [];
      var disposed = false;
      var dispose = function () {
        if (disposed) { return; }
        disposed = true;
        cleanups.forEach(function (fn) { try { fn(); } catch (err) { /* 後片付けの失敗で画面を止めない */ } });
        cleanups = [];
        if (disposer) { disposer.disconnect(); disposer = null; }
      };
      var disposer = null;
      var deckMenuBtn = h("button", { class: "btn btn--icon btn--ghost deck-menu-btn", type: "button", "aria-label": "スライドの操作", "aria-haspopup": "menu" }, SS.icon("more_vert"));
      // 書き出しは**ボタン 1 つ**。押すと形式（PDF・PPTX。語の正本はサーバの `vocabulary.export_formats`）のメニューが開く。
      // **可否は 1 つ（`caps.export`）**。答えが変わったら `paintCaps` が同じボタンに当て直すので、作り直さない。
      // 幅が足りない画面（スマホ）では字を畳んでアイコンだけにする（`aria-label` が名前）
      var exportBtn = h("button", { class: "btn btn--export deck-export", type: "button", "aria-haspopup": "menu", "aria-label": "書き出し" },
        SS.icon("download"), h("span", { class: "btn__label", text: "書き出し" }));
      // 発表（見せる画面）への入口。**可否は 1 つ（`caps.present`）**で、書き出しと同じく `paintCaps` が当て直す
      var presentBtn = h("button", { class: "btn btn--export deck-present", type: "button", "aria-label": "発表" },
        SS.icon("play_arrow"), h("span", { class: "btn__label", text: "発表" }));
      var applyPresentCap = function () {
        var c = SS.cap(caps, "present");
        presentBtn.disabled = !c.available;
        presentBtn.setAttribute("aria-label", c.label);
        presentBtn.querySelector(".btn__label").textContent = c.label;
        if (c.available) { presentBtn.removeAttribute("aria-disabled"); presentBtn.removeAttribute("title"); }
        else { presentBtn.setAttribute("aria-disabled", "true"); presentBtn.setAttribute("title", c.reason || ""); }
        describedBy(presentBtn, reasons.ids, "present");
      };
      applyPresentCap();
      presentBtn.addEventListener("click", function () { ctx.present(null); });
      var applyExportCap = function () {
        var c = SS.cap(caps, "export");
        exportBtn.disabled = !c.available;
        if (c.available) { exportBtn.removeAttribute("aria-disabled"); exportBtn.removeAttribute("title"); }
        else { exportBtn.setAttribute("aria-disabled", "true"); exportBtn.setAttribute("title", c.reason || ""); }
        describedBy(exportBtn, reasons.ids, "export");
      };
      applyExportCap();
      exportBtn.addEventListener("click", function () {
        var c = SS.cap(caps, "export");
        // 形式はサーバのデッキの答え `export_formats`（モードで決まる。モーションは PDF・PPTX を押せなくして理由を出し、
        // HTML（1枚）を出す）。ページの有無は `capabilities.export`。無ければ（古い答え）PDF と PPTX
        var formats = data.export_formats || [{ format: "pdf" }, { format: "pptx" }];
        SS.menu(exportBtn, formats.map(function (f) {
          var ok = c.available && f.available !== false;
          return { label: f.label || exportFormatLabel(f.format), disabled: !ok, reason: c.available ? f.reason : c.reason,
                   onSelect: function () { ctx.exportDeck(exportBtn, f.format); } };
        }));
      });
      // タイトルはその場で直せる（2026-09-18、利用者の指示）。**モーダルを開かない。**
      // 名前を変える口は前からあり（`POST /api/decks/<id>/title`。カードのメニュー「名前を変更」が使う）、
      // ここが足すのは入口だけである。断りの文・長さの上限・lock_version の扱いは口の側のまま変えない。
      //
      // 見出しの h1 と入力欄を入れ替えるのではなく、**同じ箱の中で 2 つを出し入れする**（`hidden`）。
      // 入れ替えると、実況の当て直しが掴んでいる要素が途中で消えて、書きかけの題が飛ぶ。
      var titleText = h("h1", { text: deck.title, title: deck.title });
      var titleInput = h("input", { class: "deck-title-input", type: "text", value: deck.title,
                                    "aria-label": "スライドの名前", maxlength: String(deckLimits().title || 200) });
      var titlePencil = h("button", { class: "btn btn--icon btn--ghost deck-title-edit", type: "button",
                                      "aria-label": "名前を変える", title: "名前を変える" }, pageIcon("pencil"));
      titleInput.hidden = true;
      // 形式の札は題の横（題は 1 行のまま縮む。札は縮まない）
      var kindTag = SS.kindTag(deck.mode, deck.mode_label);
      var titleBox = h("span", { class: "deck-title" }, titleText, titleInput, kindTag, titlePencil);
      var showTitleInput = function (editing) {
        titleText.hidden = editing;
        titleInput.hidden = !editing;
        titlePencil.hidden = editing;
        if (kindTag) { kindTag.hidden = editing; }
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
            titleText.title = deck.title;
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

      // 引き出す板の状態は後ろで作る（メニューと上の帯が先に参照するので、名前だけ先に置く）
      var thumbsSheet = null, panelSheet = null;
      var onPageHook = function () {};   // パネルの頭の当て直し（パネルを作ったあとで本物に替わる）
      var mq = window.matchMedia ? window.matchMedia("(min-width: 1024px)") : { matches: true };
      var isSide = function () { return !!mq.matches; };   // 幅 1024 以上: 右に固定のパネル。それ未満は引き出す板

      // ---- 上の帯（1 行）: 戻る・題・進み具合の札・書き出し・⋮ ----
      // 「13 ページ・ブランド: 既定」「ピン留め」は ⋮ のメニューの頭へ畳んだ。大きな進みの箱は札へ畳んだ。
      var pband = data.pages.length ? progressBand(data.counts) : null;
      // 左の列（ページ一覧）を畳む・開く（PC の幅だけ。畳むとスライドの列が広がる。v1 と同じ・2026-10-14）。
      // 畳んだかはこのブラウザに覚える（localStorage。読めなくても動く）
      var THUMBS_KEY = "qs.deck.thumbs-collapsed";
      var thumbsCollapsed = false;
      try { thumbsCollapsed = localStorage.getItem(THUMBS_KEY) === "1"; } catch (e) { /* 覚えていなくても開いて出す */ }
      var thumbsToggle = h("button", { class: "btn btn--icon btn--ghost deck-bar__thumbs", type: "button",
        "aria-controls": "deck-thumbs" });
      var paintThumbsToggle = function () {
        var label = thumbsCollapsed ? "ページ一覧を開く" : "ページ一覧を畳む";
        SS.clear(thumbsToggle).appendChild(SS.icon(thumbsCollapsed ? "left_panel_open" : "left_panel_close"));
        thumbsToggle.setAttribute("aria-label", label);
        thumbsToggle.setAttribute("title", label);
        thumbsToggle.setAttribute("aria-expanded", thumbsCollapsed ? "false" : "true");
        if (body) { body.dataset.thumbs = thumbsCollapsed ? "closed" : "open"; }
      };
      thumbsToggle.addEventListener("click", function () {
        thumbsCollapsed = !thumbsCollapsed;
        try { localStorage.setItem(THUMBS_KEY, thumbsCollapsed ? "1" : "0"); } catch (e) { /* 覚えられなくても動く */ }
        paintThumbsToggle();
      });
      var deckBar = h("header", { class: "deck-bar" },
        thumbsToggle,
        h("a", { class: "deck-bar__back", href: "/slides", "data-nav": true, "aria-label": "スライド一覧へ戻る" },
          SS.icon("arrow_back"), h("span", { class: "deck-bar__back-text", text: "一覧" })),
        titleBox,
        pband,
        h("span", { class: "deck-actions" },
          presentBtn,
          h("span", { class: "menu-anchor" }, exportBtn),
          h("span", { class: "menu-anchor" }, deckMenuBtn)));
      // この画面は名前を鉛筆でその場で直せるので、メニューには「名前を変更」を出さない（2026-09-18）
      // 「設定を替えて組み直す」（P5）: いまの台本のまま、ブランド・スタイルを選び直して組み直す。
      // 押せるかはサーバの `capabilities.rebuild`（メニューを開いたときの答えを読む）
      deckMenuBtn.addEventListener("click", function () {
        var items = SS.deckActions(deck, caps, reload, { renameInPlace: true });
        var rb = SS.cap(caps, "rebuild");
        items.unshift({ label: rb.label, disabled: !rb.available, reason: rb.reason,
                        onSelect: function () { openRebuild(deckId, reload); } }, "-");
        var ins = SS.cap(caps, "insert_page");
        var head = [];
        // 左の列が無い画面では、ここが番号へ飛ぶ入口になる
        if (!isSide()) { head.push({ label: "ページ一覧", disabled: !order.length, onSelect: function () { thumbsSheet.open(deckMenuBtn); } }); }
        head.push({ label: ins.label || "ページを追加", disabled: !ins.available, reason: ins.reason,
                    onSelect: function () { ctx.addAfter(order.length ? order[order.length - 1] : null); } });
        var di = SS.cap(caps, "deck_instruction");
        head.push({ label: "デッキ全体を直す", disabled: !di.available || !order.length, reason: di.available ? "直せるページがまだありません。" : di.reason,
                    onSelect: function () { ctx.openDeck(deckMenuBtn); } }, "-");
        SS.menu(deckMenuBtn, head.concat(items));
        var ul = document.querySelector(".menu");
        if (ul) {
          var brand = deck.brand ? "ブランド: " + deck.brand.name : "";
          ul.insertBefore(h("li", { class: "menu__info", role: "presentation" },
            [deck.pinned ? "ピン留め" : "", data.revision ? pageCountText(data.pages) : "ページなし", brand].filter(Boolean).join(" ・ ")), ul.firstChild);
        }
      });
      screen.appendChild(deckBar);

      // 企画の帯はいちばん上。待っている人がまず読むのは「いま何をしているか」である
      var planBand = planningBand(data.planning);
      screen.appendChild(planBand);
      var band = limitBanner(data.limit, ctx);
      if (band) { screen.appendChild(band); }
      // 再開のボタンは止まっている帯のすぐ下（帯の中のボタンと同じ「状態の文のすぐ下」に置く）
      var rrow = resumeRow(data.limit, ctx);
      if (rrow) { screen.appendChild(rrow); }
      // まだ使えない操作の理由は、ここに 1 か所だけ（同じ文が押せない部品の数だけ並ぶのを避ける）
      if (reasons.node) { screen.appendChild(reasons.node); }

      // ---- 選んだ数ページ（P3）。選んだ印は画面だけが持つ（読み込み直すと外れる）----
      // 入口は各ページの行の小さなチェック。選んだら上に帯が出て、帯の「直す」でパネルを「まとめて直す」で開く。
      // 選んだページはスライドに枠を付ける
      var words = instructionWords();
      var selected = [];
      ctx.isSelected = function (sid) { return selected.indexOf(sid) >= 0; };
      var selText = h("span", { class: "selection-bar__text" });
      var selEdit = h("button", { class: "btn btn--small btn--primary", type: "button", text: "直す" });
      var selClear = h("button", { class: "btn btn--small", type: "button", text: words.selection_clear || "選ぶのをやめる" });
      var selBar = h("div", { class: "selection-bar", hidden: true, role: "region", "aria-label": "選んだページ" }, selText, selEdit, selClear);
      var pagesBox = pagesInstruction(caps, reasons.ids, {
        // 画像の概念が無いモード（モーション）は、選んだ数ページの欄にも画像の添付・作り直しを出さない
        noImages: data.pages.length > 0 && data.pages.every(function (p) { return p.can_minor && p.can_minor.hidden; }),
        kinds: ctx.editKinds, onSubmit: function (box, btn) { ctx.submitPages(box, btn); } });
      var paintPanel = function () {};   // パネルを作ったあとで本物に替わる
      var panelMode = function () { return null; };
      var closePanel = function () {};
      var paintSelection = function () {
        selected = selected.filter(function (sid) { return order.indexOf(sid) >= 0; });
        var positions = selected.map(function (sid) { return ctx.positionOf(sid); }).sort(function (a, b) { return a - b; });
        pagesBox.__set(positions);
        screen.querySelectorAll(".page__select-box").forEach(function (b) { b.checked = selected.indexOf(b.dataset.slide) >= 0; });
        order.forEach(function (sid) {
          if (nodes[sid]) { nodes[sid].article.classList.toggle("page--selected", selected.indexOf(sid) >= 0); }
        });
        var has = positions.length > 0;
        selBar.hidden = !has;
        screen.classList.toggle("has-selection", has);   // 左の列とパネルは、帯の分だけ下に留まる（重ならない）
        selText.textContent = positions.length + " ページを選んでいます";
        selEdit.setAttribute("aria-label", "選んだ " + positions.length + " ページを直す");
        // 選んでいる間はページごとの「直す」を隠す（CSS）。そこに焦点があったなら帯の「直す」へ移す
        var ae = document.activeElement;
        if (has && ae && ae.classList && ae.classList.contains("page__edit")) { selEdit.focus({ preventScroll: true }); }
        // 選びが 0 になったらパネルの「まとめて直す」は対象が無い。それ以外は見出しと絵を当て直す
        if (panelMode() === "pages") { if (has) { paintPanel(); } else { closePanel(); } }
      };
      ctx.toggleSelect = function (sid, on) {
        var i = selected.indexOf(sid);
        if (on && i < 0) { selected.push(sid); }
        if (!on && i >= 0) { selected.splice(i, 1); }
        paintSelection();
      };
      ctx.clearSelection = function () { selected = []; paintSelection(); };
      selClear.addEventListener("click", function () { ctx.clearSelection(); });
      screen.appendChild(selBar);

      var body = h("div", { class: "deck-body" });
      var aside = thumbsAside(data.pages, caps, ctx);
      aside.id = "deck-thumbs";
      body.appendChild(aside);
      paintThumbsToggle();
      var deckBox = deckInstruction(caps, reasons.ids, { canMinor: deckCanMinor(data.pages), kinds: ctx.editKinds,
        onSubmit: function (box, btn) { ctx.submitDeck(box, btn); } });
      var pagesEl = h("section", { class: "pages", "aria-label": "ページ" });
      if (!data.pages.length) {
        pagesEl.appendChild(h("div", { class: "empty", text: "このスライドにはまだページがありません。" }));
      }
      // 実況で差分を当てるための参照（slide_id → 枠・サムネ・直す欄の束・いま出している状態）
      var nodes = {};
      // 隙間に足す（P11。ADR 0048）。隙間ごとの「＋ページを追加」はやめた（スライドの間に毎回出ると見づらい）。
      // 入口は列の最後の 1 か所・⋮ のメニュー・各ページのメニュー。企画を待っている隙間（押した位置）だけは、
      // これまでどおり押した位置に出す。先頭の隙間は after_slide_id = null
      var lastId = function () { return order.length ? order[order.length - 1] : null; };
      SS.append(pagesEl, insertGap(null, ctx, data, reload, true));
      data.pages.forEach(function (p) {
        var art = pageArticle(p, ctx);
        nodes[p.slide_id] = { article: art, tools: art.__tools, frame: art.querySelector(".frame"), title: p.title || "", page: p,
          stateLabel: p.display.label, headKey: p.display.key + "|" + p.display.label,
          sig: p.image_url ? null : emptySig(p.display, 0) };
        pagesEl.appendChild(art);
        SS.append(pagesEl, insertGap(p.slide_id, ctx, data, reload, true));
      });
      var endGap = insertGap(lastId, ctx, data, reload);
      if (endGap) { endGap.classList.add("gap--end"); pagesEl.appendChild(endGap); }
      // 指定のページの後ろに足す欄を開く（⋮ のメニューから）。末尾は常設の口を開く。それ以外は欄をその場で作る
      ctx.addAfter = function (afterId) {
        var gap = (afterId === lastId() && endGap) ? endGap : pagesEl.querySelector('.gap:not(.gap--end)[data-after="' + (afterId || "") + '"]');
        if (!gap) {
          gap = insertGap(afterId, ctx, data, reload);
          if (!gap) { return; }
          gap.classList.add("gap--ondemand");   // 閉じたら跡を残さない（CSS）
          var art = afterId && nodes[afterId] ? nodes[afterId].article : null;
          if (art) { art.insertAdjacentElement("afterend", gap); } else { pagesEl.insertBefore(gap, pagesEl.firstChild); }
        }
        var openBtn = gap.querySelector(".gap__add");
        if (openBtn && !openBtn.hidden) { openBtn.click(); }
        else { var gta = gap.querySelector("textarea"); if (gta) { gta.focus(); } }
        gap.scrollIntoView({ block: "center", behavior: "auto" });
      };
      body.appendChild(pagesEl);

      // ---- 直すパネル（普段は無い。「直す」を押したときだけ出る）----
      // PC: 右の列（スライドの列は余った幅を使う）/ タブレット: 右から出る引き出し / スマホ: 下から出る画面いっぱいの板。
      // 対象は 3 つ（1 ページ・選んだページ・デッキ全体）で、どれも同じ欄を使い、見出しと上の絵だけが変わる。
      // 欄は作った瞬間から目に見えない置き場（store）にあり、パネルは出す分を**移すだけ**。作り直さないので、
      // 実況の当て直し（可否・絵）も書きかけの文も、閉じても・別のページに替えても今までどおり届く・残る。
      // 開いているかは覚えない（いつも閉じた状態で開く）。
      var store = h("div", { class: "tools-store", hidden: true });
      var panelBackdrop = h("div", { class: "sheet-backdrop", hidden: true });
      var thumbsBackdrop = h("div", { class: "sheet-backdrop", hidden: true });
      var pLabel = h("h2", { class: "panel__label" });
      var pDeck = h("span", { class: "panel__deck small" });
      var pTarget = h("span", { class: "panel__target" });
      var closeBtn = h("button", { class: "btn btn--icon btn--ghost panel__close", type: "button", "aria-label": "パネルを閉じる" }, SS.icon("close"));
      // 1 ページを直しているときは、欄を開いたまま前後のページへ送れる（‹ ›・Alt+←→・スマホは絵を左右にスワイプ）
      var prevBtn = h("button", { class: "btn btn--icon btn--ghost panel__step panel__step--prev", type: "button", "aria-label": "前のページを直す",
        title: "前のページ（Alt+←）" }, SS.icon("chevron_left"));
      var nextBtn = h("button", { class: "btn btn--icon btn--ghost panel__step panel__step--next", type: "button", "aria-label": "次のページを直す",
        title: "次のページ（Alt+→）" }, SS.icon("chevron_right"));
      var media = h("div", { class: "panel__media" });
      var slot = h("div", { class: "panel__slot" });
      var panel = h("aside", { class: "panel", id: "deck-panel", "aria-label": "直す" },
        h("div", { class: "panel__head" }, h("div", { class: "panel__who" }, pLabel, pDeck), closeBtn),
        // 対象の見出しの行。1 ページのときは左右の端に ‹ ›（絵にはかぶせない。絵はできるだけ大きく見せる）
        h("div", { class: "panel__nav" }, prevBtn, pTarget, nextBtn),
        media, slot);
      body.appendChild(panel);
      Object.keys(nodes).forEach(function (sid) { store.appendChild(nodes[sid].tools); });
      store.appendChild(pagesBox);
      store.appendChild(deckBox);

      var mode = null;            // null（閉じている）/ "page" / "pages" / "deck"
      var activeSlide = null;     // 1 ページのときの対象（閉じたあとも、焦点を戻す先のために残す）
      var closedMode = null;
      var lastTrigger = null, mediaSig = "", sideOpen = false, switching = false;
      panelMode = function () { return mode; };
      var shotOf = function (sid) { var img = nodes[sid].frame.querySelector("img"); return img ? img.getAttribute("src") : ""; };
      var targetIds = function () {
        if (mode === "page") { return activeSlide && nodes[activeSlide] ? [activeSlide] : []; }
        if (mode === "pages") { return order.filter(function (sid) { return selected.indexOf(sid) >= 0; }); }
        return [];
      };
      var shot = function (sid, caption) {
        var src = shotOf(sid), n = nodes[sid];
        var pic = h("div", { class: "panel__shot-pic" },
          src ? h("img", { src: src, alt: "ページ " + ctx.positionOf(sid) + ": " + n.title })
              : h("span", { class: "panel__shot-empty small", text: "絵はまだありません" }));
        if (n.display) { editBadge(pic, n.display); }
        return h("figure", { class: "panel__shot" }, pic, caption ? h("figcaption", { class: "small", text: caption }) : null);
      };
      // 上の絵。1 ページはそのページの絵、選んだページは並べる（PC・タブレットは縦に、スマホは横にめくる）、デッキ全体は絵なし
      var paintMedia = function (ids) {
        var sig = mode + "|" + ids.map(function (sid) { return sid + "=" + shotOf(sid) + "=" + nodes[sid].title + "=" + ctx.positionOf(sid)
          + "=" + (nodes[sid].display ? nodes[sid].display.key : ""); }).join(",");
        if (sig === mediaSig) { return; }
        mediaSig = sig;
        SS.clear(media);
        if (mode === "deck") {
          media.appendChild(h("p", { class: "reason panel__deck-note", text: "口調・配色などデッキ全体に効く指示" }));
        } else if (mode === "page" && ids.length) {
          media.appendChild(shot(ids[0]));
        } else if (mode === "pages") {
          var strip = h("ol", { class: "panel__strip", tabindex: "0", "aria-label": "選んだページの絵" });
          ids.forEach(function (sid) {
            strip.appendChild(h("li", { class: "panel__strip-item" }, shot(sid, ctx.positionOf(sid) + " ページ  " + nodes[sid].title)));
          });
          var count = h("p", { class: "panel__strip-count small", "aria-hidden": "true" });
          var paintCount = function () {
            var w = strip.clientWidth || 1;
            var i = Math.max(0, Math.min(ids.length - 1, Math.round(strip.scrollLeft / w)));
            var dots = ids.map(function (_, k) { return k === i ? "●" : "○"; }).join(" ");
            count.textContent = dots + "  " + (i + 1) + " / " + ids.length;
          };
          strip.addEventListener("scroll", paintCount);
          media.appendChild(strip);
          media.appendChild(count);
          paintCount();
        }
      };
      paintPanel = function () {
        var ids = targetIds();
        var target = "";
        if (mode === "page" && ids.length) { target = ctx.positionOf(ids[0]) + " / " + order.length + "　" + nodes[ids[0]].title; }
        else if (mode === "pages") {
          var pos = ids.map(function (sid) { return ctx.positionOf(sid); }).sort(function (a, b) { return a - b; });
          target = pos.join("・") + " ページ（" + ids.length + " ページ）";
        } else if (mode === "deck") { target = "デッキ全体（" + order.length + " ページ）"; }
        var label = mode === "pages" ? "まとめて直す" : "直す";
        if (pLabel.textContent !== label) { pLabel.textContent = label; }
        if (pDeck.textContent !== deck.title) { pDeck.textContent = deck.title; }
        if (pTarget.textContent !== target) { pTarget.textContent = target; }
        panel.setAttribute("aria-label", label + "　" + deck.title + "　" + target);
        panel.dataset.mode = mode || "";
        var at = mode === "page" ? order.indexOf(activeSlide) : -1;
        prevBtn.hidden = nextBtn.hidden = mode !== "page";
        prevBtn.disabled = at <= 0;
        nextBtn.disabled = at < 0 || at >= order.length - 1;
        paintMedia(ids);
        Object.keys(nodes).forEach(function (sid) {
          nodes[sid].article.classList.toggle("page--editing", mode === "page" && sid === activeSlide);
        });
      };
      // ▶ は絵のあるページだけ押せる（絵が来たら実況で押せるようにする）
      var paintPlay = function (sid) {
        var n = nodes[sid], b = n && n.article.querySelector(".page__play");
        if (!b) { return; }
        var ok = !!n.frame.querySelector("img");
        b.disabled = !ok;
        if (ok) { b.removeAttribute("aria-disabled"); b.title = "再生"; }
        else { b.setAttribute("aria-disabled", "true"); b.title = "絵ができたら再生できます"; }
      };
      Object.keys(nodes).forEach(paintPlay);
      onPageHook = function (sid) { paintPlay(sid); if (mode) { paintPanel(); } };
      // 欄をスロットに移す（前の欄は置き場へ戻す）。書きかけの文は節点ごと動くので消えない
      var mount = function (node) {
        var cur = slot.firstElementChild;
        if (cur === node) { return; }
        if (cur) { store.appendChild(cur); }
        if (node) {
          slot.appendChild(node);
          var eb = node.classList.contains("editbox") ? node : node.querySelector(".editbox");
          if (eb && eb.__sync) { eb.__sync(); }   // 持ち越した文の行数を、この幅で数え直す
        }
      };

      var fallbackFocus = function () {
        var n = closedMode === "page" && activeSlide && nodes[activeSlide];
        var edit = n ? n.article.querySelector(".page__edit") : null;
        if (shown(edit)) { return edit; }
        return shown(selEdit) ? selEdit : deckMenuBtn;   // 選んでいる間はページごとの「直す」は隠れる
      };
      var shown = function (el) { return !!(el && el.isConnected && el.getClientRects().length); };
      // スマホのキーボードが出たら、板の高さを見えている高さに合わせる。絵は縮めず、板の中をスクロールできるようにして、
      // 書いている欄を見える位置へ送る（上へ送れば絵が見える）
      var vv = window.visualViewport;
      var fitViewport = function () {
        if (!vv || !panelSheet.isOpen()) { return; }
        panel.style.setProperty("--vv-h", Math.round(vv.height) + "px");
        panel.style.setProperty("--vv-top", Math.round(vv.offsetTop) + "px");
        var ae = document.activeElement;
        if (ae && ae.tagName === "TEXTAREA" && panel.contains(ae)) { ae.scrollIntoView({ block: "nearest" }); }
      };
      if (vv) {
        vv.addEventListener("resize", fitViewport);
        vv.addEventListener("scroll", fitViewport);
        cleanups.push(function () { vv.removeEventListener("resize", fitViewport); vv.removeEventListener("scroll", fitViewport); });
      }
      var afterClose = function () {
        if (switching || !mode) { return; }
        closedMode = mode;
        mount(null);
        mode = null;
        mediaSig = "";
        paintPanel();
        panel.style.removeProperty("--vv-h");
        panel.style.removeProperty("--vv-top");
      };
      panelSheet = makeSheet(panel, panelBackdrop, { fallback: fallbackFocus, onOpen: fitViewport, onClose: afterClose });
      thumbsSheet = makeSheet(aside, thumbsBackdrop, { fallback: function () { return deckMenuBtn; } });
      ctx.closeThumbs = function (restore) { if (thumbsSheet.isOpen()) { thumbsSheet.close(restore !== false); } };
      var applyLayout = function () {
        if (isSide()) {
          // 板が開いたまま幅が広がったら、そのまま右の列に移す
          if (panelSheet.isOpen()) { switching = true; panelSheet.close(false); switching = false; sideOpen = true; }
          thumbsSheet.close(false);
          panel.classList.toggle("is-open", sideOpen);
          body.dataset.panel = sideOpen ? "open" : "closed";
          panel.removeAttribute("role");
          panel.removeAttribute("aria-modal");
        } else {
          if (sideOpen) { sideOpen = false; panel.classList.remove("is-open"); panelSheet.open(lastTrigger); }
          if (!panelSheet.isOpen()) { panel.classList.remove("is-open"); }
          body.dataset.panel = "closed";
          panel.setAttribute("role", "dialog");
          panel.setAttribute("aria-modal", "true");
        }
      };
      var openPanel = function (m, sid, trigger) {
        mode = m;
        activeSlide = sid || null;
        lastTrigger = trigger || document.activeElement;
        mount(m === "page" ? nodes[sid].tools : m === "pages" ? pagesBox : deckBox);
        mediaSig = "";
        paintPanel();
        if (isSide()) { sideOpen = true; applyLayout(); }
        else if (!panelSheet.isOpen()) { panelSheet.open(lastTrigger); }
        // PC は開いたらすぐ書けるよう欄へ。板（スマホ・タブレット）は欄へ移さない: 移すとキーボードがすぐ出て、
        // 見出しとページの絵が上へ押し出される。まず全体を見せ、書くのは欄を押してから（焦点は板の閉じるボタン）
        var ta = slot.querySelector("textarea");
        if (ta && isSide()) { ta.focus({ preventScroll: true }); }
        if (!isSide()) { panel.scrollTop = 0; }
        // 対象のスライドを見える位置に送る（PC。列が狭まるので、開いたあとで探す）
        if (isSide()) {
          var ids = targetIds();
          var first = ids.length && nodes[ids[0]] ? nodes[ids[0]].article : null;
          if (first) { first.scrollIntoView({ block: m === "page" ? "center" : "nearest", behavior: "auto" }); }
        }
      };
      closePanel = function () {
        if (!mode) { return; }
        if (isSide()) {
          sideOpen = false;
          applyLayout();
          afterClose();
          var t = shown(lastTrigger) ? lastTrigger : fallbackFocus();
          if (t && t.focus) { t.focus({ preventScroll: true }); }
        } else { panelSheet.close(true); }
      };
      closeBtn.addEventListener("click", closePanel);
      // PC の列は板ではないので、Esc はここで受ける（板・ダイアログの Esc は、それぞれの中で止まる）
      screen.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !e.defaultPrevented && isSide() && sideOpen && !document.querySelector(".menu")) {
          e.preventDefault();
          closePanel();
        }
      });
      ctx.openTools = function (sid, trigger) { openPanel("page", sid, trigger); };
      // 欄を開いたまま、前後のページへ送る。書きかけの文と選んだ札はページごとの欄に残るので、行き来しても戻る。
      // 元のスライドもついてくる（PC は真ん中の列で見える位置へ。スマホは閉じたときにそのページにいる）
      var step = function (delta, focusText) {
        if (mode !== "page") { return; }
        var at = order.indexOf(activeSlide), sid = order[at + delta];
        if (at < 0 || !sid || !nodes[sid]) { return; }
        activeSlide = sid;
        mount(nodes[sid].tools);
        mediaSig = "";
        paintPanel();
        nodes[sid].article.scrollIntoView({ block: isSide() ? "center" : "nearest", behavior: "auto" });
        if (focusText) { var ta = slot.querySelector("textarea"); if (ta) { ta.focus({ preventScroll: true }); } }
      };
      prevBtn.addEventListener("click", function () { step(-1, false); });
      nextBtn.addEventListener("click", function () { step(1, false); });
      panel.addEventListener("keydown", function (e) {
        if (!e.altKey || e.metaKey || e.ctrlKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) { return; }
        e.preventDefault();
        step(e.key === "ArrowLeft" ? -1 : 1, e.target && e.target.tagName === "TEXTAREA");
      });
      // スマホ・タブレット: 上の絵を左右にスワイプして送る（縦の動きが大きいときはスクロールとみなす）
      var touch = null;
      media.addEventListener("touchstart", function (e) {
        touch = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
      }, { passive: true });
      media.addEventListener("touchend", function (e) {
        if (!touch || mode !== "page" || !e.changedTouches.length) { touch = null; return; }
        var dx = e.changedTouches[0].clientX - touch.x, dy = e.changedTouches[0].clientY - touch.y;
        touch = null;
        if (Math.abs(dx) >= 56 && Math.abs(dx) > Math.abs(dy) * 1.5) { step(dx < 0 ? 1 : -1, false); }
      });
      ctx.openDeck = function (trigger) { openPanel("deck", null, trigger); };
      selEdit.addEventListener("click", function () { openPanel("pages", null, selEdit); });
      var onMq = function () {
        if (!screen.isConnected) { dispose(); return; }
        applyLayout();
      };
      if (mq.addEventListener) {
        mq.addEventListener("change", onMq);
        cleanups.push(function () { mq.removeEventListener("change", onMq); });
      }
      applyLayout();
      paintSelection();

      // ---- ダイアログ（版を選ぶ・ページの記録）。PC は中央、スマホは下からの板 ----
      var dialog = null;   // { kind, sid, shell, sheet, view, state }
      var dialogTitle = function (kind, sid) {
        var n = nodes[sid];
        return (kind === "versions" ? "版を選ぶ" : kind === "play" ? "再生" : "このページの記録") + "　ページ " + ctx.positionOf(sid) + (n && n.title ? "　" + n.title : "");
      };
      var openDialog = function (kind, sid, trigger) {
        var n = nodes[sid];
        if (!n) { return; }
        if (dialog) { dialog.sheet.close(false); }
        var shell = dialogShell(dialogTitle(kind, sid), "dlg--" + kind);
        var d = { kind: kind, sid: sid, shell: shell, view: null, state: { pick: null } };
        if (kind === "versions") { d.view = versionsView(ctx, n.page, d.state); shell.body.appendChild(d.view.el); }
        else if (kind === "play") { d.pv = playView(deckId, sid); shell.body.appendChild(d.pv.el); d.cleanup = d.pv.dispose; d.focus = d.pv.focus; }
        else { shell.body.appendChild(detailsBlock(n.page)); }
        screen.appendChild(shell.backdrop);
        screen.appendChild(shell.el);
        d.sheet = makeSheet(shell.el, shell.backdrop, {
          fallback: function () { return nodes[sid] ? nodes[sid].article.querySelector(".page__edit") : deckMenuBtn; },
          onClose: function () {
            if (d.cleanup) { d.cleanup(); }
            shell.el.remove(); shell.backdrop.remove(); if (dialog === d) { dialog = null; } } });
        if (d.pv) { d.pv.setClose(function () { d.sheet.close(true); }); }
        shell.closeBtn.addEventListener("click", function () { d.sheet.close(true); });
        dialog = d;
        d.sheet.open(trigger);
        var pressed = shell.el.querySelector('.version[aria-pressed="true"]');
        if (pressed) { pressed.focus({ preventScroll: true }); }
        if (d.focus) { d.focus(); }
      };
      ctx.openVersions = function (sid, trigger) { openDialog("versions", sid, trigger); };
      ctx.openPlay = function (sid, trigger) { openDialog("play", sid, trigger); };
      ctx.openRecord = function (sid, trigger) { openDialog("record", sid, trigger); };
      ctx.closeDialog = function () { if (dialog) { dialog.sheet.close(true); } };
      ctx.pageOf = function (sid) { return nodes[sid] ? nodes[sid].page : null; };
      cleanups.push(function () { if (dialog) { dialog.sheet.close(false); } });

      screen.appendChild(body);
      screen.appendChild(store);
      screen.appendChild(panelBackdrop);
      screen.appendChild(thumbsBackdrop);
      root.appendChild(screen);

      // 見ているページを追う。サムネの強調と、固定していないときのパネルの中身に使う
      var current = null;
      var setCurrent = function (slideId) {
        if (slideId === current) { return; }
        current = slideId;
        aside.querySelectorAll(".thumb").forEach(function (b) {
          var on = b.dataset.slide === slideId;
          b.classList.toggle("thumb--current", on);
          if (on) { b.setAttribute("aria-current", "true"); if (isSide()) { b.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" }); } } else { b.removeAttribute("aria-current"); }
        });
      };
      if ("IntersectionObserver" in window) {
        var ratios = {};
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (en) { ratios[en.target.dataset.slide] = en.isIntersecting ? en.intersectionRatio : 0; });
          var best = null, bestR = 0;
          data.pages.forEach(function (p) { var r = ratios[p.slide_id] || 0; if (r > bestR) { bestR = r; best = p.slide_id; } });
          if (best) { setCurrent(best); }
        }, { threshold: [0, 0.25, 0.5, 0.75, 1], rootMargin: "-" + (deckBar.offsetHeight + 8) + "px 0px -20% 0px" });
        pagesEl.querySelectorAll(".page").forEach(function (el) { io.observe(el); });
        cleanups.push(function () { io.disconnect(); });
      }
      // 画面が差し替わったら（別のデッキ・別の画面・描き直し）、上の後片付けを 1 回だけ走らせる
      if (window.MutationObserver) {
        disposer = new MutationObserver(function () { if (!screen.isConnected) { dispose(); } });
        disposer.observe(root, { childList: true });
      }
      cleanups.push(function () { document.documentElement.classList.remove("is-locked"); });
      if (data.pages.length) { setCurrent(data.pages[0].slide_id); }

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
          h("p", null, h("button", { class: "btn btn--small", type: "button", text: "再読み込み", onclick: function () { SS.render(); } }))), selBar);
      };
      var pctx = { caps: caps, nodes: nodes, aside: aside, positionOf: ctx.positionOf, grabbed: null,
                   reasonIds: reasons.ids, justAdopted: {}, capsGen: 0, onPage: function (sid) { onPageHook(sid); } };
      var watchCfg = null;      // 実況の設定（下で作る。送信のあとに乗せ直すために名前を付けて持つ）
      var candidates = {};      // 前の取り直しで「まだ絵になっていない候補版」があったページ
      var grew = false;         // 0 枚だった画面にページが出来たので描き直しを頼んだ（二度頼まない）

      // ---- 版を選ぶ（「この版にする」を押したらその版が使われる。**答えを待ってから絵を替える**）----
      // 並べ替えは楽観、採用は待つ。理由が違うので結論が違う: 落とすのは手の動きの終わりなので待つと
      // 「戻ってから跳ぶ」が見えるが、採用はボタン 1 回で、切り替えて見せてから戻すのは
      // **成果物について嘘をつく**ことになる。断られ方も 4 通りあって珍しくない。
      var adopting = {};   // slide_id → 答えを待っている（そのページのスタック全体を押せなくする）
      // 押している間はそのページの版を 1 つも押せなくする。**見た目の都合ではない**:
      // 採用は毎回 lock_version を上げるので、答えを待たずに次を押すと 2 回目が必ず断られる
      var stackBusy = function (slideId, busy, pressed) {
        var el = dialog && dialog.sid === slideId ? dialog.shell.el : null;
        if (el) {
          el.querySelectorAll(".version, .vdlg__adopt").forEach(function (b) {
            if (busy && !b.disabled) { b.dataset.held = "1"; b.disabled = true; }
            else if (!busy && b.dataset.held === "1") { b.disabled = false; delete b.dataset.held; }
          });
        }
        if (pressed) {
          if (busy) { pressed.setAttribute("aria-busy", "true"); } else { pressed.removeAttribute("aria-busy"); }
        }
      };
      // ページの版の答えを握り直し、ヘッダの版番号と、開いている「版を選ぶ…」「記録」を当て直す。
      // **絵（paintPage）と揃えて呼ぶ**: 絵だけ替わって印が残ると、画面が「どの版が使われているか」について嘘をつく
      var paintVersions = function (page) {
        var n = nodes[page.slide_id];
        if (!n) { return; }
        n.page = page;
        if (dialog && dialog.sid === page.slide_id) {
          if (dialog.view) { dialog.view.update(page); }
          else if (!dialog.shell.body.querySelector("details[open]")) { SS.clear(dialog.shell.body).appendChild(detailsBlock(page)); }
        }
        var ver = n.article.querySelector(".page__ver");
        if (ver) { ver.textContent = "v" + page.version; }
        // 土台にする版は「いま映している版」。採用で動くので、ここで握り直す（送信の照合に使う）
        n.article.dataset.version = page.version_id;
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
      //      あとから作り直す版の一覧・空枠は、押されたその時にここを読む
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
        else if (next.node) { screen.insertBefore(next.node, selBar); }
        reasons = next;
        ctx.reasonIds = next.ids;
        pctx.reasonIds = next.ids;
        // 2 つのボタンが 1 つの可否を分け合うので、当て直すのも 2 つとも（文言は形式のまま保つ）
        applyExportCap();
        applyPresentCap();
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
          var card = box.closest(".page-tools");
          var canMinor = card ? minor[card.dataset.slide] : (freshPages ? deckCanMinor(freshPages) : null);
          box.__caps(SS.cap(freshCaps, key), canMinor || null, next.ids[key]);
        };
        setBox(deckBox, "deck_instruction");
        // 選んだ数ページの欄は、絵を土台にするかをページごとに台本係とエンジンが決めるので、チェックを固定しない
        pagesBox.__box.__caps(SS.cap(freshCaps, "pages_instruction"), { available: true, reason: null }, next.ids.pages_instruction);
        var canPick = SS.cap(freshCaps, "pages_instruction").available;
        screen.querySelectorAll(".page__select-box").forEach(function (b) { b.disabled = !canPick; });
        Object.keys(nodes).forEach(function (sid) {
          setBox(nodes[sid].tools.querySelector(".editbox"), "page_instruction");
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
          // ページの集合が変わった（差し込み・複製・分割…）。書きかけが無ければ描き直す。あれば知らせるだけ
          if (!known) { if (nothingIsBeingTyped()) { reload(); } else { notify("revision", REVISION_CHANGED); } return; }
          // **可否を先に当ててから絵を当てる。** 空枠の中の「作り直す」は `paintPage` が作るので、
          // 順番が逆だと 1 回ぶん古い答えのボタンが残る
          paintCaps(fresh.capabilities || {}, fresh.pages);
          fresh.pages.forEach(function (p) { paintPage(pctx, p); paintVersions(p); });
          paintBand(pband, fresh.counts);
          paintPageCount(fresh.revision && fresh.revision.revision, fresh.pages);
        });
      };
      ctx.adopt = function (slideId, versionId, button, said, onDone) {
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
          // 何が起きたかはサーバの文をそのまま出す（同じ版をもう一度押したときも本文が違う）。
          // 押したダイアログは閉じるので、答えはその場の 1 行ではなく知らせで出す
          if (r.message) { SS.toast(r.message); }
          if (onDone) { onDone(); }
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
      var waitingOrder = null;   // 自分の並べ替えが描き終えるのを待っている並び
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
          var eb = n.tools.querySelector(".editbox");
          if (eb && eb.__target) { eb.__target("ページ " + pos); }
          var fix = n.article.querySelector(".page__edit");
          if (fix) { fix.setAttribute("aria-label", "ページ " + pos + " を直す"); }
          var play = n.article.querySelector(".page__play");
          if (play) { play.setAttribute("aria-label", "ページ " + pos + " を再生"); }
          n.frame.setAttribute("aria-label", "ページ " + pos + "（" + n.stateLabel + "）");
        });
        paintSelection();   // 選んだページの見出し（番号）も動く
        paintPanel();
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
        if (endGap) { pagesEl.appendChild(endGap); }   // 末尾の口は、並べ替えても最後に居る
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
          waitingOrder = confirmed.slice();   // 描き終えるまで、実況の古い並びで戻さない（P3: 並べ替えも仕事になった）
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
        var picked = box.__kind ? box.__kind() : { kind: null, options: [] };
        return { text: ta ? ta.value : "", edit_mode: mode && mode.checked ? "remake" : "minor",
                 attachments: readAttachments(box), kind: picked.kind, options: picked.options };
      };
      var clearBox = function (box) {
        var ta = box.querySelector("textarea");
        if (ta) { ta.value = ""; }
        box.querySelectorAll(".attach").forEach(function (chip) { chip.remove(); });
        if (box.__resetKind) { box.__resetKind(); }
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
      var postInstruction = function (box, btn, body, onDone) {
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
          seenRevision(r.revision_id);   // 直しは新しい描いた回になる。自分の操作を「ほかの場所で」と言わない
          if (onDone) { onDone(r); }
          // 1 ページの直しは、送れたら文を出さない（ページの絵の上の「編集中」の札が伝える）
          if (!(r.started && panelMode() === "page")) { said.appendChild(h("p", { text: r.message || "" })); }
          if (r.started) {
            // 1 ページの直しは、送っても欄を閉じない（続けて頼む・‹ › で次のページへ送れる）。答えは欄の中の 1 行。
            // 選んだページ・デッキ全体は閉じる（対象が終わったので）。答えの文は閉じる欄の中には残せないので、知らせで出す
            if (panelMode() !== "page") {
              closePanel();
              if (r.message) { SS.toast(r.message); }
            }
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
          text: read.text, attachments: read.attachments, kind: read.kind, options: read.options, lock_version: lockVersion,
          expected_base_version_id: n ? n.article.dataset.version : null });
      };
      ctx.submitPages = function (box, btn) {
        var read = readBox(box);
        var ids = order.filter(function (sid) { return selected.indexOf(sid) >= 0; });
        if (!ids.length) { return; }
        postInstruction(box, btn, { scope: "pages", slide_ids: ids, edit_mode: read.edit_mode, text: read.text,
          attachments: read.attachments, kind: read.kind, options: read.options, lock_version: lockVersion },
          function () { ctx.clearSelection(); });
      };
      ctx.submitDeck = function (box, btn) {
        var read = readBox(box);
        var words = instructionWords();
        var shown = order.length;   // **画面が見せている枚数**。確認にもこの数を出す
        var body = { scope: "deck", edit_mode: read.edit_mode, text: read.text,
                     attachments: read.attachments, kind: read.kind, options: read.options, lock_version: lockVersion };
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
        if (next) { screen.insertBefore(next, selBar); }
        band = next;
        // 帯と脇のボタンは**同じ 1 つの値の反対側**なので、必ず一緒に作り直す（二重にも 0 にもしない）
        var nextRow = resumeRow(l, ctx);
        if (rrow) { rrow.remove(); }
        if (nextRow) { screen.insertBefore(nextRow, reasons.node || selBar); }
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
          var row = n && dialog && dialog.sid === c.slide_id && dialog.shell.el.querySelector('.version[data-version="' + c.version_id + '"]');
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
      // **版の一覧だけは実況に載っていない。** 載っているのは「いま使っている版の絵」だけで、
      // その下に積む版の一覧（`versions`）は入っていない。絵が届いた瞬間にスタックの中の絵も
      // 変わるので、**絵が増えた / 差し替わったときは読み直す**（実ブラウザで、これが無いと
      // 版の一覧が空のままになるのを見た。2026-09-18）。
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
      // **自分が作っていない候補が居たら、版の一覧を取り直す。**（2026-09-18）
      // 画面から送ったときは、送った本人が版の行を先に置くので取り直しは要らない。会話から
      // 修正指示を送れるようになった（ROADMAP P10 の `revise`）ので、**画面が知らない候補**が
      // 現れるようになった。置き場が無いと `paintCandidates` は黙って読み飛ばし、出来上がるまで
      // 画面には何も出ない（作っている最中がいちばん見たい時間なのに）。
      var unknownCandidate = function (prog) {
        return (prog.candidates || []).some(function (c) {
          var n = nodes[c.slide_id];
          return c.version_id && !(n && n.page.versions.some(function (v) { return v.id === c.version_id; }));
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
        // **並び・ページの集合が変わった**（P3: 差し込み・複製・分割・非表示・並べ替えは仕事が終わってから変わる）。
        // 自分の並べ替えが描き終えるまでは待つ（実況の古い並びで戻さない）。それ以外は、書きかけが無ければ
        // 描き直し、あれば知らせるだけにする（書いた文を黙って消さない）
        var ids = prog.pages.map(function (p) { return p.slide_id; });
        if (waitingOrder && ids.join("|") === waitingOrder.join("|")) { waitingOrder = null; }
        var settling = !!waitingOrder && busy(prog);
        if (!settling && !saving && !queued && ids.join("|") !== order.join("|")) {
          waitingOrder = null;
          if (nothingIsBeingTyped()) { reload(); return; }
          notify("revision", REVISION_CHANGED);
          return;
        }
        // **可否を先に当ててから絵を当てる。** 空枠の中の「作り直す」は `paintPage` が作るので、
        // 順番が逆だと 1 回ぶん古い答えのボタンが残る（`refresh()` と同じ順序）
        if (capsMoved(prog)) { paintCaps(prog.capabilities, prog.pages); }
        prog.pages.forEach(function (p) { paintPage(pctx, p, true); });
        var finished = paintCandidates(prog.candidates);
        paintLimit(prog.limit);
        // 読み直す理由は**版の一覧だけ**である（絵が届いた / 候補が出来上がった / 画面が知らない
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
        // P3: 直すたびに新しい描いた回（`revision_id`）になる。会話から直したときも中身は実況が当てるので、
        // 回が変わっただけでは知らせない（並びが変わったときは上で描き直す）
        if (prog.revision_id) { seenRevision(prog.revision_id); }
      };
      // 実況の設定は名前を付けて持つ（送信で画像づくりが始まったら、同じ設定で乗せ直す）
      watchCfg = { deckId: deckId, guard: guard, screen: screen, seq: data.last_event_seq,
        active: busy(data), paint: paint, notify: notify };
      startWatch(watchCfg);
    }
  };
})();
