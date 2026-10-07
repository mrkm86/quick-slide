// 起動: (1) 語彙と health を読む (2) URL に launch と token があれば attach を 1 回だけ送り、トークンを URL から消す（ADR 0019 §4）
// (3) pathname に応じた画面を描く。新しいウィンドウ・タブを開く API は使わない（DESIGN §1.2「ウィンドウは 1 つ」）。
(function () {
  "use strict";
  var SS = window.SS;
  // Keep the initial shell stable; load the settings screen as a local extension.
  var settingsReady = new Promise(function (resolve, reject) {
    var script = document.createElement("script");
    script.src = "/static/settings.js";
    script.onload = resolve;
    script.onerror = function () { reject(new SS.ApiError("設定画面を読み込めませんでした。再読み込みしてください。", "settings_load")); };
    document.head.appendChild(script);
  });
  var settingsLink = SS.h("a", { class: "settings-link", href: "/settings", "data-nav": true,
    "data-route": "settings", "aria-label": "設定", title: "設定" });
  settingsLink.appendChild(document.getElementById("settings-icon").content.cloneNode(true));
  document.querySelector(".topbar__right").appendChild(settingsLink);

  function describeClient() {
    var uad = navigator.userAgentData || null;
    return {
      user_agent: navigator.userAgent || null,
      inner_width: window.innerWidth,
      inner_height: window.innerHeight,
      user_agent_data: uad ? { brands: uad.brands || null, mobile: !!uad.mobile, platform: uad.platform || null } : null,
      has_opener: !!window.opener,
      has_parent: window.parent !== window,
      clipboard: {
        navigator_clipboard: !!navigator.clipboard,
        clipboard_item: typeof ClipboardItem !== "undefined",
        write: !!(navigator.clipboard && navigator.clipboard.write)
      }
    };
  }

  var params = new URLSearchParams(location.search);
  var launchId = params.get("launch");
  var token = params.get("token");
  if (token) {
    params.delete("token");
    var rest = params.toString();
    history.replaceState(null, "", location.pathname + (rest ? "?" + rest : ""));
  }
  if (launchId) { SS.launchId = launchId; }

  // health（稼働状況は実際の応答から。推測しない）
  fetch("/api/health").then(function (r) { return r.json(); }).then(function (j) {
    var el = document.getElementById("server-state");
    el.textContent = j && j.ok ? "サーバ稼働中" : "サーバの応答はありますが、保存先に問題があります";
  }).catch(function () { document.getElementById("server-state").textContent = "サーバに接続できません"; });

  // attach（launch トークンは 1 回で消費される。結果は通知で伝え、拒否されたときの文面はサーバの日本語をそのまま出す）
  if (launchId && token) {
    var t = token; token = null;
    SS.api("/api/launches/" + encodeURIComponent(launchId) + "/attach", { method: "POST", body: { token: t, client: describeClient() } })
      .then(function () { t = null; })
      .catch(function (e) { t = null; SS.toast(e.message || "ブラウザの起動を記録できませんでした。", { kind: "error" }); });
  }

  // 「新しいスライドは会話から作ります」の文言は状態に左右されない固定の1行なので HTML に直書きしてある
  // （server/vocabulary.py の NEW_DECK_NOTICE が正本。ここでは capabilities を読まない）。
  // どの画面で見せる/隠すかは画面遷移（route）だけで決まるので、その切り替えは core.js の SS.render にある。

  Promise.all([
    SS.api("/api/vocabulary").catch(function () { return null; }),
    settingsReady
  ]).then(function (res) {
    SS.vocab = res[0];
    if (!SS.vocab) { SS.toast("状態名の一覧を取得できませんでした。表示が一部欠けることがあります。", { kind: "error" }); }
    SS.render();
  }).catch(function (e) { SS.clear(document.getElementById("view")).appendChild(SS.errorBlock(e)); });
})();
