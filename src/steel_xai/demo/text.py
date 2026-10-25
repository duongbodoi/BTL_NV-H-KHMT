"""Hiển thị một ca dạng văn bản cho terminal (demo CLI, chạy được không cần Streamlit)."""

from __future__ import annotations

from steel_xai.demo.view import STATUS_VI, CaseView, CheckView, report_label, status_counts

TYPE_VI = {"inventory_increase": "Hàng tồn kho", "gross_margin_change": "Biên lợi nhuận gộp"}
DIR_VI = {"up": "tăng", "down": "giảm"}
CONFIG_VI = {"A": "A · LLM only", "B": "B · LLM + RAG", "C": "C · Full System"}


def _check_lines(c: CheckView, indent: str = "      ") -> list[str]:
    head = f"{indent}- [{c.importance}/{c.kind}] {c.check_id}"
    vals = ", ".join(f"{k}={v}" for k, v in c.values.items())
    if vals:
        head += f": {vals}"
    lines = [head]
    if c.detail and c.detail not in ("FOUND", "UNVERIFIED"):
        lines.append(f"{indent}  {c.detail[:160]}")
    for q in c.quotes:
        page = f", trang {q.page}" if q.page is not None else ""
        lines.append(f"{indent}  “{q.text}” ({q.doc_id}{page})")
    return lines


def render_text(view: CaseView, disclaimer: str, top: int = 3) -> str:
    bar = "=" * 78
    t = TYPE_VI.get(view.anomaly_type, view.anomaly_type)
    lines = [
        bar,
        f"  {disclaimer}",
        bar,
        f"Ca {view.case_id} ({view.split}, {view.run_id}) · model {view.model}",
    ]
    lines += [f"CẢNH BÁO DỮ LIỆU: {w}" for w in view.warnings]
    lines.append(
        f"BẤT THƯỜNG: {t} {DIR_VI.get(view.direction, view.direction)} — {view.company} "
        f"{view.period} (mốc as-of {view.asof_date or 'không rõ'}; chỉ báo do code tính)"
    )
    for ind in view.indicators:
        lines.append(f"  · {ind.label}: {ind.display}")
    if view.rules_hit:
        lines.append(f"  Luật vượt ngưỡng: {', '.join(view.rules_hit)}")
    if view.flags:
        lines.append(f"  Cờ dữ liệu: {', '.join(view.flags)}")
    counts = status_counts(view)
    lines += [
        "",
        "GIẢ THUYẾT (thư viện, trạng thái do luật trong code quyết định): "
        + ", ".join(f"{STATUS_VI[s]} {n}" for s, n in counts.items()),
    ]
    for h in view.hypotheses:
        detailed = h.rank <= top
        mark = "▶ " if detailed else "  "
        lines.append(
            f"{mark}#{h.rank} [{h.status}] {h.code} {h.claim} (check critical đạt {h.critical})"
        )
        if detailed:
            groups = (
                ("Bằng chứng ủng hộ", h.evidence),
                ("Bằng chứng phản bác", h.contradictions),
                ("Kỳ vọng không xảy ra", h.failed),
                ("Còn thiếu / chưa kiểm được", h.missing),
            )
        else:  # ngoài top-k vẫn LUÔN hiện phản bác (spec 12.1: hiển thị cả hai)
            groups = (("Bằng chứng phản bác", h.contradictions),)
        for title, group in groups:
            if group:
                lines.append(f"    {title}:")
                for c in group:
                    lines += _check_lines(c)
    lines += ["", "GIẢI THÍCH CUỐI", f"({report_label(view.report_status)})"]
    lines.append(view.report_markdown.strip() or "(không có báo cáo)")
    if view.compare:
        lines += ["", "SO SÁNH CẤU HÌNH (cùng ca, cùng lần chạy; số liệu bản nháp đầu)"]
        for b in view.compare:
            lines.append(
                f"  {CONFIG_VI.get(b.config, b.config)}: {b.claims} khẳng định, "
                f"không nguồn {b.unsupported}, lệch số {b.numeric_mismatches}, "
                f"trích dẫn qua kiểm nguyên văn {b.citations_ok}/{b.citations_total}"
            )
            for i, x in enumerate(b.hypotheses, 1):
                lines.append(f"    {i}. {x}")
    lines += [bar, f"  {disclaimer}", bar]
    return "\n".join(lines)
