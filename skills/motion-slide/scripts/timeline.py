"""台本（または組んだ HTML）の data-dur から、自動再生の時刻表を出す。BGM の山・抜けを拍に合わせるときに使う。

    python3 -E timeline.py <deck.html> [--json]

出力: 拍ごとに「開始の秒  場面:拍  場面の題」。最後の行は終わりの秒。--json なら {"s3:2": 33.6, …, "end": 123.6}
data-dur が無い拍は、ms-config の dur（既定 4200ms）で数える（エンジンと同じ）
"""
import json, re, sys, pathlib

for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932）でなく UTF-8 で出す。読むのはエージェントとサーバ
    _out.reconfigure(encoding="utf-8")

src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
m = re.search(r'<script type="application/json" id="ms-config">(.*?)</script>', src, re.S)
default = json.loads(m.group(1)).get("dur", 4200) if m else 4200
t, out = 0.0, {}
for a in re.findall(r'<section class="scene[^"]*"([^>]*)>', src):
    sid = re.search(r'\bid="([^"]+)"', a).group(1)
    beats = int(re.search(r'data-beats="(\d+)"', a).group(1))
    title = (re.search(r'data-title="([^"]*)"', a) or [None, ""])[1]
    d = re.search(r'data-dur="([^"]*)"', a)
    durs = [int(x) for x in d.group(1).split(",") if x.strip()] if d else []
    for b in range(beats):
        out[f"{sid}:{b + 1}"] = round(t, 2)
        if "--json" not in sys.argv:
            print(f"{t:7.1f}  {sid}:{b + 1:<3} {title if b == 0 else ''}")
        t += (durs[b] if b < len(durs) and durs[b] else default) / 1000
out["end"] = round(t, 2)
print(json.dumps(out, ensure_ascii=False, indent=1) if "--json" in sys.argv else f"{t:7.1f}  終わり")
