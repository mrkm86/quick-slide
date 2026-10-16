#!/usr/bin/env python3
"""デザインテンプレートを、会話から登録・更新・複製・削除・書き出しする（設定の画面と同じ保存の道を通る）。

```text
quick_slide_template.py list   [--brand 名前または ID]
quick_slide_template.py show   --template 名前または ID [--version N] [--brand …]
quick_slide_template.py export --template 名前または ID [--version N] --out <ファイル> [--force]
quick_slide_template.py add    --brand 名前または ID --from <ファイル>        # 新しく登録（いつも新しい ID・v1）
quick_slide_template.py update --template 名前または ID --from <ファイル> [--overwrite] [--dry-run]
quick_slide_template.py copy   --template 名前または ID [--name 新しい名前]
quick_slide_template.py delete --template 名前または ID
```

- 渡すファイルは、前付け（format: quick-slide-template / version: 1 / name / description / modes / resources）と
  本文（`# 構成`・`# 追加指示`）の Markdown 1 枚（UTF-8）。画面の「書き出し」と同じ形。
- 保存はサーバの API を呼ぶ（DB は開かない）。サーバの起こし方・トークン・会話の識別子の採り方は
  `quick_slide_start.py` と同じ部品（`quick_slide_common.py`）。
- ブランドは名前か ID。省けば、アプリが「既定」にしているブランドの中だけで探す。同じ名前が複数なら、候補の ID を返して止まる
  （最初の 1 件を選ばない）。テンプレートも、ブランドの中で名前か ID で探す（ID は直に引く）。
- `update` の既定は新しい版。同じ会話が作った版を、ほかの誰も触っていなければサーバがその版を上書きする。`--overwrite` は最新の版を
  必ず上書き。最新と中身が同じなら何も作らず `unchanged`。`--dry-run` は何も書かずに、何が起きるかだけを返す。

出力: **stdout の最終行は JSON 1 行**（`show` だけは、その前にテンプレートの Markdown を出す）。
成功 `{"status":"ok","action":…,"template_id":…,"version":…,"revision_id":…,"based_on":…,"changed_sections":[…],"message":…}`、
`--dry-run` は `"status":"dry_run"`、失敗は `{"status":"error","code":…,"message":…}`。
終了コードは 0=成功 / 2=入力の誤り / 3=起動できない・届かない / 4=処理の失敗（`quick_slide_start.py` と同じ）。

秘匿: セッショントークン・会話の識別子の生値は出さない。テンプレートの本文は `show` 以外では出さない。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True   # 置いた場所（配る物の中）に __pycache__ を残さない
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quick_slide_common as common  # noqa: E402
from quick_slide_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, Failure, api_failure, detect_conversation, emit, ensure_server, read_record, read_text_file,
    repo_root, request,
)

BASE = "/api/settings/design-templates"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
RUNTIMES = ("claude_code", "codex", "browser", "cli", "other")


# --- サーバとの会話 --------------------------------------------------------

class Session:
    """起こしたサーバへの口。トークンはここの中だけで使い、出力に出さない。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.repo = repo_root(args.repo)
        self.home = common.data_root(args.home)
        ensure_server(self.repo, self.home, args.server_timeout)
        self.record = read_record(self.home)
        runtime, key, _ = detect_conversation()
        self.runtime = args.runtime or runtime
        self.key = args.conversation_key or key

    def get(self, path: str, **kw: Any) -> Any:
        status, body = request(self.record, "GET", path, timeout=60.0, **kw)
        if status != 200:
            raise api_failure(status, body)
        return body

    def post(self, path: str, body: dict[str, Any]) -> Any:
        status, out = request(self.record, "POST", path, body, session=True, timeout=120.0)
        if status not in (200, 201) or not isinstance(out, dict):
            raise api_failure(status, out)
        return out

    def origin(self) -> dict[str, Any]:
        """保存の由来（会話の識別子はサーバが鍵付きのハッシュにして控える。ここでは生で送るだけ）。"""
        out: dict[str, Any] = {"origin_runtime": self.runtime if self.runtime in RUNTIMES else "other"}
        if self.key:
            out["conversation_key"] = self.key
        return out


# --- ブランド・テンプレートを探す -------------------------------------------

