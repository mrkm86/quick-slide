"""試験から本物の `pip install` を流さない（呼ばれたら失敗する）。診断が「パッケージが無い」と読み違えたときの網。"""
import sys

print("試験から本物の pip を呼ばない（tests/fakes/no_pip）: " + " ".join(sys.argv[1:]), file=sys.stderr)
sys.exit(1)
