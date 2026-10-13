"""場面ごとのキャッシュの鍵の試験（Chrome は使わない）。ADR 0004。

    python3 -E cache_check.py <台本.json>   # 0=全部通った。落ちたら理由を出して 1
"""
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import shotcache as SC  # noqa: E402

INDEX = '<style>:root{--bg:#fff;--pri:#123456}\n.x{}</style><script type="application/json" id="ms-logo">{"src":"a"}</script>'


def keys(d, wait=1800, index=INDEX):
    return SC.scene_keys(d, SC.shared_inputs(d, index, wait))


def changed(a, b):
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def main(path):
    base = json.load(open(path, encoding="utf-8"))
    n = len(base["scenes"])
    assert n >= 3, "試験の台本は 3 場面以上"
    k0 = keys(base)
    assert len(set(k0)) == n, "場面ごとに鍵が違う"
    assert keys(copy.deepcopy(base)) == k0, "同じ台本なら同じ鍵"

    d = copy.deepcopy(base)                     # 場面の中身を変えたら、その場面だけ変わる
    d["scenes"][1]["talk"][0] += "。"
    assert changed(k0, keys(d)) == [1], changed(k0, keys(d))

    d = copy.deepcopy(base)                     # 前の場面の term を変えたら、その場面と、ここまでの言葉に出る後ろの場面
    d["scenes"][0]["term"] = "別の言葉"
    assert changed(k0, keys(d)) == list(range(n)), changed(k0, keys(d))

    d = copy.deepcopy(base)                     # 後ろの場面の term は前の場面に効かない
    d["scenes"][n - 1]["term"] = "別の言葉"
    assert changed(k0, keys(d)) == [n - 1]

    d = copy.deepcopy(base)                     # 場面の拍の数を変えたら、通しの位置が動く後ろの場面も変わる
    d["scenes"][0]["beats"] += 1
    assert changed(k0, keys(d)) == list(range(n))

    d = copy.deepcopy(base)                     # 場面を足したら、総数が変わるので全部
    d["scenes"].append(copy.deepcopy(d["scenes"][-1])); d["scenes"][-1]["id"] = "extra"
    assert changed(k0, keys(d)[:n]) == list(range(n))

    assert changed(k0, keys(base, wait=3000)) == list(range(n)), "待ちが変われば全部"
    assert changed(k0, keys(base, index=INDEX.replace("#123456", "#654321"))) == list(range(n)), "テーマの色が変われば全部"
    assert changed(k0, keys(base, index=INDEX.replace('"a"', '"b"'))) == list(range(n)), "ロゴが変われば全部"
    d = copy.deepcopy(base)
    d["style"] = (d.get("style") or "") + "\n.x{color:red}"
    assert changed(k0, keys(d)) == list(range(n)), "共通のスタイルが変われば全部"
    d = copy.deepcopy(base)
    d["config"] = {**(d.get("config") or {}), "crumb": "別の名前"}
    assert changed(k0, keys(d)) == list(range(n)), "config が変われば全部"
    d = copy.deepcopy(base)
    d["title"] = "別の題"                      # 台本の題は絵に出ない
    assert keys(d) == k0
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1])
