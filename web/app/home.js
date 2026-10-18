// ホーム（ピン枠 + 最近 10 件）と、すべてのスライド（検索・絞り込み・複製・削除・ごみ箱）。UI.md §2 / R5。
// カードに出すのは **先頭の画像・タイトル・更新日時・枚数の 4 つだけ**（利用者の見本。ADR 0020 追記）。
// 状態の印・進捗の文・目的・タグはカードに出さない（スライドの画面には全部ある）。
// 出す値はすべて API の値（thumbnail_url / title / updated_at / page_count_label）。ここで数えたり丸めたりしない。
(function () {
  "use strict";
  var SS = window.SS;
  var h = SS.h;
  // カードの操作が使う上限（名前の長さ）。**画面に数を書かない**（正本は server/vocabulary.py）
  function cardLimits() { return (SS.vocab && SS.vocab.card_limits) || {}; }

  // ---- デッキ操作（カードのメニューとスライド画面のメニューで共用。4 つだけ）----
  // `opts.renameInPlace` は「この画面では名前をその場で直せる」の意味で、編集画面だけが渡す。
  // **一覧のカードには鉛筆が無いので、そちらにはメニューの「名前を変更」が要る。** 同じ操作の
  // 入口を 2 つ並べない、というだけの違いなので、作る場所は 1 か所のまま出し分ける
  // （2026-09-18、利用者「編集画面の メニューからは名前を変更は不要になりましたね」）
  SS.deckActions = function (card, caps, onChanged, opts) {
    var id = card.id;
    function run(p, done) {
      return p.then(function (res) { if (done) { done(res); } if (onChanged) { onChanged(res); } }).catch(SS.toastError);
    }
    var rename = { label: "名前を変更",
        onSelect: function () {
          // 断られたらモーダルは閉じず、サーバの日本語がその場に出る（SS.textModal）
          SS.textModal({ title: "スライドの名前を変更", label: "タイトル", value: card.title, submitLabel: "変更する",
            maxlength: cardLimits().title,
            onSubmit: function (value) {
              return SS.api("/api/decks/" + id + "/title", { method: "POST", body: { title: value, lock_version: card.lock_version } })
                .then(function (res) { if (onChanged) { onChanged(res); } });
            } });
        } };
    return [
      opts && opts.renameInPlace ? null : rename,
      { label: card.pinned ? "ピン留めを解除" : "ピン留め",
        onSelect: function () { run(SS.api("/api/decks/" + id + "/pin", { method: "POST", body: { pinned: !card.pinned, lock_version: card.lock_version } })); } },
      { label: "複製",
        onSelect: function () { run(SS.api("/api/decks/" + id + "/duplicate", { method: "POST", body: {} }), function (res) {
          SS.toast("複製しました: " + res.deck.title, { actionLabel: "開く", onAction: function () { SS.navigate("/slides/" + res.deck.id); } });
        }); } },
      "-",
      { label: "削除（ごみ箱へ移動）", danger: true,
        onSelect: async function () {
          if (!(await SS.confirmModal("「" + card.title + "」をごみ箱へ移動します。画像は退避され、ごみ箱から元に戻せます。よろしいですか？"))) { return; }
          run(SS.api("/api/decks/" + id + "/delete", { method: "POST", body: {} }), function (res) {
            SS.toast(res.message || "ごみ箱へ移動しました。", { actionLabel: "元に戻す", onAction: function () {
              SS.api("/api/decks/" + id + "/restore", { method: "POST", body: {} }).then(function (r) {
                SS.toast(r.message || "元に戻しました。"); if (onChanged) { onChanged(r); }
              }).catch(SS.toastError);
            } });
            if (SS.route().name === "deck") { SS.navigate("/"); }
          });
        } }
    ].filter(Boolean);
  };

  // メニューのボタンは**最下段の行の右端**に置く（サムネの上には重ねない。
  // 利用者の指摘「サムネが隠れるのがNG」。2026-09-11）。
  // 下端にあるので popup は**上へ開く**。向きは器のクラスで宣言する（`SS.menu` が読む）。
  function menuButton(card, caps, onChanged) {
    var wrap = h("div", { class: "card__menu menu-anchor menu-anchor--up" });
    var b = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "操作", "aria-haspopup": "menu",
      onclick: function (e) { e.stopPropagation(); SS.menu(b, SS.deckActions(card, caps, onChanged)); } }, SS.icon("more_vert"));
    wrap.appendChild(b);
    return wrap;
  }

  // ---- カード ----
  // 先頭の画像・タイトル・更新日時・枚数だけ。**カード全体がスライドの画面へのリンク**で、
  // 実体はタイトルの <a> を CSS（`.card--link .card__title::after`）でカード全体まで広げたもの。
  // <a> で <article> を包まないのは、中にメニューのボタンがあるため（リンクの中のボタンは壊れる）。
  // 3 段: 画像 / タイトル / 最下段の行（左に日時と枚数、右端にメニューのボタン）。
  SS.deckCard = function (card, caps, opts) {
    opts = opts || {};
    var thumb = h("div", { class: "card__thumb", "aria-hidden": "true" });
    if (card.thumbnail_url) {
      thumb.appendChild(h("img", { src: card.thumbnail_url, alt: "", loading: "lazy" }));
    } else {
      // 画像が無い・取れない場合はそう言う（U1）。空枠を出来上がったように見せない
      thumb.appendChild(h("span", { text: card.page_count === 0 ? "ページなし" : "画像なし" }));
    }
    var title = opts.noLink
      ? h("p", { class: "card__title", text: card.title })
      : h("a", { class: "card__title", href: "/slides/" + card.id, "data-nav": true, text: card.title });
    // 更新日時と枚数を小さく 1 行。枚数の語はサーバが作る（`page_count_label`）
    var line = h("p", { class: "card__line" }, SS.fmtDate(card.updated_at) + " · " + card.page_count_label);
    // 最下段の行: 左に日時と枚数、右端にメニューのボタン（ごみ箱の行はボタンが無いので行だけ）
    var foot = h("div", { class: "card__foot" }, line, opts.noMenu ? null : menuButton(card, caps, opts.onChanged));
    // 形式の札（プレゼン・ピッチ・モーション）。名前と色は語彙から（`SS.kindTag`）
    var tag = SS.kindTag(card.mode, card.mode_label);
    var body = h("div", { class: "card__body" }, tag ? h("div", { class: "card__kind" }, tag) : null, title, foot, opts.extra || null);
    // まとめて操作するための選び（一覧の行だけ。`opts.select = {checked, onChange}`）。
    // 行の左端に置き、リンクの覆いより手前に出すので、押してもスライドの画面へは移らない
    var pick = null;
    if (opts.select) {
      var box = h("input", { type: "checkbox", class: "card__select-box", "aria-label": "「" + card.title + "」を選ぶ", dataset: { deck: card.id } });
      box.checked = !!opts.select.checked;
      box.addEventListener("change", function () { opts.select.onChange(box.checked); });
      pick = h("label", { class: "card__select" }, box);
    }
    var el = h("article", { class: "card" + (opts.row ? " card--row" : "") + (opts.noLink ? "" : " card--link") + (pick ? " card--selectable" : ""),
      "aria-label": card.title }, pick, thumb, body);
    return el;
  };

  // ホームの画像と、会話への依頼・PowerPoint取り込みの入口（ADR 0029）。
  function homeHero() {
    // 装飾専用の自作 SVG。文字と説明は HTML で表示し、利用者の入力は SVG に入れない。
    // 絵は「思いついたら、もう資料。」: 灯った電球（思いつき）から、スライドが重なって出てくる（もう資料）。
    var art = h("div", { class: "home-hero__art", "aria-hidden": "true", html:
      '<svg viewBox="0 0 560 340" focusable="false">' +
      '<ellipse class="hero-halo" cx="320" cy="170" rx="210" ry="150"/>' +
      '<g class="hero-guides"><path d="M30 300H520M60 60h14m-7-7v14"/></g>' +
      '<path class="hero-trail" d="M180 146C222 92 258 58 300 52M178 160C215 140 240 128 262 124M176 190C198 200 212 205 228 206"/>' +
      '<g transform="translate(300 24) rotate(9 100 56)"><rect class="hero-slide hero-slide--back" width="200" height="112" rx="8"/><path class="hero-lines" d="M18 24h62M18 36h38"/></g>' +
      '<g transform="translate(262 92) rotate(-3 108 61)"><rect class="hero-slide hero-slide--middle" width="216" height="121" rx="8"/><path class="hero-lines" d="M18 26h70M18 40h44"/></g>' +
      '<g transform="translate(228 158)"><rect class="hero-shadow" y="8" width="240" height="135" rx="9"/><rect class="hero-slide" width="240" height="135" rx="9"/>' +
      '<g transform="scale(.75)"><rect class="hero-accent" x="24" y="25" width="28" height="5" rx="2.5"/>' +
      '<path class="hero-lines hero-lines--strong" d="M24 52h128M24 66h89"/><path class="hero-lines" d="M24 93h102M24 106h80M24 119h95"/>' +
      '<rect class="hero-panel" x="187" y="26" width="109" height="128" rx="8"/>' +
      '<rect class="hero-bar hero-bar--light" x="204" y="105" width="16" height="30" rx="4"/><rect class="hero-bar hero-bar--mid" x="231" y="82" width="16" height="53" rx="4"/><rect class="hero-accent" x="258" y="56" width="16" height="79" rx="4"/></g></g>' +
      '<path class="hero-bulb-rays" d="M128 112V100M85 132l-9-8M171 132l9-8"/>' +
      '<path class="hero-bulb" d="M106 220C106 206 84 194 84 168A44 44 0 1 1 172 168C172 194 150 206 150 220Z"/>' +
      '<path class="hero-bulb-filament" d="M118 220V192M138 220V192M118 192l5-10 5 10 5-10 5 10"/>' +
      '<rect class="hero-bulb-base" x="108" y="222" width="40" height="9" rx="3"/><rect class="hero-bulb-base" x="110" y="234" width="36" height="9" rx="3"/>' +
      '<path class="hero-accent" d="M117 246h22l-5 8h-12Z"/></svg>'
    });
    var hero = h("section", { class: "home-hero", "aria-labelledby": "hero-title" },
      h("div", { class: "home-hero__copy" },
        h("p", { class: "home-hero__eyebrow", text: "思いついたら、もう資料。" }),
        h("h1", { id: "hero-title", text: "Quick Slide" }),
        h("p", { class: "home-hero__description" }, "頭の中にあることを", h("br"), "整えて、そのままスライドにします。")), art);
    homeStart(hero);
    return hero;
  }

  // 形式の違いの板（スマホの「？」から）。名前・説明・書き出せる形式はサーバの語彙（`deck_kinds`）のまま並べる
  SS.openKindHelp = function (kinds, opener) {
    var close = h("button", { class: "btn btn--icon btn--ghost", type: "button", "aria-label": "閉じる" }, SS.icon("close"));
    var dlg = h("dialog", { class: "kind-help", "aria-labelledby": "kind-help-h" },
      h("div", { class: "kind-help__head" }, h("h2", { id: "kind-help-h", text: "形式の違い" }), close),
      h("ul", { class: "kind-help__list" }, kinds.map(function (k) {
        var outs = (k.outputs || []).map(function (o) { return o && o.label ? o.label : o; }).join("・");
        return h("li", { class: "kind-help__item" }, SS.kindTag(k.mode, k.label),
          k.description ? h("p", { text: k.description }) : null,
          outs ? h("p", { class: "small muted", text: "書き出し: " + outs }) : null);
      })));
    function done() { if (dlg.open) { dlg.close(); } }
    close.addEventListener("click", done);
    dlg.addEventListener("click", function (e) { if (e.target === dlg) { done(); } });   // 板の外（背景）を押したら閉じる
    dlg.addEventListener("close", function () { dlg.remove(); if (opener && opener.isConnected) { opener.focus(); } });
    document.body.appendChild(dlg);
    dlg.showModal();
  };

  function homeStart(hero) {
    var iw = (SS.vocab && SS.vocab.import) || {};
    var limit = ((SS.vocab && SS.vocab.create_limits) || {}).import_bytes || 0;
    var err = h("p", { class: "create__error", role: "alert" });
    var fileInput = h("input", { type: "file", accept: iw.accept || ".pptx", hidden: true, tabindex: "-1", "aria-hidden": "true" });
    var importBtn = h("button", { class: "btn home-import", type: "button", "data-tone": "blue", title: iw.hint || null },
      SS.icon("upload"), h("span", { text: iw.button || "プレゼン資料を取り込む" }));
    // 取り込みは画像生成CLIの有無に依存しない。既存の取り込みAPIが利用可否を判定する。
    var importing = false;
    function sendImport(file) {
      if (!file || importing) { return; }
      err.textContent = "";
      if (!/\.(pptx|pptm|ppsx|potx)$/i.test(file.name || "")) { err.textContent = iw.wrong_type; return; }
      if (limit && file.size > limit) { err.textContent = iw.too_large; return; }
      importing = true;
      importBtn.disabled = true;
      importBtn.lastChild.textContent = iw.working;
      importBtn.setAttribute("aria-busy", "true");
      SS.postFile("/api/imports?runtime=browser&name=" + encodeURIComponent(file.name), file).then(function (r) {
        SS.navigate(r.deck_url || ("/slides/" + r.deck_id));
      }).catch(function (e) {
        importing = false; importBtn.disabled = false;
        importBtn.lastChild.textContent = iw.button;
        importBtn.removeAttribute("aria-busy");
        err.textContent = e.message || "取り込めませんでした。もう一度お試しください。";
      });
    }
    importBtn.addEventListener("click", function () { fileInput.value = ""; fileInput.click(); });
    fileInput.addEventListener("change", function () { sendImport(fileInput.files && fileInput.files[0]); });
    hero.querySelector(".home-hero__copy").appendChild(h("div", { class: "home-start" },
      h("div", null, h("a", { href: "/create", "data-nav": true, class: "btn btn--primary" }, SS.icon("add"), "資料を作成する"),
        h("p", { class: "small muted", text: "会話に渡す依頼文をつくります" })),
      h("div", null, importBtn, h("p", { class: "small muted", text: "PowerPoint のファイルを選択・ドロップ" })), fileInput));
    hero.appendChild(err);
    function hasFile(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0; }
    hero.addEventListener("dragover", function (e) {
      if (!hasFile(e) || importing) { return; }
      e.preventDefault(); hero.classList.add("create--drop"); hero.setAttribute("data-drop", iw.drop);
    });
    hero.addEventListener("dragleave", function (e) { if (!hero.contains(e.relatedTarget)) { hero.classList.remove("create--drop"); } });
    hero.addEventListener("drop", function (e) {
      hero.classList.remove("create--drop");
      if (!hasFile(e)) { return; }
      e.preventDefault(); sendImport(e.dataTransfer.files && e.dataTransfer.files[0]);
    });
  }

  // ---- 新しいバージョンのお知らせ（docs/adr/0020-release-and-update-notice.md）----
  // サーバが GitHub に確かめた答え（`/api/update`）を見て、新しいバージョンがあるときだけホームの頭に**控えめな 1 行**を出す。
  // 閉じるボタンは置かない（更新するまで出し続ける。目立たせないことで、うるさくしない）。
  // 確かめられなかった・止めている・最新のときは何も出さない
  function updateNotice(root, guard) {
    SS.api("/api/update").then(function (st) {
      if (!guard() || !st || !st.update_available || !st.latest_version) { return; }
      root.insertBefore(h("p", { class: "update-notice small", role: "status" },
        h("span", null, h("span", { class: "update-notice__dot", "aria-hidden": "true" }),
          "Quick Slide " + st.latest_version + " が公開されています（現在：" + st.current_version + "）"),
        // 変更点は GitHub のリリースの頁（同じウィンドウで開く）。更新のしかたは 設定 › 更新
        st.release_url ? h("a", { href: st.release_url, rel: "noopener noreferrer", class: "update-notice__notes", text: "変更点" }) : null,
        h("a", { href: "/settings/updates", "data-nav": true, class: "update-notice__more", text: "更新のしかた" })), root.firstChild);
    }).catch(function () { /* お知らせのためにホームを止めない */ });
  }

  // ---- ホーム ----
  SS.views.home = function (root, route, guard) {
    SS.clear(root).appendChild(h("p", { class: "muted", text: "読み込んでいます…" }));
    return SS.api("/api/decks/home").then(function (data) {
      if (!guard()) { return; }
      var caps = data.capabilities || {};
      var refresh = function () { if (guard()) { SS.views.home(root, route, guard); } };
      SS.clear(root);
      root.appendChild(homeHero());

      // ピン留めは 1 件も無ければ節ごと出さない（空の節と案内の文で、作成欄と最近のスライドの間が空く・2026-10-12）
      var pins = null;
      if (data.pinned.length) {
        pins = h("section", { class: "section", "aria-labelledby": "pins-h" },
          h("div", { class: "section__head" }, h("h2", { id: "pins-h", text: "ピン留め" }), h("span", { class: "muted small", text: data.pinned.length + " 件" })));
        pins.appendChild(h("div", { class: "pins" }, data.pinned.map(function (c) { return SS.deckCard(c, caps, { onChanged: refresh }); })));
      }
      if (pins) { root.appendChild(pins); }

      var recent = h("section", { class: "section", "aria-labelledby": "recent-h" },
        h("div", { class: "section__head" }, h("h2", { id: "recent-h", text: "最近のスライド" }),
          h("span", { class: "muted small", text: data.recent.length + " 件" + (data.more_count > 0 ? "（ほかに " + data.more_count + " 件）" : "") }),
          h("div", { class: "grow" }),
          h("a", { href: "/slides", "data-nav": true, class: "section__link small", text: "スライドをすべて見る →" })));
      if (data.recent.length) {
        recent.appendChild(h("div", { class: "cards" }, data.recent.map(function (c) { return SS.deckCard(c, caps, { onChanged: refresh }); })));
      } else {
        recent.appendChild(h("div", { class: "empty", text: "スライドがまだありません。" }));
      }
      root.appendChild(recent);
      updateNotice(root, guard);
    });
  };

  // ---- ごみ箱の見出し（空けられる容量と「空にする」）----
  // **戻せない操作なので、行のボタンと同じ 2 段にする**（窓は出さない）。ただし件数は必ず言う:
  // 「空にする」だけでは何件消えるか分からない（文はサーバの `purge_all_confirm_label`）。
  function trashHead(data, words, reload) {
    var box = h("div", { class: "trash-head" });
    var left = h("p", { class: "muted small" }, h("span", { text: data.reclaimable_label }),
      h("span", { class: "trash-head__notice", text: words.notice }));
    var actions = h("div", { class: "trash-head__actions" });
    function paint(confirming) {
      SS.clear(actions);
      if (confirming) {
        var yes = h("button", { class: "btn btn--small btn--danger", type: "button", text: data.purge_all_confirm_label, onclick: function () {
          yes.disabled = true; yes.textContent = words.working;
          SS.api("/api/trash/purge", { method: "POST", body: {} })
            .then(function (r) { SS.toast(r.message); reload(); })
            .catch(function (e) { paint(false); SS.toastError(e); });
        } });
        actions.appendChild(yes);
        actions.appendChild(h("button", { class: "btn btn--small", type: "button", text: words.cancel, onclick: function () { paint(false); } }));
        yes.focus();
      } else {
        actions.appendChild(SS.disabledButton(data.capabilities, "purge_all", "btn--small"));
        actions.lastChild.addEventListener("click", function () { paint(true); });
      }
    }
    paint(false);
    box.appendChild(left); box.appendChild(actions);
    return box;
  }

  // ---- すべてのスライド ----
  SS.views.list = function (root, route, guard) {
    var params = new URLSearchParams(location.search);
    var state = {
      q: params.get("q") || "", status: params.get("status") || "", mode: params.get("mode") || "",
      pinned: params.get("pinned") === "1", trash: params.get("trash") === "1",
      offset: parseInt(params.get("offset") || "0", 10) || 0, limit: 24
    };
    function sync() {
      var p = new URLSearchParams();
      if (state.q) { p.set("q", state.q); }
      if (state.status) { p.set("status", state.status); }
      if (state.mode) { p.set("mode", state.mode); }
      if (state.pinned) { p.set("pinned", "1"); }
      if (state.trash) { p.set("trash", "1"); }
      if (state.offset) { p.set("offset", String(state.offset)); }
      var qs = p.toString();
      history.replaceState(null, "", "/slides" + (qs ? "?" + qs : ""));
    }

    SS.clear(root);
    root.appendChild(h("div", { class: "view-head" }, h("h1", { text: "スライド" })));
    var vocab = SS.vocab || {};
    var statuses = vocab.deck_statuses || {};

    var search = h("input", { type: "search", placeholder: "タイトル・サブタイトル・目的・読者・タグで検索", value: state.q, "aria-label": "検索" });
    var statusSel = h("select", { "aria-label": "状態" }, h("option", { value: "", text: "すべての状態" }),
      Object.keys(statuses).map(function (k) { return h("option", { value: k, text: statuses[k], selected: state.status === k }); }));
    // 形式の絞り込み（形式の名前は語彙から。`mode=<mode>` で一覧の口に渡す）
    var kindSel = h("select", { "aria-label": "形式" }, h("option", { value: "", text: "すべての形式" }),
      SS.deckKinds().map(function (k) { return h("option", { value: k.mode, text: k.label, selected: state.mode === k.mode }); }));
    var pinChk = h("input", { type: "checkbox", checked: state.pinned });
    var trashBtn = h("button", { class: "btn btn--small" + (state.trash ? " btn--primary" : ""), type: "button", text: "ごみ箱", "aria-pressed": state.trash ? "true" : "false" });
    var toolbar = h("div", { class: "toolbar" }, search, kindSel, statusSel, h("label", null, pinChk, "ピン留めのみ"), trashBtn);
    root.appendChild(toolbar);
    var countEl = h("p", { class: "muted small", "aria-live": "polite" });
    var listEl = h("div", { class: "list" });
    var pager = h("div", { class: "pager" });

    // ---- 選んだスライド（まとめての操作）。編集画面の「選んだページ」と同じ作り: 行の左のチェックで選ぶと、
    // 上に帯が出て、帯のボタンで選んだもの全部に同じ操作をする。選びは画面だけが持ち、読み込み直すと外れる
    // （ページ送り・絞り込みで見えなくなったものを、知らないうちに操作しないため）。ごみ箱の画面では出さない
    var shown = [];       // いま並んでいるカード（API の値）
    var selected = [];    // 選んだスライドの id
    var busy = false;
    var selText = h("span", { class: "selection-bar__text", "aria-live": "polite" });
    var selAll = h("button", { class: "btn btn--small", type: "button", text: "すべて選ぶ", title: "このページに並んでいるスライドをすべて選ぶ" });
    var selPin = h("button", { class: "btn btn--small", type: "button", text: "ピン留め" });
    var selUnpin = h("button", { class: "btn btn--small", type: "button", text: "ピン留めを解除" });
    var selTrash = h("button", { class: "btn btn--small btn--danger", type: "button", text: "ごみ箱へ移動" });
    var selClear = h("button", { class: "btn btn--small", type: "button", text: "選ぶのをやめる" });
    var selBar = h("div", { class: "selection-bar selection-bar--list", hidden: true, role: "region", "aria-label": "選んだスライド" },
      selText, selAll, selPin, selUnpin, selTrash, selClear);
    function chosenCards() { return shown.filter(function (c) { return selected.indexOf(c.id) >= 0; }); }
    function paintSelection() {
      var cards = chosenCards();
      selBar.hidden = cards.length === 0;
      selText.textContent = cards.length + " 件を選んでいます";
      listEl.querySelectorAll(".card__select-box").forEach(function (b) {
        b.checked = selected.indexOf(b.dataset.deck) >= 0;
        b.disabled = busy;
        var art = b.closest(".card");
        if (art) { art.classList.toggle("card--selected", b.checked); }
      });
      // 押しても何も変わらないボタンは押せなくする（全部ピン留め済みなら「ピン留め」は要らない）
      selAll.disabled = busy || cards.length === shown.length;
      selPin.disabled = busy || !cards.some(function (c) { return !c.pinned; });
      selUnpin.disabled = busy || !cards.some(function (c) { return c.pinned; });
      selTrash.disabled = busy || cards.length === 0;
      selClear.disabled = busy;
    }
    function toggleSelect(id, on) {
      var i = selected.indexOf(id);
      if (on && i < 0) { selected.push(id); }
      if (!on && i >= 0) { selected.splice(i, 1); }
      paintSelection();
    }
    // 1 件ずつ順に送る（まとめての口は無い）。途中で断られても残りは続け、終わってから数を言う
    function eachDeck(cards, send) {
      var done = [], failed = [];
      return cards.reduce(function (p, c) {
        return p.then(function () {
          return send(c).then(function () { done.push(c); }, function (e) { failed.push({ card: c, error: e }); });
        });
      }, Promise.resolve()).then(function () { return { done: done, failed: failed }; });
    }
    function runBulk(cards, send, doneWords, after) {
      busy = true; paintSelection();
      return eachDeck(cards, send).then(function (r) {
        busy = false;
        if (r.done.length) { after(r.done); }
        if (r.failed.length) {
          var e = r.failed[0].error;
          SS.toast(r.failed.length + " 件は" + doneWords + "できませんでした（「" + r.failed[0].card.title + "」: " + (e && e.message ? e.message : "処理できませんでした。") + "）",
            { kind: "error", sticky: true });
        }
        selected = [];
        load();
      });
    }
    selAll.addEventListener("click", function () { selected = shown.map(function (c) { return c.id; }); paintSelection(); });
    selClear.addEventListener("click", function () { selected = []; paintSelection(); });
    function bulkPin(pinned) {
      var cards = chosenCards().filter(function (c) { return !!c.pinned !== pinned; });
      runBulk(cards, function (c) {
        return SS.api("/api/decks/" + c.id + "/pin", { method: "POST", body: { pinned: pinned, lock_version: c.lock_version } });
      }, pinned ? "ピン留め" : "ピン留めを解除", function (done) {
        SS.toast(done.length + " 件を" + (pinned ? "ピン留めしました。" : "ピン留めから外しました。"));
      });
    }
    selPin.addEventListener("click", function () { bulkPin(true); });
    selUnpin.addEventListener("click", function () { bulkPin(false); });
    selTrash.addEventListener("click", async function () {
      var cards = chosenCards();
      if (!cards.length) { return; }
      if (!(await SS.confirmModal(cards.length + " 件のスライドをごみ箱へ移動します。画像は退避され、ごみ箱から元に戻せます。よろしいですか？"))) { return; }
      runBulk(cards, function (c) { return SS.api("/api/decks/" + c.id + "/delete", { method: "POST", body: {} }); }, "ごみ箱へ移動", function (done) {
        SS.toast(done.length + " 件をごみ箱へ移動しました。", { actionLabel: "元に戻す", onAction: function () {
          eachDeck(done, function (c) { return SS.api("/api/decks/" + c.id + "/restore", { method: "POST", body: {} }); }).then(function (r) {
            if (r.done.length) { SS.toast(r.done.length + " 件を元に戻しました。"); }
            if (r.failed.length) { SS.toastError(r.failed[0].error); }
            if (guard()) { load(); }
          });
        } });
      });
    });

    root.appendChild(countEl); root.appendChild(selBar); root.appendChild(listEl); root.appendChild(pager);

    var timer = null;
    function reload() { state.offset = 0; sync(); load(); }
    search.addEventListener("input", function () { state.q = search.value; if (timer) { clearTimeout(timer); } timer = setTimeout(reload, 250); });
    statusSel.addEventListener("change", function () { state.status = statusSel.value; reload(); });
    kindSel.addEventListener("change", function () { state.mode = kindSel.value; reload(); });
    pinChk.addEventListener("change", function () { state.pinned = pinChk.checked; reload(); });
    trashBtn.addEventListener("click", function () { state.trash = !state.trash; trashBtn.className = "btn btn--small" + (state.trash ? " btn--primary" : ""); trashBtn.setAttribute("aria-pressed", state.trash ? "true" : "false"); reload(); });

    function load() {
      countEl.textContent = "読み込んでいます…";
      SS.clear(listEl); SS.clear(pager);
      shown = []; selected = []; paintSelection();
      var p;
      if (state.trash) {
        p = SS.api("/api/trash").then(function (data) { return Object.assign({}, data, { total: data.items.length, trash: true }); });
      } else {
        var qs = new URLSearchParams();
        if (state.q) { qs.set("q", state.q); }
        if (state.status) { qs.set("status", state.status); }
        if (state.mode) { qs.set("mode", state.mode); }
        if (state.pinned) { qs.set("pinned", "1"); }
        qs.set("limit", String(state.limit)); qs.set("offset", String(state.offset));
        p = SS.api("/api/decks?" + qs.toString());
      }
      return p.then(function (data) {
        if (!guard()) { return; }
        var caps = data.capabilities || {};
        // 形式ごとの件数（サーバの `modes`）を選択肢の名前に添える
        if (Array.isArray(data.modes)) {
          data.modes.forEach(function (m) {
            var opt = kindSel.querySelector('option[value="' + m.mode + '"]');
            if (opt) { opt.textContent = SS.deckKind(m.mode, m.label).label + "（" + m.count + "）"; }
          });
        }
        if (data.trash) {
          var words = (SS.vocab && SS.vocab.purge) || {};
          countEl.textContent = "ごみ箱: " + data.total + " 件";
          if (!data.items.length) { listEl.appendChild(h("div", { class: "empty", text: words.empty })); return; }
          listEl.appendChild(trashHead(data, words, load));
          data.items.forEach(function (c) {
            // 完全に削除できるかは**行ごとに違う**（作っている途中のものは断られる）ので、
            // 一覧まとめの `caps` ではなくその行に付いてきた答えを見る
            var rowCaps = c.capabilities || caps;
            var restore = h("button", { class: "btn btn--small", type: "button", text: "元に戻す", onclick: function () {
              SS.api("/api/decks/" + c.id + "/restore", { method: "POST", body: {} }).then(function (r) { SS.toast(r.message || "元に戻しました。"); load(); }).catch(SS.toastError);
            } });
            var actions = h("div", { class: "card__meta" });
            // **窓は出さない。ボタンがその場で 2 段になる。**（2026-09-18、利用者「ダイアログいらない」）
            // 戻せない操作なので 1 回押しただけでは消えず、2 回目は違う文字の違うボタンを押すことになる
            function paint(confirming) {
              SS.clear(actions);
              if (confirming) {
                var yes = h("button", { class: "btn btn--small btn--danger", type: "button", text: words.confirm, onclick: function () {
                  yes.disabled = true; yes.textContent = words.working;
                  SS.api("/api/decks/" + c.id + "/purge", { method: "POST", body: {} })
                    .then(function (r) { SS.toast(r.message); load(); })
                    .catch(function (e) { paint(false); SS.toastError(e); });
                } });
                actions.appendChild(yes);
                actions.appendChild(h("button", { class: "btn btn--small", type: "button", text: words.cancel, onclick: function () { paint(false); } }));
                yes.focus();
              } else {
                actions.appendChild(restore);
                var purge = SS.disabledButton(rowCaps, "purge", "btn--small");
                purge.addEventListener("click", function () { paint(true); });
                actions.appendChild(purge);
                if (purge.disabled) { actions.appendChild(h("span", { class: "reason", text: SS.cap(rowCaps, "purge").reason })); }
              }
              actions.appendChild(h("span", { text: "削除 " + SS.fmtDate(c.deleted_at) }));
              // 容量の単位はサーバが書く（画面は数えない。`home.js` 冒頭の規則）
              actions.appendChild(h("span", { class: "muted", text: c.reclaimable_size }));
            }
            paint(false);
            // ごみ箱の行はリンクにしない（削除済みのスライドの画面は開けず、中に「元に戻す」ボタンがある）
            listEl.appendChild(SS.deckCard(c, rowCaps, { row: true, noMenu: true, noLink: true, extra: actions }));
          });
          return;
        }
        var from = data.total === 0 ? 0 : data.offset + 1;
        var to = Math.min(data.offset + data.items.length, data.total);
        countEl.textContent = data.total + " 件" + (data.total ? "（" + from + "–" + to + " 件目）" : "");
        if (!data.items.length) { listEl.appendChild(h("div", { class: "empty", text: "条件に合うスライドはありません。" })); }
        shown = data.items.slice();
        data.items.forEach(function (c) {
          listEl.appendChild(SS.deckCard(c, caps, { row: true, onChanged: load,
            select: { checked: false, onChange: function (on) { toggleSelect(c.id, on); } } }));
        });
        paintSelection();
        if (data.total > state.limit) {
          var prev = h("button", { class: "btn btn--small", type: "button", text: "← 前へ", disabled: data.offset === 0, onclick: function () { state.offset = Math.max(0, state.offset - state.limit); sync(); load(); } });
          var next = h("button", { class: "btn btn--small", type: "button", text: "次へ →", disabled: to >= data.total, onclick: function () { state.offset = state.offset + state.limit; sync(); load(); } });
          pager.appendChild(prev); pager.appendChild(h("span", { text: Math.floor(data.offset / state.limit) + 1 + " / " + Math.ceil(data.total / state.limit) + " ページ" })); pager.appendChild(next);
        }
      }).catch(function (e) { if (guard()) { countEl.textContent = ""; SS.clear(listEl).appendChild(SS.errorBlock(e)); } });
    }
    return load();
  };
})();
