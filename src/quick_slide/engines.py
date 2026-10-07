"""エンジン（`skills/html-slide`・`skills/image-slide`）の呼び方の正本。モード → スキル・入口・スタイル・引数。

サーバはエンジンの中身を持たない。台本の決まり（`references/deck-format.md` など）は実行時にここから読み、
検査（`check`）と描くこと（`deck`）は子プロセスで呼ぶ。エンジンの終了コードは
0=成功 / 1=一部できなかった / 2=台本・引数の誤り / 4=依存不足・Codex の認証（両エンジン共通）。
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMAGE_LOOKS = ("photo", "illustration", "handdrawn")
# 描いた回で作る出力（エンジンの `--format`）。画面に出すのは `slides/slide-NN.png`
FORMATS = "pptx,pdf,png,thumbs"

RC_OK = 0
RC_PARTIAL = 1
RC_SCRIPT = 2
RC_DEPENDENCY = 4


@dataclass(frozen=True)
class Engine:
    mode: str
    skill: str
    entry: str
    styles: tuple[str, ...]
    default_style: str
    # 台本係に読ませる決まり（`skills/` からの相対）。写しを持たず、毎回ここから読む
    contract_docs: tuple[str, ...]
    deck_extra: tuple[str, ...] = ()
    takes_style_note: bool = False
    needs_codex_auth: bool = False
    label: str = ""

    def entry_path(self, skills_dir: Path) -> Path:
        return skills_dir / self.skill / self.entry

    def style_args(self, args: dict[str, Any]) -> list[str]:
        out = ["--style", str(args.get("style") or self.default_style)]
        if args.get("image_look"):
            out += ["--image-look", str(args["image_look"])]
        if self.takes_style_note and args.get("style_note"):
            out += ["--style-note", str(args["style_note"])]
        return out

    def check_command(self, python: str, skills_dir: Path, script: Path, args: dict[str, Any]) -> list[str]:
        return [python, str(self.entry_path(skills_dir)), "check", "--script", str(script), *self.style_args(args)]

    def deck_command(self, python: str, skills_dir: Path, script: Path, out: Path, args: dict[str, Any]) -> list[str]:
        return [python, str(self.entry_path(skills_dir)), "deck", "--script", str(script), "--out", str(out),
                *self.style_args(args), "--format", FORMATS, *self.deck_extra]

    def revise_command(self, python: str, skills_dir: Path, script: Path, out: Path, args: dict[str, Any], *,
                       page: int, note: str, remake: bool = False, slot: str | None = None,
                       attach: list[Path] | None = None) -> list[str]:
        """絵だけを直す（`revise`）。deck と同じ引数に、直すページと指示を足す。両エンジンとも、直したら
        PPTX・PDF・ページの画像・サムネイルまで組み直す（image-slide は `finish`、html-slide は `cmd_deck` を呼ぶ）。

        - image-slide: そのページの絵（キャッシュの区画の `slide-NN.png`）だけを描く。既定は微修正（今の絵を土台に）
        - html-slide: そのページの生成の枠 1 つ（`--slot`。枠が 1 つなら省ける）だけを描く
        直す前の絵は、どちらもキャッシュの区画の `history/` に残る（戻すのは Quick Slide の版スタックがする）。
        """
        cmd = [python, str(self.entry_path(skills_dir)), "revise", "--script", str(script), "--out", str(out),
               *self.style_args(args), "--format", FORMATS, *self.deck_extra, "--page", str(page), "--note", note]
        if slot and self.mode == "presentation":
            cmd += ["--slot", slot]
        if remake:
            cmd.append("--remake")
        for p in attach or []:
            cmd += ["--attach", str(p)]
        return cmd

    def auth_command(self, python: str, skills_dir: Path) -> list[str] | None:
        """描く前の確かめ（仕事ごとに 1 回）。ピッチは全ページを Codex で描くので `auth` で ChatGPT のログインを見る。
        プレゼンは画像の枠があるときだけ Codex を使い、要るかどうかはエンジンが決める（`deck` の rc=4 で分かる）。"""
        if not self.needs_codex_auth:
            return None
        return [python, str(self.entry_path(skills_dir)), "auth"]


ENGINES: dict[str, Engine] = {
    # プレゼン: 文字も図形も PowerPoint の部品。LibreOffice での照合（--no-odp で飛ばす）は描く回には要らない
    "presentation": Engine(
        mode="presentation", skill="html-slide", entry="scripts/html_slide.py",
        styles=("standard", "visual"), default_style="standard",
        contract_docs=("html-slide/references/deck-format.md",),
        deck_extra=("--no-odp",), label="プレゼン（編集できて動く PPTX）"),
    # ピッチ: 同じ台本を 1 枚ずつ画像生成で丸ごと描く
    "pitch": Engine(
        mode="pitch", skill="image-slide", entry="scripts/image_slide.py",
        styles=("visual", "standard", "infographic"), default_style="visual",
        contract_docs=("html-slide/references/deck-format.md", "image-slide/references/intake.md"),
        takes_style_note=True, needs_codex_auth=True, label="ピッチ（1 枚ずつ画像生成で描く）"),
}


def engine_for(mode: str) -> Engine:
    return ENGINES[mode]


# エンジンの印に入れないもの（試験・設計の記録・見本は描く結果に関わらない）
_MARKER_SKIP = {"tests", "docs", "examples", "__pycache__"}


def engine_marker(skills_dir: Path, engine: Engine) -> dict[str, Any]:
    """どのエンジンで描いたかの印（再現の記録）。スキルのフォルダの中身（試験・文書・見本を除く）の sha256 と、
    取れればリポジトリの HEAD と手を入れた跡（dirty）。配った先に .git が無ければ HEAD は None。"""
    base = skills_dir / engine.skill
    h = hashlib.sha256()
    files = 0
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if any(part in _MARKER_SKIP or part.startswith(".") for part in rel.parts) or not path.is_file():
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        h.update(rel.as_posix().encode("utf-8") + b"\0")
        h.update(path.read_bytes() + b"\0")
        files += 1
    head = dirty = None
    try:
        r = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            head = r.stdout.strip()
            d = subprocess.run(["git", "-C", str(base), "status", "--porcelain", "--", "."], capture_output=True,
                               text=True, timeout=5)
            dirty = bool(d.stdout.strip()) if d.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"skill": engine.skill, "sha256": h.hexdigest(), "files": files, "git_head": head, "git_dirty": dirty}
