"""Phase 13 — demo: hiển thị bất thường, giả thuyết, trạng thái, bằng chứng, trích dẫn, phần còn
thiếu và giải thích cuối từ kết quả đã chạy (không gọi API). Streamlit: `demo/app.py`; CLI:
`scripts/demo.py`."""

from steel_xai.demo.view import (
    CaseView,
    DemoInputs,
    build_case_view,
    default_inputs,
    list_cases,
    read_candidates,
)

__all__ = [
    "CaseView",
    "DemoInputs",
    "build_case_view",
    "default_inputs",
    "list_cases",
    "read_candidates",
]
