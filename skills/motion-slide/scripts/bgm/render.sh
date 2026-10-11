#!/usr/bin/env bash
# events.json（midi.py の Song.save が書く）を macOS 内蔵の General MIDI 音源で鳴らし、BGM の m4a にする。
# インストール不要（swift と ffmpeg だけ）。macOS 専用（Apple DLS シンセを使う）。
#
#   bash render.sh <events.json があるフォルダ> [音量の上げ幅 dB（既定 9.5）]
#
# 出力: <フォルダ>/bgm.wav と <フォルダ>/bgm.m4a（AAC 128k）。最後に音量（LUFS）を出す。目安は -20〜-18
# ⚠️ loudnorm は使わない（強弱が潰れる）。上げ幅と limiter で合わせる
set -euo pipefail
DIR="$(cd "$1" && pwd)"; GAIN="${2:-9.5}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v swift >/dev/null || { echo "swift が無い（macOS の Xcode Command Line Tools）" >&2; exit 4; }
command -v ffmpeg >/dev/null || { echo "ffmpeg が無い" >&2; exit 4; }
swift "$HERE/render.swift" "$DIR/events.json" "$DIR/raw.wav"
ffmpeg -y -loglevel error -i "$DIR/raw.wav" -af "volume=${GAIN}dB,alimiter=limit=0.89" "$DIR/bgm.wav"
ffmpeg -y -loglevel error -i "$DIR/bgm.wav" -c:a aac -b:a 128k "$DIR/bgm.m4a"
rm -f "$DIR/raw.wav"
ffmpeg -hide_banner -i "$DIR/bgm.m4a" -af ebur128 -f null - 2>&1 | grep -E "^\s+I:" | tail -1
ls -l "$DIR/bgm.m4a"
