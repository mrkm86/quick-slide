"""BGM を音符で書くための小さな道具（標準ライブラリだけ）。曲そのものは台本ごとに compose.py を書く。

    import sys; sys.path.insert(0, "<このフォルダ>"); from midi import Song
    s = Song(bpm=84)
    s.program(0, 0)                    # ch0 = ピアノ（General MIDI の番号）
    s.note(3.2, 0, 62, 70, 0.8)        # 3.2 秒に D4 を強さ 70 で 0.8 秒
    s.drum(82.8, 49, 116, 2)           # 82.8 秒にシンバル（ch9）
    s.save("<出力フォルダ>")            # events.json（render.swift が読む）と song.mid（DAW で開ける）

時刻は秒で書く。山・抜け・アクセントは scripts/timeline.py の秒（拍の切り替わり）に置く。
"""
import json, pathlib, struct


class Song:
    def __init__(self, bpm=84):
        self.bpm, self.ev = bpm, []
        self.beat = 60 / bpm

    def note(self, t, ch, n, vel, dur):
        vel = max(1, min(127, int(vel)))
        self.ev.append((round(t, 4), "on", ch, n, vel)); self.ev.append((round(t + dur, 4), "off", ch, n, 0))

    def drum(self, t, n, vel, dur=.1):
        self.note(t, 9, n, vel, dur)

    def program(self, ch, prog, t=0):
        self.ev.append((t, "pc", ch, prog, 0))

    def cc(self, t, ch, num, val):
        self.ev.append((round(t, 4), "cc", ch, num, val))

    def fade(self, t0, span, chans, steps=16):   # 終わりのフェード（エクスプレッション CC11）
        for i in range(steps):
            for ch in chans:
                self.cc(t0 + i * span / steps, ch, 11, max(0, 127 - i * (127 // steps)))

    def save(self, out):
        out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
        ev = sorted(self.ev, key=lambda e: (e[0], 0 if e[1] in ("pc", "cc", "off") else 1))
        (out / "events.json").write_text(json.dumps([{"t": e[0], "k": e[1], "c": e[2], "a": e[3], "b": e[4]} for e in ev]))
        tpq, trk, last = 480, bytearray(), 0

        def vlq(n):
            b = [n & 0x7F]; n >>= 7
            while n:
                b.insert(0, (n & 0x7F) | 0x80); n >>= 7
            return bytes(b)
        trk += vlq(0) + bytes([0xFF, 0x51, 3]) + int(60_000_000 / self.bpm).to_bytes(3, "big")
        for t, k, ch, a, b in ev:
            tick = round(t / self.beat * tpq); trk += vlq(tick - last); last = tick
            trk += bytes({"on": [0x90 | ch, a, b], "off": [0x80 | ch, a, 0], "pc": [0xC0 | ch, a], "cc": [0xB0 | ch, a, b]}[k])
        trk += vlq(0) + bytes([0xFF, 0x2F, 0])
        (out / "song.mid").write_bytes(b"MThd" + struct.pack(">IHHH", 6, 0, 1, tpq) + b"MTrk" + struct.pack(">I", len(trk)) + trk)
        return len(ev), ev[-1][0] if ev else 0
