"""Demo web (Phase 13, spec mục 20): `streamlit run src/steel_xai/demo/app.py`.

Hiển thị từ kết quả ĐÃ chạy (không gọi API): bất thường → giả thuyết + trạng thái → bằng chứng có
trích dẫn → phần còn thiếu → giải thích cuối, kèm so sánh A/B/C. Dòng "hỗ trợ phân tích, không phải
tư vấn đầu tư" ở đầu và cuối trang. Demo không phải mục tiêu nghiên cứu chính.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

import streamlit as st

from steel_xai.demo.text import CONFIG_VI, DIR_VI, TYPE_VI
from steel_xai.demo.view import (
    STATUS_ORDER,
    STATUS_VI,
    CheckView,
    DemoInputs,
    default_inputs,
    list_cases,
    load_showcase,
    report_label,
    run_hint,
    status_counts,
)

STATUS_COLOR = {
    "SUPPORTED": "green",
    "PARTIAL": "orange",
    "UNVERIFIED": "gray",
    "CONTRADICTED": "red",
}
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|$<>~])")


def md_escape(text: str) -> str:
    """Trích dẫn hiển thị NGUYÊN VĂN: thoát ký tự Markdown để không bị đổi thành định dạng."""
    return _MD_SPECIAL.sub(r"\\\1", text)


@st.cache_data(show_spinner=False)
def _inputs(output_dir: str | None) -> DemoInputs:
    inp = default_inputs()
    if output_dir:  # cho test/trình diễn trỏ sang thư mục kết quả khác
        inp = replace(inp, output_dir=Path(output_dir))
    return inp


def _badge(status: str) -> str:
    return f":{STATUS_COLOR.get(status, 'gray')}[**{status}**] · {STATUS_VI.get(status, '')}"


def _checks(title: str, checks: tuple[CheckView, ...]) -> None:
    if not checks:
        return
    st.markdown(f"**{title}**")
    for c in checks:
        vals = ", ".join(f"`{k}` = {md_escape(v)}" for k, v in c.values.items())
        st.markdown(f"- `{c.check_id}` ({c.importance}, {c.kind}){': ' + vals if vals else ''}")
        if c.detail and c.detail not in ("FOUND", "UNVERIFIED"):
            st.caption(md_escape(c.detail[:300]))
        for q in c.quotes:
            page = f", trang {q.page}" if q.page is not None else ""
            st.markdown(f"> “{md_escape(q.text)}”  \n> — *{md_escape(q.doc_id)}{page}*")


def main() -> None:
    st.set_page_config(page_title="Giải thích bất thường BCTC thép", layout="wide")
    inp = _inputs(os.environ.get("STEEL_XAI_DEMO_OUTPUT_DIR"))
    st.warning(inp.disclaimer)

    split = st.sidebar.radio("Tập", ("dev", "test"), horizontal=True)
    cases = dict(list_cases(inp.output_dir, split))
    if not cases:
        st.info(f"Chưa có kết quả cấu hình C cho tập {split}. {run_hint(split)}")
        st.warning(inp.disclaimer)
        st.stop()
    show_split, showcase = load_showcase()
    roles = {x.case_id: x for x in showcase} if split == show_split else {}
    # ca trình diễn lên đầu (theo thứ tự nhóm chọn), rồi các ca còn lại
    order = [c for c in roles if c in cases] + [c for c in cases if c not in roles]
    case_id = st.sidebar.selectbox(
        "Ca",
        order,
        format_func=lambda c: f"★ {c} ({roles[c].role})" if c in roles else c,
    )
    run_id = st.sidebar.selectbox("Lần chạy", cases[case_id])
    top = st.sidebar.slider("Số hạng hiển thị chi tiết", 1, 10, 3)
    show_ref = st.sidebar.checkbox("Hiện đáp án tham chiếu (nếu đã có đáp án cuối)", value=False)

    v = inp.case_view(split, case_id, run_id)
    for w in v.warnings:
        st.error(f"Cảnh báo dữ liệu: {w}")
    if case_id in roles:
        st.info(f"Ca trình diễn: {roles[case_id].role}. {roles[case_id].note}")

    # 1. Bất thường
    t = TYPE_VI.get(v.anomaly_type, v.anomaly_type)
    st.title(f"{t} {DIR_VI.get(v.direction, v.direction)} — {v.company} {v.period}")
    st.caption(
        f"Ca {v.case_id} · {split} · {run_id} · mốc as-of {v.asof_date or 'không rõ'} · "
        f"LLM {v.model} · chỉ báo do code tính, không có LLM"
    )
    if v.indicators:
        cols = st.columns(len(v.indicators))
        for col, ind in zip(cols, v.indicators, strict=True):
            col.metric(ind.label, ind.display, help=ind.name)
    if v.rules_hit:
        st.markdown("Luật vượt ngưỡng: " + ", ".join(f"`{r}`" for r in v.rules_hit))
    if v.flags:
        st.caption("Cờ dữ liệu: " + ", ".join(f"`{f}`" for f in v.flags))

    # 2. Giả thuyết + trạng thái
    st.header("Giả thuyết và trạng thái kiểm chứng")
    st.caption(
        "Giả thuyết lấy từ thư viện do nhóm soạn; trạng thái do luật trong code quyết định "
        "(LLM chỉ phân loại đoạn văn bản và viết lại)."
    )
    counts = status_counts(v)
    for col, s in zip(st.columns(len(STATUS_ORDER)), STATUS_ORDER, strict=True):
        col.metric(STATUS_VI[s], counts[s])
    st.dataframe(
        [
            {
                "Hạng": h.rank,
                "Mã": h.code,
                "Giả thuyết": h.claim,
                "Trạng thái": h.status,
                "Check critical đạt": h.critical,
            }
            for h in v.hypotheses
        ],
        hide_index=True,
        width="stretch",
    )

    # 3. Bằng chứng, trích dẫn, phần thiếu (giả thuyết bị phản bác luôn hiện phần phản bác)
    st.header("Bằng chứng, trích dẫn và phần còn thiếu")
    for h in v.hypotheses:
        detailed = h.rank <= top
        if not detailed and not h.contradictions:
            continue
        with st.expander(f"#{h.rank} {h.code} {h.claim}", expanded=h.rank == 1):
            st.markdown(_badge(h.status) + f" · check critical đạt {h.critical}")
            if detailed:
                _checks("Bằng chứng ủng hộ", h.evidence)
            _checks("Bằng chứng phản bác", h.contradictions)
            if detailed:
                _checks("Kỳ vọng không xảy ra", h.failed)
                _checks("Còn thiếu / chưa kiểm được", h.missing)
    if v.series:
        st.subheader("Chuỗi giá đầu vào mà các check đã dùng")
        for name, pts in v.series.items():
            unit = inp.series_units.get(name, "")
            st.caption(f"{name} ({unit})" if unit else name)
            st.line_chart({"giá trị": {p: val for p, val in pts}})

    # 4. Giải thích cuối
    st.header("Giải thích cuối")
    st.caption(report_label(v.report_status))
    st.markdown(v.report_markdown or "_Không có báo cáo._")

    # 5. So sánh A / B / C
    if v.compare:
        st.header("So sánh cấu hình (cùng ca, cùng lần chạy)")
        st.caption("Số liệu bản nháp đầu của mỗi cấu hình (C còn được bộ kiểm cho sinh lại).")
        for col, b in zip(st.columns(len(v.compare)), v.compare, strict=True):
            with col:
                st.subheader(CONFIG_VI.get(b.config, b.config))
                for i, x in enumerate(b.hypotheses, 1):
                    st.markdown(f"{i}. {x}")
                st.caption(
                    f"{b.claims} khẳng định · không nguồn: {b.unsupported} · lệch số: "
                    f"{b.numeric_mismatches} · trích dẫn qua kiểm nguyên văn: "
                    f"{b.citations_ok}/{b.citations_total}"
                )

    if show_ref:
        st.header("Đáp án tham chiếu")
        st.write(", ".join(v.reference) if v.reference else "Chưa có đáp án cuối cho ca này.")

    st.warning(inp.disclaimer)


main()
