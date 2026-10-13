"""台本 JSON の抜け道の再現（Codex のレビューで出た6件）。引数なしで名前の一覧、名前を渡すとその再現を試す。

    python3 -I repro.py <台本.json> <名前>   # 形の検査が期待どおり NG にするか（0=NG にした）
    python3 -I repro.py <台本.json> --assemble <名前>   # 検査を通らなくても組み立てて、他の場面・共通部に影響しない形か（0=影響しない）
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import deckjson as D  # noqa: E402


def set_(path, value):
    def f(d):
        t = d
        for k in path[:-1]:
            t = t[k]
        t[path[-1]] = value
    return f


def add_style(sc, css):
    return lambda d: d["scenes"][sc].__setitem__("style", D.text_of(d["scenes"][sc].get("style", "")) + "\n" + css)


S = ["scenes"]
CASES = {   # 名前: (台本を変える関数, 期待する NG の一部)
    "hook-breakout": (set_(S + [2, "hook"], "}, document.body.dataset.leaked=1, (b,x)=>{"), "hook の文法の誤り"),
    "script-breakout": (set_(S + [2, "script"], "}); document.body.dataset.leaked=1; (() => {"), "script の文法の誤り"),
    "style-end-scene": (add_style(0, "#s1 .a{color:red}</style><script>document.body.dataset.leaked=1</script><style>"), "</style"),
    "style-end-shared": (set_(["style"], "a{}</style><script>1</script><style>"), "</style"),
    "style-comment-swallow": (add_style(0, "/* ここから閉じない"), "閉じていない"),
    "html-upper-script": (set_(S + [0, "html"], "<div class=\"x\" data-in=\"0\">a</div><SCRIPT>document.body.dataset.leaked=1</SCRIPT>"), "<script> は書けない"),
    "html-textarea": (set_(S + [0, "html"], "<textarea><div class=\"x\" data-in=\"0\">a</div>"), "<textarea> は書けない"),
    "html-extra-close": (set_(S + [0, "html"], "<div class=\"x\" data-in=\"0\">a</div></div></div>"), "閉じすぎ"),
    "html-open-comment": (set_(S + [0, "html"], "<!-- <div class=\"x\" data-in=\"0\">a</div>"), "<!-- が閉じていない"),
    "html-iframe": (set_(S + [0, "html"], "<iframe src=\"x\"></iframe>"), "<iframe> は書けない"),
    "stage-script": (set_(["stage"], "<SCRIPT>1</SCRIPT>"), "<script> は書けない"),
    "ref-space": (set_(S + [2, "hook"], "document.getElementById (\"w1\");"), "別の場面"),
    "ref-comment": (set_(S + [2, "hook"], "document.getElementById /* x */ (\"w1\");"), "別の場面"),
    "ref-query": (set_(S + [2, "hook"], "document.querySelector(\"#s2 .col\");"), "別の場面"),
    "ref-dollar": (set_(S + [2, "hook"], "$ ( \"s2\" );"), "別の場面"),
    "css-sibling": (add_style(0, "#s1 ~ .scene {display:none}"), "兄弟の結合子"),
    "css-adjacent": (add_style(0, "#s1+.scene{display:none}"), "兄弟の結合子"),
    "css-has": (add_style(0, "#s1:has(~ .scene){display:none}"), "兄弟の結合子"),
    "css-media": (add_style(0, "@media (min-width:1px){#s1 ~ .scene{display:none}}"), "兄弟の結合子"),
    "css-comment-sibling": (add_style(0, "#s1 /* c */ ~ .scene {display:none}"), "兄弟の結合子"),
    "css-comment-adjacent": (add_style(0, "#s1/**/+/**/.scene{display:none}"), "兄弟の結合子"),
    "css-import": (add_style(0, "@import url(x.css);"), "@import"),
}
# 他の場面の要素を引くための前提（s1 に w1 の要素を足す）
PREP = lambda d: d["scenes"][0].__setitem__("html", D.text_of(d["scenes"][0]["html"]) + "<svg class=\"wires\" id=\"w1\"></svg>")


def main(argv):
    if len(argv) < 2:
        print("\n".join(CASES))
        return 0
    asm = "--assemble" in argv
    path, name = argv[0], [a for a in argv[1:] if not a.startswith("--")][0]
    d = json.load(open(path, encoding="utf-8"))
    PREP(d)
    CASES[name][0](d)
    if asm:
        h = D.assemble(d)
        head_style = "".join(re.findall(r"<style>(.*?)</style>", h, re.S))
        scripts_open = len(re.findall(r"<script\b", h, re.I))
        scripts_close = len(re.findall(r"</script", h, re.I))
        ok = scripts_open == scripts_close and "leaked" not in head_style + re.sub(r"<script.*?</script>", "", h, flags=re.S | re.I)
        print(h) if "--print" in argv else None
        return 0 if ok else 1
    rep = D.validate(d)
    if any(CASES[name][1] in n for n in rep.ng):
        return 0
    print(f"{name}: 期待した NG が出ない: {CASES[name][1]!r} / 出た NG: {rep.ng}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
