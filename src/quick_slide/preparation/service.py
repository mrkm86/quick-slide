"""素材準備の共通サービス（§3.1）。`prepare_run` が、入口の確認 → 読解（準備係 LLM）までを世代として作り、`TemplateRun` を返す。
台本係の段（検査・素材解決・控え）は `TemplateRun` の各メソッドをワーカー（台本を書く・直す）が呼ぶ。

- 子プロセスは起こさない（スキルの読み取りは 6b）。LLM（`llm`）とエンジンの検査は呼ぶ側が渡す
- 段の印は `record.preparation`（`records.py`）。完了した段は、入力・出力ハッシュが全部合うときだけ再利用する
- 世代の中身は `DataRoot.preparation_dir` の下に置く: `intake.json`・`resource-catalog.json`・`reading.json`・`assets.json`・
  `bindings.json`・`manifest.json`・`assets/`。ファイルは一時名へ書いてから置き換える
- 直し・差し込み・再試行は、完了した世代を引き継いだ新しい世代（`parent`）を作る。既存の世代は書き換えない
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..paths import DataRoot
from . import llm as LLM
from .assets import ASSETS_DIR, asset_total, link_or_copy, resolve_assets, verify_assets
from .catalog import SUPPORTED_ROLES, Catalog, CatalogItem, FolderProvider, Reference, issue_ids
from .engine_input import materialize_engine_input
from .errors import PreparationStopped
from .files import read_json, sha256_file, verify, write_json
from .intake import read_resources
from .policy import DEFAULT_POLICY, Policy
from .reading import Reader
from .records import PrepRecord, allocate_generation, deck_generations
from .references import PREP_KEY, REF_KEY, RefContext, page_references, stamp, validate_script

MODE_LABELS = {"pitch": "ピッチ（画像。ページ全体を画像生成で描く）", "presentation": "プレゼン（HTML）"}
MANIFEST_SCHEMA = 1
INTAKE, CATALOG, READING, ASSETS, BINDINGS, MANIFEST = ("intake.json", "resource-catalog.json", "reading.json", "assets.json",
                                                         "bindings.json", "manifest.json")


def load_compiled(ctx: Any, run: dict[str, Any]) -> dict[str, str]:
    """回のコンパイル控え（`writer.md`・`preparation.md`）。記録したハッシュと合わなければ使わない。"""
    look = run["args"].get("look") or {}
    folder = look.get("compilation_dir")
    rec = ctx.call(ctx.store.run, run["id"]).get("record") or {}
    listed = ((rec.get("look") or {}).get("compilation") or {}).get("files") or {}
    if not folder:
        raise PreparationStopped("テンプレートのコンパイル結果が見つかりません。", "compilation_missing")
    out = {}
    for name in ("writer.md", "preparation.md"):
        try:
            text = (Path(folder) / name).read_text(encoding="utf-8")
        except OSError:
            raise PreparationStopped(f"テンプレートのコンパイル結果（{name}）が見つかりません。", "compilation_missing") from None
        if listed.get(name) != hashlib.sha256(text.encode("utf-8")).hexdigest():
            raise PreparationStopped(f"テンプレートのコンパイル結果（{name}）が、控えと違っています。", "compilation_changed")
        out[name] = text
    return out


class TemplateRun:
    """テンプレートで作る回の素材準備の状態。台本係へ渡す欄・検査・素材解決・控えの口を持つ。"""

    def __init__(self, ctx: Any, run: dict[str, Any], *, doc: dict[str, Any], compiled: dict[str, str], reader: Reader,
                 boundary: Callable[[str], None] | None = None) -> None:
        self.ctx, self.run, self.doc, self.compiled, self.reader = ctx, run, doc, compiled, reader
        self.policy = reader.policy
        self.boundary = boundary or (lambda name: None)
        self.mode = run["mode"]
        self.root: DataRoot = ctx.root
        self.deck_id = run["deck_id"]
        self.rec = PrepRecord(ctx, run["id"])
        self.owned = False                  # この回の記録に世代を持っているか（直しは最初の素材解決で持つ）
        self.generation: int = 0
        self.preparation_id = ""
        self.dir: Path = Path()
        self.catalog: Catalog | None = None
        self.intake: dict[str, Any] = {}
        self.assets: dict[str, dict[str, Any]] = {}
        self.resumed = False
        self.reused_from: int | None = None
        self.ref_ctx: RefContext | None = None

    # ---- 世代の読み書き ----
    def _set_catalog(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.ref_ctx = RefContext.of(catalog)

    def _load(self, folder: Path, gen: int, pid: str) -> None:
        self.dir, self.generation, self.preparation_id = folder, gen, pid
        self.intake = read_json(folder / INTAKE)
        self._set_catalog(Catalog.from_json(read_json(folder / CATALOG)))
        self.assets = read_json(folder / ASSETS) if (folder / ASSETS).is_file() else {}

    def providers(self) -> dict[str, FolderProvider]:
        out = {}
        for row in self.doc.get("resources") or []:
            res = next((r for r in self.intake["resources"] if r["id"] == row["id"]), None)
            if res is None:
                continue
            path = Path(row["path"]).expanduser()
            out[row["id"]] = FolderProvider(row["id"], path if row["kind"] == "directory" else path.parent, res["items"])
        return out

    # ---- 準備（入口の確認・読解） ----
    def start(self, llm: LLM.Llm | None) -> None:
        cur = self.rec.current()
        adopted = cur is not None and self._adopt_intake(cur)
        if adopted and "reading" in cur["stages"] and verify(self.dir, cur["stages"]["reading"]["files"]):
            self._load(self.dir, self.generation, self.preparation_id)   # 読解まで完了した世代（同じ回の中断の再開）
            self.resumed = True
            return
        if not adopted:
            n = allocate_generation(self.ctx, self.root, self.deck_id)
            pid = self.ctx.call(self.ctx.store.new_id)
            self.generation, self.preparation_id, self.dir, self.owned = n, pid, self.root.preparation_dir(self.deck_id, n), True
            self.rec.start(n, pid)
            self.boundary("intake.begin")
            self.rec.begin(n, "intake")
            self.intake = read_resources(self.doc, reader=self.reader, preparation_md=self.compiled["preparation.md"],
                                         mode=self.mode)
            sha = write_json(self.dir / INTAKE, self.intake)
            folders = [r["id"] for r in self.intake["resources"] if r["form"] == "folder"]
            self.rec.complete(n, "intake", {INTAKE: sha}, input_digest=sha, folder_ids=folders)
            self.boundary("intake.done")
        n = self.generation
        digest = self.rec.generation(n)["input_digest"]
        if not self._reuse_reading(digest):
            self.boundary("reading.begin")
            self.rec.begin(n, "reading")
            catalog, reading = self._interpret(llm)
            files = {CATALOG: write_json(self.dir / CATALOG, catalog.to_json()), READING: write_json(self.dir / READING, reading)}
            self._set_catalog(catalog)
            self.rec.complete(n, "reading", files)
            self.boundary("reading.done")

    def _adopt_intake(self, cur: dict[str, Any]) -> bool:
        """同じ回で中断した世代の、完了した入口の確認を引き継げるか（控えのファイルが記録のハッシュと全部合う）。"""
        n = cur["generation"]
        folder = self.root.preparation_dir(self.deck_id, n)
        st = cur.get("stages") or {}
        if "intake" not in st or not verify(folder, st["intake"]["files"]):
            return False
        self.generation, self.preparation_id, self.dir, self.owned = n, cur["preparation_id"], folder, True
        self.intake = read_json(folder / INTAKE)
        return True

    def _reuse_reading(self, digest: str) -> bool:
        """同じ入力 digest で読解まで完了した世代があれば、その控えを引き継ぐ（LLM を呼ばない）。"""
        for _, g in deck_generations(self.ctx, self.deck_id):
            st = g.get("stages") or {}
            if g.get("generation") == self.generation or g.get("input_digest") != digest or "reading" not in st:
                continue
            src = self.root.preparation_dir(self.deck_id, g["generation"])
            if not (verify(src, st["intake"]["files"]) and verify(src, st["reading"]["files"])):
                continue
            catalog = Catalog.from_json(read_json(src / CATALOG)).derive(preparation_id=self.preparation_id,
                                                                         generation=self.generation)
            files = {CATALOG: write_json(self.dir / CATALOG, catalog.to_json())}
            shutil.copyfile(src / READING, self.dir / READING)
            files[READING] = sha256_file(self.dir / READING) or ""
            self._set_catalog(catalog)
            self.reused_from = g["generation"]
            self.rec.complete(self.generation, "reading", files, reused_from=g["generation"])
            return True
        return False

    def _interpret(self, llm: LLM.Llm | None) -> tuple[Catalog, dict[str, Any]]:
        res = self.intake["resources"]
        items = [CatalogItem(r["id"], i["item_id"], i["description"], i["media_type"], i["size"], i["width"], i["height"])
                 for r in res for i in r["items"]]
        issued = issue_ids(items)
        supported = SUPPORTED_ROLES.get(self.mode, ())
        interp = LLM.Interpretation()
        if res:
            if llm is None:
                raise PreparationStopped("素材を準備する AI を呼べません。", "llm_unavailable")
            interp = LLM.interpret(
                llm, lambda errs: LLM.build_prompt(preparation_md=self.compiled["preparation.md"], structure=self.doc["structure"],
                                                   mode_label=MODE_LABELS.get(self.mode, self.mode), intake=self.intake,
                                                   issued=issued, supported=supported, errors=errs),
                issued_ids={ref for ref, _ in issued}, supported=supported, policy=self.policy)
        by_ref = {ref: it for ref, it in issued}
        refs = [Reference(r["id"], by_ref[r["id"]].resource_id, by_ref[r["id"]].item_id, tuple(r["roles"]), r["description"],
                          by_ref[r["id"]].media_type) for r in sorted(interp.references, key=lambda r: int(r["id"][4:]))]
        catalog = Catalog(preparation_id=self.preparation_id, generation=self.generation, mode=self.mode,
                          issued=[{"id": ref, "resource_id": it.resource_id, "item_id": it.item_id} for ref, it in issued],
                          references=refs, notes=interp.notes, unsupported=interp.unsupported,
                          resource_names={r["id"]: r["name"] for r in res})
        return catalog, {"attempts": interp.attempts, "unsupported": interp.unsupported,
                         "resources": [{"id": r["id"], "name": r["name"], "guide": (r.get("guide") or {}).get("name"),
                                        "guide_sha256": (r.get("guide") or {}).get("sha256"), "items": len(r["items"]),
                                        "excluded": r["excluded"]} for r in res]}

    # ---- 台本係へ ----
    def writer_sections(self) -> dict[str, Any]:
        assert self.catalog is not None
        return {"writer_md": self.compiled["writer.md"], "catalog": self.catalog.writer_view(), "mode": self.mode}

    def check_script(self, data: dict[str, Any], *, fresh: bool = False) -> list[str]:
        """台本全体の参照の検査（`NG:` の行）。`fresh` は台本係の初稿そのもの（サーバの印 `_preparation` を書かせない）。"""
        problems = [f"NG: 一番外の {PREP_KEY} は書かない（サーバが付ける）"] if fresh and PREP_KEY in data else []
        for i, s in enumerate(data.get("slides") or [], 1):
            for key in ("_characters", "_layout"):
                if isinstance(s, dict) and key in s:
                    problems.append(f"NG: {i}枚目（id={s.get('id')}）: {key} は書かない（素材は _references で指定する。"
                                    "エンジンに渡す値はサーバが作る）")
        return problems + validate_script({k: v for k, v in data.items() if not (fresh and k == PREP_KEY)}, self.ref_ctx)

    def stamped(self, data: dict[str, Any]) -> dict[str, Any]:
        assert self.ref_ctx is not None
        return stamp(data, self.ref_ctx)

    # ---- 素材解決・控え ----
    def _own(self) -> None:
        """直しの回: 基にした世代を引き継いだ新しい世代を、この回の記録に作る（既存の世代は書き換えない）。"""
        if self.owned:
            return
        assert self.catalog is not None
        base_dir, base_gen = self.dir, self.generation
        n = allocate_generation(self.ctx, self.root, self.deck_id)
        pid = self.ctx.call(self.ctx.store.new_id)
        folder = self.root.preparation_dir(self.deck_id, n)
        catalog = self.catalog.derive(preparation_id=pid, generation=n)
        self.rec.start(n, pid, parent=base_gen)
        files = {INTAKE: sha256_file(base_dir / INTAKE) or ""}
        shutil.copyfile(base_dir / INTAKE, folder / INTAKE)
        self.rec.complete(n, "intake", files, input_digest=files[INTAKE], reused_from=base_gen)
        rfiles = {CATALOG: write_json(folder / CATALOG, catalog.to_json())}
        shutil.copyfile(base_dir / READING, folder / READING)
        rfiles[READING] = sha256_file(folder / READING) or ""
        self.rec.complete(n, "reading", rfiles, reused_from=base_gen)
        for a in self.assets.values():
            for f in a["files"]:
                link_or_copy(base_dir / f["path"], folder / f["path"])
        self.generation, self.preparation_id, self.dir, self.owned = n, pid, folder, True
        self.reused_from = base_gen

    def resolve(self, script: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """候補の台本の素材解決: 使う参照の素材だけをコピー・ハッシュ化し、ページへの束縛を控える。"""
        assert self.catalog is not None
        sha = script["sha256"]
        self._own()
        n = self.generation
        self.rec.complete(n, "script", {}, script_id=script["id"], script_sha256=sha)
        self.boundary("resolve.begin")
        self.rec.begin(n, "resolve")
        refs = page_references(data)
        ids = [e["id"] for page in refs.values() for e in page]
        self.assets = resolve_assets(ids, catalog=self.catalog, providers=self.providers(), reader=self.reader, gen_dir=self.dir,
                                     existing=self.assets)
        used = {k: v for k, v in self.assets.items() if k in set(ids)}
        files = {ASSETS: write_json(self.dir / ASSETS, self.assets),
                 BINDINGS: write_json(self.dir / BINDINGS, {
                     "preparation_id": self.preparation_id, "generation": n, "script_sha256": sha,
                     "refs": {k: {"files": [{kk: f[kk] for kk in ("file_id", "path", "sha256", "media_type")} for f in v["files"]]}
                              for k, v in sorted(used.items())},
                     "bindings": [{"page_id": pid, "ref_id": e["id"], "role": e["role"], "slot": e.get("slot"),
                                   "name": e.get("name"), "pose": e.get("pose"), "reason": e["reason"]}
                                  for pid, page in refs.items() for e in page]})}
        self.rec.complete(n, "resolve", files, script_sha256=sha)
        self.boundary("resolve.done")
        return files

    def convert(self, data: dict[str, Any]) -> dict[str, Any]:
        """エンジンに渡す台本（`_references` を除き、取得済みの参照を変換）。検査・描画・直しが同じ結果を使う。"""
        return materialize_engine_input(data, mode=self.mode, gen_dir=self.dir if self.assets else None, assets=self.assets)

    def finish(self, script: dict[str, Any]) -> None:
        """エンジンの検査を通った台本を、準備の控え（manifest）にして世代を完了にする。"""
        assert self.catalog is not None
        n = self.generation
        self._own()
        n = self.generation
        self.boundary("manifest.begin")
        self.rec.begin(n, "manifest")
        g = self.rec.generation(n) or {}
        manifest = {"schema_version": MANIFEST_SCHEMA, "preparation_id": self.preparation_id, "generation": n,
                    "parent": g.get("parent"), "mode": self.mode,
                    "snapshot_sha256": self.run["args"]["template_snapshot"]["snapshot_sha256"],
                    "input_digest": g.get("input_digest"), "catalog_sha256": self.catalog.content_sha256(),
                    "script": {"id": script["id"], "sha256": script["sha256"]},
                    "files": {k: sha256_file(self.dir / k) for k in (INTAKE, CATALOG, READING, ASSETS, BINDINGS)
                              if (self.dir / k).is_file()},
                    "assets": [{"ref_id": r, "path": f["path"], "sha256": f["sha256"]}
                               for r, a in sorted(self.assets.items()) for f in a["files"]],
                    "render_ready": False,
                    "compliance": {"status": "not_evaluated", "note": "指示の遵守は描画後に確かめる（この段では判定しない）"}}
        sha = write_json(self.dir / MANIFEST, manifest)
        self.rec.complete(n, "manifest", {MANIFEST: sha}, script_sha256=script["sha256"])
        self.rec.finish(n)
        self.boundary("manifest.done")

    def completed(self, store_script: Callable[[str], dict[str, Any]]) -> dict[str, Any] | None:
        """再開: 控えのハッシュが全部合う完了済みの段から、検査を通した台本の行を返す（manifest まで完了。台本係を呼ばない）。"""
        g = self.rec.generation(self.generation) or {}
        st = g.get("stages") or {}
        if not all(s in st for s in ("script", "resolve", "manifest")):
            return None
        if not (verify(self.dir, st["resolve"]["files"]) and verify(self.dir, st["manifest"]["files"])):
            return None
        if not verify_assets(self.dir, self.assets):
            return None
        row = store_script(st["script"]["script_id"])
        return row if row["sha256"] == st["script"]["script_sha256"] == st["manifest"]["script_sha256"] else None

    def pending_candidate(self, store_script: Callable[[str], dict[str, Any]]) -> dict[str, Any] | None:
        """再開: 素材解決まで完了した候補の台本（検査だけやり直す）。"""
        g = self.rec.generation(self.generation) or {}
        st = g.get("stages") or {}
        if not all(s in st for s in ("script", "resolve")) or "manifest" in st:
            return None
        if not verify(self.dir, st["resolve"]["files"]) or not verify_assets(self.dir, self.assets):
            return None
        row = store_script(st["script"]["script_id"])
        return row if row["sha256"] == st["resolve"]["script_sha256"] else None

    def stop(self, exc: PreparationStopped) -> None:
        if self.owned and self.generation:
            self.rec.fail(self.generation, exc.code, exc.message)


def prepare_run(ctx: Any, run: dict[str, Any], *, llm: LLM.Llm | None, policy: Policy = DEFAULT_POLICY,
                reader: Reader | None = None, boundary: Callable[[str], None] | None = None) -> TemplateRun:
    """テンプレートの回の入口の確認と読解（準備係 LLM）。止めるときは PreparationStopped（世代の記録に理由を残す）。"""
    snapshot = ctx.call(ctx.store.template_snapshot, run["args"]["template_snapshot"])
    tr = TemplateRun(ctx, run, doc=snapshot["template"]["document"], compiled=load_compiled(ctx, run),
                     reader=reader or Reader(policy), boundary=boundary)
    try:
        tr.start(llm)
    except PreparationStopped as exc:
        tr.stop(exc)
        raise
    return tr


def load_template_run(ctx: Any, run: dict[str, Any], *, reader: Reader | None = None, boundary: Callable[[str], None] | None = None
                      ) -> TemplateRun:
    """直し・差し込み用: デッキの最後に完了した世代（検査を通した台本の控え）を基にした `TemplateRun`。LLM・素材の再読取はしない。"""
    snapshot = ctx.call(ctx.store.template_snapshot, run["args"]["template_snapshot"])
    tr = TemplateRun(ctx, run, doc=snapshot["template"]["document"], compiled=load_compiled(ctx, run),
                     reader=reader or Reader(), boundary=boundary)
    for _, g in deck_generations(ctx, run["deck_id"]):
        st = g.get("stages") or {}
        if g.get("state") != "completed" or not all(s in st for s in ("intake", "reading", "manifest")):
            continue
        folder = ctx.root.preparation_dir(run["deck_id"], g["generation"])
        if verify(folder, st["intake"]["files"]) and verify(folder, st["reading"]["files"]) \
                and verify(folder, st["manifest"]["files"]):
            tr._load(folder, g["generation"], g["preparation_id"])
            if not verify_assets(folder, tr.assets):
                raise PreparationStopped("選んだ素材のコピーが、控えと違っています（欠けたか変わりました）。", "assets_changed")
            return tr
    raise PreparationStopped("この資料の素材準備の控えが見つかりません。新しいスライドとして頼み直してください。", "preparation_missing")
