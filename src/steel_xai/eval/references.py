"""Nạp danh sách ca kèm đáp án tham chiếu (dùng chung cho eval_tools.py và run_eval.py).

Đáp án CUỐI = hai người gán độc lập + người thứ ba giải quyết bất đồng (spec 15.2). Đáp án TẠM =
nhãn của MỘT người gán, chỉ để chỉnh hệ thống trên tập dev khi người thứ hai chưa gán xong; mọi kết
quả chấm bằng đáp án tạm bị ghi rõ và không bao giờ dùng cho tập test.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from steel_xai.contracts import CaseRecord
from steel_xai.eval.cases import load_case_context, load_cases, read_yaml
from steel_xai.eval.labels import (
    disagreements,
    final_references,
    load_label_sets,
    load_rag_index,
    person_key,
    reference_agreement,
    reference_kappa,
    status_kappa,
    taxonomy_from_library,
    validate_adjudication,
)
from steel_xai.eval.settings import EvalSettings, load_eval_settings
from steel_xai.hypotheses import load_library
from steel_xai.hypotheses.library import load_library_settings


@dataclass
class References:
    settings: EvalSettings
    cases: tuple[CaseRecord, ...]  # có đáp án cuối khi `final` là True, ngược lại chưa có
    final: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)  # kappa, đồng thuận, bất đồng
    label_sets: list[Any] = field(default_factory=list)


def load_references(strict_counts: bool = False) -> References:
    s = load_eval_settings()
    cc = load_cases(s, load_case_context(), strict_counts=strict_counts)
    refs = References(s, cc.cases, False, list(cc.errors), list(cc.warnings))
    if not cc.cases:
        refs.errors = refs.errors or ["không có ca hợp lệ"]
        return refs
    taxonomy = taxonomy_from_library(load_library().reviewed)
    lib_s = load_library_settings()
    sets, errs, warns = load_label_sets(cc.cases, taxonomy, s, lib_s, load_rag_index())
    refs.errors += errs
    refs.warnings += warns
    refs.label_sets = sets
    if len(sets) != 2:
        return refs
    a, b = sets
    refs.info["kappa_reference"] = reference_kappa(a, b, cc.cases, taxonomy, s.other_id).to_dict()
    refs.info["agreement"] = reference_agreement(a, b)
    refs.info["kappa_status"] = status_kappa(a, b).to_dict()
    refs.info["annotators"] = [a.annotator, b.annotator]
    dis = disagreements(a, b)
    refs.info["disagreements"] = {c: list(w) for c, w in dis.items()}
    raw = read_yaml(s.adjudication_path) if s.adjudication_path.is_file() else None
    _, decisions, adj_errs = validate_adjudication(
        raw, cc.cases, [a.annotator, b.annotator], taxonomy, s, lib_s
    )
    refs.errors += adj_errs
    final, ferrs = final_references(cc.cases, a, b, decisions)
    refs.errors += ferrs
    refs.info["final_cases"] = len(final)
    if not ferrs:
        refs.cases, refs.final = final, True
    return refs


def provisional_cases(refs: References, annotator: str) -> tuple[CaseRecord, ...]:
    """Ca dev kèm nhãn của MỘT người (đáp án tạm). Ca test bị bỏ: tập test chỉ chấm bằng đáp án
    cuối. Ca người đó chưa gán: không có đáp án (bị bỏ khi chấm)."""
    match = [ls for ls in refs.label_sets if person_key(ls.annotator) == person_key(annotator)]
    if not match:
        names = [ls.annotator for ls in refs.label_sets]
        raise ValueError(f"không có file nhãn hợp lệ của {annotator!r} (có: {names})")
    labels = match[0].labels
    out = []
    for c in refs.cases:
        if c.split != "dev":
            continue
        lab = labels.get(c.case_id)
        out.append(
            c
            if lab is None
            else replace(
                c,
                reference_set=tuple(sorted(lab.reference_set)),
                reference_sources=tuple(lab.sources),
                reference_statuses=dict(lab.statuses),
            )
        )
    return tuple(out)
