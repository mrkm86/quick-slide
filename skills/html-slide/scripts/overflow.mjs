// 資料の HTML のはみ出し診断（snapshot.mjs と review_probe.mjs が共有する）。
// page.evaluate に渡す関数なので、ブラウザの中で動く（外の変数は読めない）。
//
// 返すもの: 1件ずつ {slide（1 から）, kind: "overflow"|"outside", label, detail}
//   overflow … 文字などが箱より大きい（scrollWidth・scrollHeight > clientWidth・clientHeight）
//   outside  … 要素が section の外に出る
export function overflowProblems() {
  const out = [];
  document.querySelectorAll("section[data-slide]").forEach((sec, i) => {
    const sr = sec.getBoundingClientRect();
    sec.querySelectorAll("*").forEach((el) => {
      if (el.closest("aside.notes") || el.closest("svg")) return;
      const r = el.getBoundingClientRect();
      const label = `${el.className || el.tagName}「${(el.textContent || "").trim().slice(0, 16)}」`;
      if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {
        if (el.clientWidth > 0 || el.clientHeight > 0) {
          out.push({ slide: i + 1, kind: "overflow", label, detail: `${el.scrollWidth}x${el.scrollHeight} > ${el.clientWidth}x${el.clientHeight}` });
        }
      }
      if (r.width && (r.left < sr.left - 1 || r.right > sr.right + 1 || r.top < sr.top - 1 || r.bottom > sr.bottom + 1)) {
        out.push({ slide: i + 1, kind: "outside", label, detail: "" });
      }
    });
  });
  return out;
}

export function problemText(p) {
  return p.kind === "overflow" ? `${p.slide}枚目: はみ出し ${p.label} (${p.detail})` : `${p.slide}枚目: 画面の外 ${p.label}`;
}

// 見出し（h1.headline）の行数。ページごとに [{slide, lines}]。折り返しの数を見るだけで、複数行を誤りにはしない
export function headlineLines() {
  const out = [];
  document.querySelectorAll("section[data-slide]").forEach((sec, i) => {
    const h = sec.querySelector("h1.headline");
    if (!h) return;
    const lh = parseFloat(getComputedStyle(h).lineHeight);
    if (lh > 0) out.push({ slide: i + 1, lines: Math.round(h.getBoundingClientRect().height / lh) });
  });
  return out;
}
