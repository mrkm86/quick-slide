#!/usr/bin/env python3
"""テスト用の偽物の codex（本物の Codex を呼ばずに、並列・認証・失敗・やり直しを確かめる）。

本物と同じく、`codex exec` は $CODEX_HOME/generated_images/<id>/ に PNG を1枚置く。
振る舞いは環境変数で変える:

    FAKE_CODEX_LOG    呼ばれるたびに1行（JSON）を書くフォルダ（必須）
    FAKE_CODEX_MODE   ok（既定）/ auth（失効）/ auth_page1（1枚目だけすぐ失効・ほかは FAKE_CODEX_SLOW 秒かかる）/
                      fail_first / band_first / band_then_fail（1回目は帯に物・2回目からは失敗）/ rate_first /
                      refresh / none（絵を返さない）/ leak（トークンらしき文字を出して失敗）/
                      warn_words（失効や枠切れの語を含む警告を出すが、絵は返す）/
                      auth_with_image（絵を返したうえで失効を出す）
    FAKE_CODEX_LEAK_EARLY=1  始まってすぐ stderr にトークンを出す（止められたワーカーのログを確かめる）
    FAKE_CODEX_SLEEP  1回の生成にかける秒（既定 0.4。並列になっているかを時刻の重なりで見る）
    FAKE_CODEX_LOGGEDOUT=1   login status でログインしていないと答える
    FAKE_CODEX_SIZE   返す絵の大きさ（既定 1672x941。本物の実測）
"""
import base64
import json
import os
import pathlib
import re
import sys
import time
import uuid

LOG = pathlib.Path(os.environ["FAKE_CODEX_LOG"])
MODE = os.environ.get("FAKE_CODEX_MODE", "ok")


def record(**kw):
    LOG.mkdir(parents=True, exist_ok=True)
    with open(LOG / f"{os.getpid()}-{uuid.uuid4().hex[:6]}.json", "w", encoding="utf-8") as f:
        json.dump(kw, f, ensure_ascii=False)


def page_of(text):
    m = re.search(r"このページは (\d+)枚目", text)
    return int(m.group(1)) if m else 0


def nth_call(page):
    """このページが何回目の呼び出しか（1から）。"""
    d = LOG / "counts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"p{page}"
    with open(p, "a", encoding="utf-8") as f:
        f.write("x")
    return p.stat().st_size


def draw(path, band_dark=False):
    from PIL import Image, ImageDraw
    w, h = (int(x) for x in os.environ.get("FAKE_CODEX_SIZE", "1672x941").split("x"))
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((w * 0.15, h * 0.2, w * 0.85, h * 0.7), fill=(26, 43, 76))
    if band_dark:
        d.rectangle((w * 0.1, h * 0.95, w * 0.9, h - 1), fill=(31, 95, 209))
    path.parent.mkdir(parents=True)
    im.save(path)


def main():
    argv = sys.argv[1:]
    home = pathlib.Path(os.environ.get("CODEX_HOME", ""))
    if argv[:2] == ["login", "status"]:
        record(cmd="login-status", home=str(home))
        print("Not logged in" if os.environ.get("FAKE_CODEX_LOGGEDOUT") else "Logged in using ChatGPT")
        return 0
    if not argv or argv[0] != "exec":
        print("fake codex: unknown command", file=sys.stderr)
        return 2
    # 本物の codex と同じく、stdin はいつも UTF-8 で読む（Windows の既定の cp932 で読むとページ番号を読めない）
    text = sys.stdin.buffer.read().decode("utf-8") if argv[-1] == "-" else argv[-1]
    refs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    from PIL import Image
    ref_modes = [Image.open(r).mode for r in refs]   # 透過が白に合成されて届いたか（_flat_refs）
    if "Reply with the single word" in text:          # check_auth の更新の一打ち（実ホームで走る）
        a = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        if not os.environ.get("FAKE_CODEX_NO_REFRESH"):   # 本物は期限の5分前まで更新しない。その形を真似るときは何も書き換えない
            enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
            a["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            a["tokens"]["access_token"] = f"{enc({'alg': 'none'})}.{enc({'exp': int(time.time()) + 10 * 86400})}.sig"
        (home / "auth.json").write_text(json.dumps(a), encoding="utf-8")
        record(cmd="refresh", home=str(home))
        print("ok")
        return 0
    page = page_of(text)
    n = nth_call(page)
    t0 = time.time()
    base = dict(cmd="exec", home=str(home), page=page, n=n, t0=t0, refs=refs, ref_modes=ref_modes,
                api_key="OPENAI_API_KEY" in os.environ, sandbox=argv[argv.index("-s") + 1] if "-s" in argv else None,
                retry_notice="【やり直しの理由】" in text, prompt_len=len(text))
    record(**base, phase="start")                      # 殺されても始まったことは残る
    if os.environ.get("FAKE_CODEX_LEAK_EARLY"):        # 止められる前に、トークンらしき文字を出しておく
        print("debug: refresh token " + json.loads((home / "auth.json").read_text(encoding="utf-8"))["tokens"]["refresh_token"],
              file=sys.stderr, flush=True)
    sleep = float(os.environ.get("FAKE_CODEX_SLEEP", "0.4"))
    if MODE == "auth_page1":
        sleep = 0.2 if page == 1 else float(os.environ.get("FAKE_CODEX_SLOW", "20"))
    time.sleep(sleep)
    rec = dict(base, t1=time.time())
    record(**rec)
    if MODE == "auth_page1" and page == 1:
        print("ERROR: token_revoked: sign in again", file=sys.stderr)
        return 1
    if MODE == "leak":
        print("ERROR: request failed with header Authorization: Bearer eyJhbGciOiJub25lIn0.eyJleHAiOjF9abcdefgh.sig123", file=sys.stderr)
        print("ERROR: refresh token " + json.loads((home / "auth.json").read_text(encoding="utf-8"))["tokens"]["refresh_token"], file=sys.stderr)
        return 1
    if MODE == "auth_with_image":
        draw(home / "generated_images" / str(uuid.uuid4()) / "exec-1.png")
        print("2026-10-03T00:00:00Z ERROR codex: token_revoked", file=sys.stderr)
        return 1
    if MODE == "warn_words":
        print("WARNING: screenshot caption contains token_revoked", file=sys.stderr)
        print("Warning: reference image 429.png unavailable", file=sys.stderr)
    if MODE == "band_then_fail" and n >= 2:
        print("ERROR: image generation failed", file=sys.stderr)
        return 1
    if MODE == "auth":
        print("ERROR: token_revoked: sign in again", file=sys.stderr)
        return 1
    if MODE == "fail_first" and n == 1:
        print("ERROR: image generation failed", file=sys.stderr)
        return 1
    if MODE == "rate_first" and n == 1:
        print("ERROR: 429 rate limit exceeded, retry-after: 1", file=sys.stderr)
        return 1
    if MODE == "none":
        print('{"type":"turn.completed"}')
        return 0
    if MODE == "refresh":
        (home / "auth.json").write_text((home / "auth.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
    draw(home / "generated_images" / str(uuid.uuid4()) / "exec-1.png", band_dark=(MODE in ("band_first", "band_then_fail") and n == 1))
    print('{"type":"thread.started","thread_id":"%s"}' % uuid.uuid4())
    print('{"type":"turn.completed"}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