def resolve_brand(session: Session, wanted: str | None) -> dict[str, Any]:
    """名前か ID でブランドを 1 つに決める。省けばアプリの既定のブランド。曖昧なら候補を返して止まる。"""
    catalog = session.get("/api/settings")
    brands = [b for b in catalog.get("brands") or [] if isinstance(b, dict)]
    if wanted is None:
        default_id = catalog.get("default_brand_id")
        for b in brands:
            if b.get("id") == default_id:
                return {"id": b["id"], "name": b.get("name")}
        raise Failure("brand_not_found", "既定のブランドが見つかりません。--brand で名前か ID を指定してください。", EXIT_INPUT)
    by_id = [b for b in brands if b.get("id") == wanted]
    matches = by_id or [b for b in brands if b.get("name") == wanted]
    if len(matches) == 1:
        return {"id": matches[0]["id"], "name": matches[0].get("name")}
    if matches:
        raise Failure("brand_ambiguous", f"同じ名前のブランドが {len(matches)} 件あります。--brand に ID を渡してください。", EXIT_INPUT,
                      candidates=[{"id": b["id"], "name": b.get("name")} for b in matches])
    raise Failure("brand_not_found", "その名前か ID のブランドがありません。", EXIT_INPUT,
                  candidates=[{"id": b["id"], "name": b.get("name")} for b in brands])


def get_view(session: Session, template_id: str, version: int | None = None) -> dict[str, Any]:
    suffix = f"?revision={version}" if version is not None else ""
    return session.get(f"{BASE}/{template_id}{suffix}")


def resolve_template(session: Session, wanted: str, brand_arg: str | None, version: int | None = None) -> dict[str, Any]:
    """名前か ID でテンプレートを 1 つに決め、その版（既定は最新）を返す。名前も ID も、ブランド（省けば既定）の中だけ。"""
    brand = resolve_brand(session, brand_arg)   # 名前も ID も、このブランドの中だけ（省けば既定のブランド）
    if UUID_RE.match(wanted):
        view = get_view(session, wanted, version)
        if view["brand_id"] != brand["id"]:
            if brand_arg is None:
                raise Failure("template_other_brand", "その ID のテンプレートは、既定のブランドのものではありません。"
                              "--brand にそのテンプレートのブランドを渡してください。", EXIT_INPUT)
            raise Failure("template_not_found", "そのブランドにその ID のテンプレートがありません。", EXIT_INPUT)
        return view
    rows = session.get(f"{BASE}?brand_id={brand['id']}").get("templates") or []
    matches = [r for r in rows if r["name"] == wanted]
    if len(matches) > 1:
        raise Failure("template_ambiguous", f"同じ名前のテンプレートが {len(matches)} 件あります。--template に ID を渡してください。",
                      EXIT_INPUT, candidates=[{"id": r["template_id"], "name": r["name"], "version": r["revision"]} for r in matches])
    if not matches:
        raise Failure("template_not_found", f"ブランド「{brand['name']}」にその名前のテンプレートがありません。", EXIT_INPUT,
                      brand_id=brand["id"], candidates=[{"id": r["template_id"], "name": r["name"]} for r in rows])
    return get_view(session, matches[0]["template_id"], version)


def require_live(view: dict[str, Any]) -> None:
    if view.get("deleted_at") is not None:
        raise Failure("template_deleted", "このテンプレートは削除済みです。", EXIT_INPUT, template_id=view["template_id"])


def expected_id(session: Session, view: dict[str, Any], args: argparse.Namespace) -> str:
    """競合の検査に送る改訂 ID。`--expected-revision` があればそのまま（最新に差し替えない）、無ければ最新。"""
    given = getattr(args, "expected_revision", None)
    return given if given is not None else latest(session, view)["current_revision_id"]


def latest(session: Session, view: dict[str, Any]) -> dict[str, Any]:
    """版を選んで引いたときも、保存の競合の検査には最新の改訂の ID を使う。"""
    return view if view.get("is_current") else get_view(session, view["template_id"])


# --- ファイル ---------------------------------------------------------------

def markdown_from_file(path: str) -> str:
    """交換ファイル（Markdown）を読む。中身の検査と文書への変換はサーバがする（CLI は製品のコードを import しない）。"""
    return read_text_file(path, "テンプレート").lstrip("\ufeff")   # メモ帳の BOM は外す


# --- 出力 ------------------------------------------------------------------

