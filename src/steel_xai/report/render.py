"""Dựng báo cáo Markdown 8 mục (spec mục 13). Khung, bảng trạng thái, phần thiếu, phản bác và danh
sách nguồn do CODE dựng từ dữ liệu đã kiểm; LLM chỉ góp các khẳng định đã qua bộ kiểm."""

from __future__ import annotations

from steel_xai.report.checks import Claim
from steel_xai.report.facts import EvidenceItem, MissingItem, NumberFact, ReportInput, VerdictItem
from steel_xai.report.settings import ReportSettings

_TITLES = {
    "anomaly": "1. Bất thường",
    "quantitative": "2. Quan sát định lượng",
    "top_hypotheses": "3. Giả thuyết hàng đầu",
    "evidence": "4. Bằng chứng từng giả thuyết",
    "contradictions": "5. Bằng chứng phản bác",
    "missing": "6. Dữ liệu/bằng chứng còn thiếu",
    "conclusion": "7. Kết luận có điều kiện",
}


_CHECK_ROLES = {
    "support": "đạt, ủng hộ",
    "failed": "không đạt",
    "contradict": "phản bác",
    "input": "đầu vào",
}


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _claim_line(c: Claim) -> str:
    return f"- {c.text} [{', '.join(c.sources)}]"


def _hyp_name(v: VerdictItem) -> str:
    return f"{v.code} ({v.hypothesis_id})" if v.code else v.hypothesis_id


def _source_line(item: NumberFact | EvidenceItem | MissingItem | VerdictItem) -> str:
    if isinstance(item, NumberFact):
        return f"- [{item.id}] {item.label} = {item.display}. Nguồn: {item.source}"
    if isinstance(item, EvidenceItem):
        p = item.provenance
        chunk = f", {p.chunk_id}" if p.chunk_id else ""
        return f"- [{item.id}] {p.doc_id}, trang {p.page}{chunk} ({item.polarity}): “{item.quote}”"
    if isinstance(item, MissingItem):
        return f"- [{item.id}] {item.hypothesis_id}/{item.check_id}: {item.reason}"
    return (
        f"- [{item.id}] {_hyp_name(item)}: {item.status}, hạng {item.rank}, "
        f"critical {item.critical_passed}/{item.critical_total} (luật kiểm chứng trong code)"
    )


def render_report(
    inp: ReportInput,
    claims: tuple[Claim, ...],
    settings: ReportSettings,
    note: str | None = None,
) -> str:
    a = inp.anomaly
    by_section: dict[str, list[Claim]] = {k: [] for k in _TITLES}
    for c in claims:
        by_section.setdefault(c.section, []).append(c)
    shown: set[str] = {x for c in claims for x in c.sources}
    out = [f"# Báo cáo giải thích bất thường: {a.company} {a.period}", ""]
    out.append(f"> {settings.disclaimer}")
    if inp.used_drafts:
        out.append(f"> **{settings.draft_warning}**")
    if note:
        out.append(f"> {note}")

    # 1
    out += ["", f"## {_TITLES['anomaly']}", ""]
    out.append(
        f"- Loại: `{a.type}`, hướng `{a.direction}`; mốc as-of: {a.asof_date or 'chưa có'}; "
        f"cơ sở so sánh: {', '.join(a.baseline_used) or 'không'}"
        + (f"; cờ: {', '.join(f'`{f}`' for f in a.flags)}" if a.flags else "")
    )
    out += [_claim_line(c) for c in by_section["anomaly"]]

    # 2
    out += ["", f"## {_TITLES['quantitative']}", "", "| Mã | Chỉ báo | Giá trị |", "|---|---|---|"]
    for n in inp.numbers:
        if n.role == "indicator":
            out.append(f"| {n.id} | {_cell(n.label)} | {n.display} |")
            shown.add(n.id)
    out += ["", *(_claim_line(c) for c in by_section["quantitative"])]

    # 3
    out += [
        "",
        f"## {_TITLES['top_hypotheses']}",
        "",
        "| Hạng | Mã | Giả thuyết | Trạng thái | Critical đạt | Có bằng chứng văn bản |",
        "|---|---|---|---|---|---|",
    ]
    for v in inp.verdicts:
        out.append(
            f"| {v.rank} | {v.id} | {_cell(_hyp_name(v))}: {_cell(v.claim)} | **{v.status}** | "
            f"{v.critical_passed}/{v.critical_total} | {'có' if v.has_verified_text else 'không'} |"
        )
        shown.add(v.id)
    if inp.others:
        rest = ", ".join(f"{_hyp_name(v)} ({v.status}, hạng {v.rank})" for v in inp.others)
        out += ["", f"Các giả thuyết khác: {rest}."]
    out += ["", *(_claim_line(c) for c in by_section["top_hypotheses"])]

    # 4
    out += ["", f"## {_TITLES['evidence']}"]
    general = [c for c in by_section["evidence"] if not c.hypothesis_id]
    for v in inp.verdicts:
        mine = [c for c in by_section["evidence"] if c.hypothesis_id == v.hypothesis_id]
        out += ["", f"### {_hyp_name(v)} — {v.status}", ""]
        quant = [
            n for n in inp.numbers if n.hypothesis_id == v.hypothesis_id and n.role in _CHECK_ROLES
        ]
        if quant:
            out.append("Kết quả check định lượng (do code tính):")
            for n in quant:
                out.append(
                    f"- [{n.id}] `{n.check_id}` ({_CHECK_ROLES[n.role]}): "
                    f"{n.label.split(': ')[-1]} = {n.display}"
                )
                shown.add(n.id)
            out.append("")
        out += [_claim_line(c) for c in mine] or ["- (không có khẳng định diễn giải nào)"]
    if general:
        out += ["", *(_claim_line(c) for c in general)]

    # 5
    out += ["", f"## {_TITLES['contradictions']}", ""]
    contra = [n for n in inp.numbers if n.role == "contradict"]
    contra_e = [e for e in inp.evidence if e.polarity == "contradict"]
    for n in contra:
        out.append(
            f"- [{n.id}] {n.hypothesis_id}/{n.check_id}: {n.label.split(': ')[-1]} = {n.display}"
        )
        shown.add(n.id)
    for e in contra_e:
        out.append(f"- [{e.id}] {e.hypothesis_id}/{e.check_id}: “{e.quote}”")
        shown.add(e.id)
    if not contra and not contra_e:
        out.append("- Không ghi nhận bằng chứng phản bác nào.")
    out += [_claim_line(c) for c in by_section["contradictions"]]

    # 6
    out += ["", f"## {_TITLES['missing']}", ""]
    for m in inp.missing:
        out.append(f"- [{m.id}] {m.hypothesis_id}/{m.check_id} ({m.importance}): {m.reason}")
        shown.add(m.id)
    if not inp.missing:
        out.append("- Không có check nào thiếu dữ liệu.")
    out += [_claim_line(c) for c in by_section["missing"]]

    # 7
    out += ["", f"## {_TITLES['conclusion']}", ""]
    out += [_claim_line(c) for c in by_section["conclusion"]] or [
        "- Không có kết luận diễn giải được chấp nhận; xem trạng thái do code xác định ở mục 3."
    ]

    # 8
    out += ["", "## 8. Nguồn", ""]
    for item_id, item in inp.by_id.items():
        if item_id in shown or isinstance(item, EvidenceItem):
            out.append(_source_line(item))
    return "\n".join(out) + "\n"
