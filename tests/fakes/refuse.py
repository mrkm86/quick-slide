#!/usr/bin/env python3
"""試験の既定の差し替え先。**本物の claude / codex を試験から呼ばないための網**（呼ばれたら失敗する）。"""
import sys

print("refused: 試験から本物の CLI は呼ばない（偽物を差していない）", file=sys.stderr)
sys.exit(97)