def outcome(r: dict[str, Any], *, dry: bool = False) -> dict[str, Any]:
    doc = r.get("document") or {}
    out = {"status": "dry_run" if dry else "ok", "action": r.get("action"), "template_id": r.get("template_id"),
           "version": r.get("revision"), "revision_id": r.get("revision_id"), "based_on": doc.get("based_on"),
           "changed_sections": r.get("changed_sections") or [], "message": r.get("message")}
    if dry:
        out["dry_run"] = True
    return out


# --- 各操作 ------------------------------------------------------------------

def cmd_list(session: Session, args: argparse.Namespace) -> int:
    brand = resolve_brand(session, args.brand)
    rows = session.get(f"{BASE}?brand_id={brand['id']}").get("templates") or []
    emit({"status": "ok", "action": "list", "brand_id": brand["id"], "brand_name": brand["name"],
          "templates": [{"template_id": r["template_id"], "name": r["name"], "description": r["description"], "modes": r["modes"],
                         "version": r["revision"], "updated_at": r["updated_at"]} for r in rows],
          "message": f"ブランド「{brand['name']}」のテンプレートは {len(rows)} 件です。"})
    return EXIT_OK


def cmd_show(session: Session, args: argparse.Namespace) -> int:
    view = resolve_template(session, args.template, args.brand, args.version)
    md = session.get(f"{BASE}/{view['template_id']}/markdown?revision={view['revision']}", want_bytes=True).decode("utf-8")
    sys.stdout.write(md if md.endswith("\n") else md + "\n")
    emit({"status": "ok", "action": "show", "template_id": view["template_id"], "name": view["document"]["name"],
          "version": view["revision"], "revision_id": view["revision_id"], "latest_version": view["latest_revision"],
          "message": f"「{view['document']['name']}」の v{view['revision']} です。"})
    return EXIT_OK


