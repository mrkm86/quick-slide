// ホーム（ピン枠 + 最近 10 件）と、すべてのスライド（検索・絞り込み・複製・削除・ごみ箱）。UI.md §2 / R5。
// カードに出すのは **先頭の画像・タイトル・更新日時・枚数の 4 つだけ**（利用者の見本。ADR 0020 追記）。
// 状態の印・進捗の文・目的・タグはカードに出さない（スライドの画面には全部ある）。
// 出す値はすべて API の値（thumbnail_url / title / updated_at / page_count_label）。ここで数えたり丸めたりしない。
// **いま画面から降りている機能は `src/quick_slide/scope.py` に 1 か所で書いてある**（そこに無い語は
// このファイルに出てこない。DB の列と HTTP の口は残っているので、戻すときの繋ぎ直し先もそこにある）。
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
        onSelect: function () {
          if (!SS.confirm("「" + card.title + "」をごみ箱へ移動します。画像は退避され、ごみ箱から元に戻せます。よろしいですか？")) { return; }
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
    var body = h("div", { class: "card__body" }, title, foot, opts.extra || null);
    var el = h("article", { class: "card" + (opts.row ? " card--row" : "") + (opts.noLink ? "" : " card--link"), "aria-label": card.title }, thumb, body);
    return el;
  };

  function homeHero() {
    // 装飾専用の自作 SVG。文字と説明は HTML で表示し、利用者の入力は SVG に入れない。
    var art = h("div", { class: "home-hero__art", "aria-hidden": "true", html:
      '<svg viewBox="0 0 560 340" focusable="false">' +
      '<ellipse class="hero-halo" cx="302" cy="173" rx="210" ry="150"/>' +
      '<g class="hero-guides"><path d="M30 242H516M82 56V291M479 58V274"/><circle cx="82" cy="242" r="5"/><circle cx="479" cy="58" r="5"/></g>' +
      '<g transform="translate(178 34) rotate(9 144 81)"><rect class="hero-slide hero-slide--back" width="288" height="162" rx="12"/><path class="hero-lines" d="M26 34h88M26 48h54"/></g>' +
      '<g transform="translate(84 115) rotate(-8 144 81)"><rect class="hero-slide hero-slide--middle" width="288" height="162" rx="12"/></g>' +
      '<g transform="translate(132 92)"><rect class="hero-shadow" x="0" y="12" width="320" height="180" rx="12"/><rect class="hero-slide" width="320" height="180" rx="12"/>' +
      '<rect class="hero-accent" x="24" y="25" width="28" height="5" rx="2.5"/>' +
      '<path class="hero-lines hero-lines--strong" d="M24 52h128M24 66h89"/><path class="hero-lines" d="M24 93h102M24 106h80M24 119h95"/>' +
      '<rect class="hero-panel" x="187" y="26" width="109" height="128" rx="8"/>' +
      '<rect class="hero-bar hero-bar--light" x="204" y="105" width="16" height="30" rx="4"/><rect class="hero-bar hero-bar--mid" x="231" y="82" width="16" height="53" rx="4"/><rect class="hero-accent" x="258" y="56" width="16" height="79" rx="4"/>' +
      '<circle class="hero-dot" cx="27" cy="154" r="3"/><path class="hero-lines" d="M39 154h30"/></g>' +
      '<g class="hero-guides"><path d="M465 294h20m-10-10v20M57 114h14m-7-7v14"/></g></svg>'
    });
    return h("section", { class: "home-hero", "aria-labelledby": "hero-title" },
      h("div", { class: "home-hero__copy" },
        h("p", { class: "home-hero__eyebrow", text: "伝えたいことを、一枚ずつ。" }),
        h("h1", { id: "hero-title", text: "Quick Slide" }),
        h("p", { class: "home-hero__description", text: "会話や原稿から、日本語のスライドを作ります。" })), art);
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

      var pins = h("section", { class: "section", "aria-labelledby": "pins-h" },
        h("div", { class: "section__head" }, h("h2", { id: "pins-h", text: "ピン留め" }), h("span", { class: "muted small", text: data.pinned.length + " 件" })));
      if (data.pinned.length) {
        pins.appendChild(h("div", { class: "pins" }, data.pinned.map(function (c) { return SS.deckCard(c, caps, { onChanged: refresh }); })));
      } else {
        pins.appendChild(h("p", { class: "muted small", text: "ピン留めしたスライドはありません。カードのメニューからピン留めすると、ここに常に表示されます。" }));
      }
      root.appendChild(pins);

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
      q: params.get("q") || "", status: params.get("status") || "",
      pinned: params.get("pinned") === "1", trash: params.get("trash") === "1",
      offset: parseInt(params.get("offset") || "0", 10) || 0, limit: 24
    };
    function sync() {
      var p = new URLSearchParams();
      if (state.q) { p.set("q", state.q); }
      if (state.status) { p.set("status", state.status); }
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
    var pinChk = h("input", { type: "checkbox", checked: state.pinned });
    var trashBtn = h("button", { class: "btn btn--small" + (state.trash ? " btn--primary" : ""), type: "button", text: "ごみ箱", "aria-pressed": state.trash ? "true" : "false" });
    var toolbar = h("div", { class: "toolbar" }, search, statusSel, h("label", null, pinChk, "ピン留めのみ"), trashBtn);
    root.appendChild(toolbar);
    var countEl = h("p", { class: "muted small", "aria-live": "polite" });
    var listEl = h("div", { class: "list" });
    var pager = h("div", { class: "pager" });
    root.appendChild(countEl); root.appendChild(listEl); root.appendChild(pager);

    var timer = null;
    function reload() { state.offset = 0; sync(); load(); }
    search.addEventListener("input", function () { state.q = search.value; if (timer) { clearTimeout(timer); } timer = setTimeout(reload, 250); });
    statusSel.addEventListener("change", function () { state.status = statusSel.value; reload(); });
    pinChk.addEventListener("change", function () { state.pinned = pinChk.checked; reload(); });
    trashBtn.addEventListener("click", function () { state.trash = !state.trash; trashBtn.className = "btn btn--small" + (state.trash ? " btn--primary" : ""); trashBtn.setAttribute("aria-pressed", state.trash ? "true" : "false"); reload(); });

    function load() {
      countEl.textContent = "読み込んでいます…";
      SS.clear(listEl); SS.clear(pager);
      var p;
      if (state.trash) {
        p = SS.api("/api/trash").then(function (data) { return Object.assign({}, data, { total: data.items.length, trash: true }); });
      } else {
        var qs = new URLSearchParams();
        if (state.q) { qs.set("q", state.q); }
        if (state.status) { qs.set("status", state.status); }
        if (state.pinned) { qs.set("pinned", "1"); }
        qs.set("limit", String(state.limit)); qs.set("offset", String(state.offset));
        p = SS.api("/api/decks?" + qs.toString());
      }
      return p.then(function (data) {
        if (!guard()) { return; }
        var caps = data.capabilities || {};
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
        data.items.forEach(function (c) { listEl.appendChild(SS.deckCard(c, caps, { row: true, onChanged: load })); });
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
