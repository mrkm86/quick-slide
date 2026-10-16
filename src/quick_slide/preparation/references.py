"""台本の `_references`（§3.4）。台本係が書くのは参照 ID と役割・理由・（ピッチ以外の）枠・名前・ポーズだけで、
パスや `_characters` は書かない（`_characters` はサーバがエンジンへ渡す直前に作る）。

ページの `_references: [{id, role, slot?, name?, pose?, reason}]`。検査は ID の存在・候補か・準備世代・許可された役割・
ページ／枠・name と pose の型・重複を見る。省略（元を引き継ぐ）・空配列（明示的な削除）・分割（全ページに明示）の区別は
`writer/patch.py` が持つ。台本の一番外の `_preparation`（準備 ID・世代・カタログのハッシュ）はサーバが付ける。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .catalog import ROLES, SUPPORTED_ROLES, Catalog

REF_KEY = "_references"
PREP_KEY = "_preparation"
MAX_PAGE_REFS = 5       # 1 ページの画像生成に添えられる参照画像の上限（エンジンと同じ。§4.2）
MAX_TEXT = 100
MAX_REASON = 300
ENTRY_KEYS = {"id", "role", "slot", "name", "pose", "reason"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_REF_WORD = re.compile(r"\bref-\d+\b")
_SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class RefInfo:
    id: str
    roles: tuple[str, ...]
    resource: str


@dataclass(frozen=True)
class RefContext:
    """台本係の答えを確かめるときの、準備カタログの範囲。"""
    preparation_id: str
    generation: int
    catalog_sha256: str
    mode: str
    refs: Mapping[str, RefInfo]
    issued: frozenset[str]

    @classmethod
    def of(cls, catalog: Catalog) -> "RefContext":
        refs = {r.id: RefInfo(r.id, r.roles, catalog.resource_names.get(r.resource_id, r.resource_id))
                for r in catalog.references}
        return cls(catalog.preparation_id, catalog.generation, catalog.content_sha256(), catalog.mode, refs,
                   frozenset(catalog.issued_ids))

    @property
    def supported_roles(self) -> tuple[str, ...]:
        return SUPPORTED_ROLES.get(self.mode, ())


def _text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum and not _CONTROL.search(value)


def check_references(page: Any, where: str, ctx: RefContext) -> list[str]:
    """1 ページの `_references` の検査（無ければ何も言わない。省略の扱いは呼ぶ側）。`NG:` の行を返す。"""
    if not isinstance(page, dict) or REF_KEY not in page:
        return []
    refs = page[REF_KEY]
    if not isinstance(refs, list):
        return [f"NG: {where}: {REF_KEY} は配列で書く（使わないなら []）"]
    problems: list[str] = []
    if len(refs) > MAX_PAGE_REFS:
        problems.append(f"NG: {where}: {REF_KEY} は 1 ページに {MAX_PAGE_REFS} 件まで（参照画像の上限）")
    seen: set[str] = set()
    for i, e in enumerate(refs, 1):
        w = f"{where} の {REF_KEY} の {i} 番目"
        if not isinstance(e, dict):
            problems.append(f"NG: {w}はオブジェクトで書く")
            continue
        extra = sorted(set(e) - ENTRY_KEYS)
        if extra:
            problems.append(f"NG: {w}に知らないキー: {', '.join(extra)}（使えるのは id・role・slot・name・pose・reason。"
                            "パス・image は書かない）")
        rid, role = e.get("id"), e.get("role")
        info = ctx.refs.get(rid) if isinstance(rid, str) else None
        if info is None:
            if isinstance(rid, str) and rid in ctx.issued:
                problems.append(f"NG: {w}の id {rid} は、準備係が候補にしなかった素材（使えない）")
            else:
                problems.append(f"NG: {w}の id {rid!r} は準備カタログにない（カタログの id だけを使う）")
        elif rid in seen:
            problems.append(f"NG: {w}の id {rid} が同じページに重複している")
        else:
            seen.add(rid)
        if role not in ROLES:
            problems.append(f"NG: {w}の role は {'・'.join(ROLES)} のどれか")
        elif info is not None and role not in info.roles:
            problems.append(f"NG: {w}の id {rid} は役割 {role} では使えない（許された役割: {'・'.join(info.roles)}）")
        elif role not in ctx.supported_roles:
            problems.append(f"NG: {w}の役割 {role} は、このモードではまだ使えない")
        if not _text(e.get("reason"), MAX_REASON):
            problems.append(f"NG: {w}の reason（選んだ理由）を 1〜{MAX_REASON} 字の文で書く")
        for key in ("name", "pose"):
            if key in e and (not _text(e[key], MAX_TEXT) or role != "character"):
                problems.append(f"NG: {w}の {key} は character の役割にだけ、1〜{MAX_TEXT} 字の文で書く")
        if role == "character" and not _text(e.get("name"), MAX_TEXT):
            problems.append(f"NG: {w}: character には name（誰か）が要る")
        if "slot" in e:
            slot = e["slot"]
            if ctx.mode == "pitch":
                problems.append(f"NG: {w}の slot は書かない（ピッチのページには画像の枠が無い）")
            elif not isinstance(slot, str) or not _SLOT_RE.match(slot) or not isinstance(page.get(slot), dict):
                problems.append(f"NG: {w}の slot は、このページにある画像の枠の名前にする")
    if sum(1 for e in refs if isinstance(e, dict) and e.get("role") == "image") > 1:
        problems.append(f"NG: {where}: role が image の素材は 1 ページに 1 つまで（画像の枠は 1 つ）")
    text = json.dumps({k: v for k, v in page.items() if k != REF_KEY}, ensure_ascii=False)
    if _REF_WORD.search(text):
        problems.append(f"NG: {where}: 参照 ID（ref-N）をページの文字に書かない")
    return problems


def validate_script(data: dict[str, Any], ctx: RefContext | None) -> list[str]:
    """台本全体の `_references` と `_preparation` の検査（直さなかったページの継承も含めて、台本ごと）。"""
    if ctx is None:
        return [f"NG: ページ {s.get('id')} に {REF_KEY} がある（テンプレートを使わない資料には書かない）"
                for s in data.get("slides") or [] if isinstance(s, dict) and REF_KEY in s]
    problems: list[str] = []
    stamp = data.get(PREP_KEY)
    used = False
    for i, s in enumerate(data.get("slides") or [], 1):
        if not isinstance(s, dict):
            continue
        if REF_KEY in s:
            used = True
        problems += check_references(s, f"{i}枚目（id={s.get('id')}）", ctx)
    if used and isinstance(stamp, dict) and stamp.get("catalog_sha256") != ctx.catalog_sha256:
        problems.append("NG: 台本の素材参照は、いまの準備カタログとは別の世代のもの（世代違い）。カタログの id で書き直す")
    return problems


def stamp(data: dict[str, Any], ctx: RefContext) -> dict[str, Any]:
    """台本に準備の印（一番外の `_preparation`）を付ける。参照を 1 つも使わない台本には付けない。"""
    have = data.get(PREP_KEY)
    if isinstance(have, dict) and have.get("catalog_sha256") == ctx.catalog_sha256 and any(
            isinstance(s, dict) and s.get(REF_KEY) for s in data.get("slides") or []):
        return data   # 同じカタログの印がもうある（引き継いだ世代の印は書き換えない）
    out = {k: v for k, v in data.items() if k != PREP_KEY}
    if any(isinstance(s, dict) and s.get(REF_KEY) for s in data.get("slides") or []):
        out[PREP_KEY] = {"preparation_id": ctx.preparation_id, "generation": ctx.generation,
                         "catalog_sha256": ctx.catalog_sha256}
    return out


def page_references(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """ページの id → `_references`（空でないものだけ）。"""
    return {str(s.get("id")): copy.deepcopy(s[REF_KEY]) for s in data.get("slides") or []
            if isinstance(s, dict) and s.get(REF_KEY)}