def cmd_export(session: Session, args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser()
    if out.exists() and not args.force:
        raise Failure("file_exists", "書き出す先のファイルが既にあります。上書きするなら --force を付けてください。", EXIT_INPUT)
    view = resolve_template(session, args.template, args.brand, args.version)
    data = session.get(f"{BASE}/{view['template_id']}/markdown?revision={view['revision']}", want_bytes=True)
    try:
        with out.open("wb" if args.force else "xb") as stream:   # バイトのまま書く（改行・文字コードを変えない）
            stream.write(data)
    except FileExistsError:
        raise Failure("file_exists", "書き出す先のファイルが既にあります。上書きするなら --force を付けてください。", EXIT_INPUT) from None
    except OSError:
        raise Failure("out_unwritable", "書き出す先に書けませんでした（フォルダがあるか、書く権限があるか確認してください）。", EXIT_INPUT) from None
    emit({"status": "ok", "action": "exported", "template_id": view["template_id"], "version": view["revision"],
          "revision_id": view["revision_id"], "out": str(out), "bytes": len(data),
          "sha256": hashlib.sha256(data).hexdigest(),
          "message": f"「{view['document']['name']}」の v{view['revision']} を書き出しました。"})
    return EXIT_OK


def cmd_add(session: Session, args: argparse.Namespace) -> int:
    brand = resolve_brand(session, args.brand)
    r = session.post(BASE, {"brand_id": brand["id"], "markdown": markdown_from_file(args.from_file), **session.origin()})
    emit({**outcome(r), "brand_id": brand["id"], "message": f"「{r['document']['name']}」を新しく登録しました（v{r['revision']}）"})
    return EXIT_OK


def cmd_update(session: Session, args: argparse.Namespace) -> int:
    view = resolve_template(session, args.template, args.brand)
    require_live(view)
    body = {"markdown": markdown_from_file(args.from_file), "mode": "conversation", "expected_revision_id": expected_id(session, view, args),
            "overwrite": bool(args.overwrite), **session.origin()}
    if args.dry_run:
        r = session.post(f"{BASE}/plan", {"template_id": view["template_id"], **body})
        emit({"status": "dry_run", "dry_run": True, "action": r.get("action"), "template_id": view["template_id"],
              "version": r.get("revision"), "revision_id": None, "based_on": None,
              "expected_revision_id": body["expected_revision_id"],
              "changed_sections": r.get("changed_sections") or [], "message": r.get("message")})
        return EXIT_OK
    r = session.post(f"{BASE}/{view['template_id']}", body)
    emit(outcome(r))
    return EXIT_OK


def cmd_copy(session: Session, args: argparse.Namespace) -> int:
    view = resolve_template(session, args.template, args.brand)
    require_live(view)
    body = {"expected_revision_id": expected_id(session, view, args)}
    if args.name is not None:
        body["name"] = args.name
    r = session.post(f"{BASE}/{view['template_id']}/copy", body)
    emit({**outcome(r), "message": f"「{r['document']['name']}」として複製しました（v{r['revision']}）"})
    return EXIT_OK


def cmd_delete(session: Session, args: argparse.Namespace) -> int:
    view = resolve_template(session, args.template, args.brand)
    require_live(view)
    r = session.post(f"{BASE}/{view['template_id']}/delete", {"expected_revision_id": expected_id(session, view, args)})
    emit(outcome(r))
    return EXIT_OK


COMMANDS = {"list": cmd_list, "show": cmd_show, "export": cmd_export, "add": cmd_add, "update": cmd_update,
            "copy": cmd_copy, "delete": cmd_delete}


class Parser(argparse.ArgumentParser):
    """引数の誤りを、標準の JSON（終了コード 2）で返す。argparse の文は渡された値を引用するので、会話の識別子などが
    stderr に漏れないよう、値を含まない固定の文にする。サブコマンドの parser も同じ型になる。"""

    def error(self, message: str) -> Any:
        raise Failure("bad_arguments", "引数が正しくありません。`--help` で使い方を確かめてください（渡された値はここには出しません）。", EXIT_INPUT)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="quick_slide_template.py", description="デザインテンプレートを会話から登録・更新・複製・削除・書き出しする")
    sub = p.add_subparsers(dest="command", required=True)

    def common_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--home", help=f"データの置き場（既定: ${common.ENV_HOME} または ~/.quick-slide）")
        sp.add_argument("--repo", help=f"実体のあるリポジトリ（この実行だけ。{common.ENV_REPO} より優先）")
        sp.add_argument("--server-timeout", type=float, default=30.0, help="サーバの起動を待つ秒数")
        sp.add_argument("--runtime", choices=RUNTIMES, help="保存元（既定は環境変数から）")
        sp.add_argument("--conversation-key", help="会話の識別子を明示する（既定は環境変数から。定数は渡さない）")

    def add(name: str, help_text: str, *, brand: bool, template: bool) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text)
        if brand:
            sp.add_argument("--brand", required=name == "add", help="ブランドの名前か ID（省けばアプリの既定のブランド）")
        if template:
            sp.add_argument("--template", required=True, help="テンプレートの名前か ID")
            if name in ("update", "copy", "delete"):
                sp.add_argument("--expected-revision", help="競合の検査に使う改訂の ID（--dry-run が返した expected_revision_id）。"
                                "渡すと、最新に差し替えずにそのまま送る（間に別の保存があれば 409）")
        common_opts(sp)
        return sp

    add("list", "ブランドのテンプレートの一覧", brand=True, template=False)
    for name, text in (("show", "テンプレートの中身（Markdown）を出す"), ("export", "テンプレートを Markdown のファイルに書き出す")):
        sp = add(name, text, brand=True, template=True)
        sp.add_argument("--version", type=int, help="版の番号（省けば最新）")
        if name == "export":
            sp.add_argument("--out", required=True, help="書き出す先のファイル")
            sp.add_argument("--force", action="store_true", help="既にあるファイルを上書きする")
    sp = add("add", "ファイルから新しく登録する（v1）", brand=True, template=False)
    sp.add_argument("--from", dest="from_file", required=True, help="Markdown のファイル（UTF-8）")
    sp = add("update", "ファイルの内容で更新する（既定は新しい版）", brand=True, template=True)
    sp.add_argument("--from", dest="from_file", required=True, help="Markdown のファイル（UTF-8）")
    sp.add_argument("--overwrite", action="store_true", help="最新の版を上書きする（既定は新しい版）")
    sp.add_argument("--dry-run", action="store_true", help="何も保存せず、何が起きるかだけを返す")
    sp = add("copy", "別のテンプレートとして複製する（v1）", brand=True, template=True)
    sp.add_argument("--name", help="複製の名前（省けば「…のコピー」）")
    add("delete", "削除する（一覧から外す。版の控えは残る）", brand=True, template=True)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return COMMANDS[args.command](Session(args), args)
    except Failure as failure:
        emit({"status": "error", "code": failure.code, "message": failure.message, **failure.extra})
        return failure.exit_code


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    raise SystemExit(main())
