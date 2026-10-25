"""Test Phase 13: demo đọc kết quả đã chạy (không gọi API) và hiển thị đủ các phần spec yêu cầu:
bất thường, giả thuyết, trạng thái, bằng chứng, trích dẫn, phần còn thiếu, giải thích cuối, dòng
"không phải tư vấn đầu tư"."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from steel_xai.contracts import Check, Hypothesis, RunRecord
from steel_xai.demo.text import render_text
from steel_xai.demo.view import (
    build_case_view,
    fmt_indicator,
    list_cases,
    read_candidates,
    status_counts,
)

AID = "AAA|FY2021|inventory_increase|up"
DISCLAIMER = "Hỗ trợ phân tích, không phải tư vấn đầu tư. Không kết luận gian lận."
LIB = {
    "h1": Hypothesis(
        id="h1",
        anomaly_type="inventory_increase",
        claim="Tồn kho tăng do tích trữ nguyên liệu.",
        business_rationale="r",
        expected_quantitative_evidence=(),
        expected_textual_evidence=(),
        contradictory_evidence=(),
        required_external_data=(),
        critical_missing_evidence=(),
        status="reviewed",
        reviewed_by="Người Duyệt",
        code="INV-1",
        direction="up",
        checks=(
            Check("raw_up", "quant", "critical", "support", tool="growth_yoy"),
            Check("txt", "text", "supporting", "support", queries=("dự trữ",)),
        ),
    ),
}


def finding(cid, kind, importance, outcome, values=None, reason="", text=(), inputs=()):
    return {
        "check_id": cid,
        "kind": kind,
        "importance": importance,
        "polarity": "support",
        "outcome": outcome,
        "detail": reason,
        "quant": None
        if kind == "text"
        else {
            "check_id": cid,
            "status": "PASS",
            "values": values or {},
            "inputs": list(inputs),
            "reason": reason,
        },
        "text": list(text),
    }


QUOTE = {
    "kind": "text",
    "polarity": "support",
    "check_id": "txt",
    "quote": "Công ty tăng dự trữ nguyên liệu.",
    "provenance": {"doc_id": "AAA_2021", "page": 12, "chunk_id": "c1", "source_text": "..."},
    "reason": "r",
}


def c_record(status="ok", run="run1"):
    verdict = {
        "hypothesis_id": "h1",
        "status": "PARTIAL",
        "rank": 1,
        "critical_passed": 1,
        "critical_total": 2,
        "evidence": [
            finding(
                "raw_up",
                "quant",
                "critical",
                "satisfied",
                {"value": "0.5"},
                "growth_yoy: 0.5",
                inputs=[
                    {"kind": "external", "name": "hrc_price", "period": "2021-01", "value": "600"},
                    {"kind": "external", "name": "hrc_price", "period": "2021-12", "value": "800"},
                ],
            ),
            finding("txt", "text", "supporting", "satisfied", text=[QUOTE]),
        ],
        "missing": [finding("capex", "quant", "critical", "unknown", reason="thiếu capex FY2021")],
        "contradictions": [],
        "failed": [],
    }
    return RunRecord(
        "C01",
        "dev",
        AID,
        "C",
        run,
        status,
        outputs={
            "mode": "library",
            "ranking": [
                {"rank": 1, "hypothesis_id": "h1", "text": "x", "status": "PARTIAL"},
                {
                    "rank": 2,
                    "hypothesis_id": "h2",
                    "text": "Giả thuyết ngoài thư viện test",
                    "status": "UNVERIFIED",
                },
            ],
            "verdicts": [verdict],
            "report": {
                "status": "accepted",
                "markdown": "# Báo cáo\n\nCó thể do tích trữ (E1).",
                "claims": [],
                "citations": [],
            },
        },
        llm={"provider": "gemini", "model": "m"},
    )


def baseline_record(cfg):
    return RunRecord(
        "C01",
        "dev",
        AID,
        cfg,
        "run1",
        "ok",
        outputs={
            "mode": "free",
            "ranking": [
                {"rank": i, "hypothesis_id": None, "text": f"{cfg} gt {i}", "status": None}
                for i in (1, 2, 3, 4)
            ],
            "report": {
                "claims": [{"text": "k"}],
                "first_draft": {"unsupported": 2, "numeric_mismatches": 1},
                "citations": [{"verified": True}, {"verified": False}],
            },
        },
    )


def write(out: Path, rec: RunRecord) -> None:
    p = out / rec.split / "runs" / rec.config / rec.case_id / f"{rec.run_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec.to_dict(), ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def out(tmp_path):
    o = tmp_path / "outputs"
    write(o, c_record())
    write(o, c_record(status="error", run="run2"))  # lần lỗi không được hiển thị
    write(o, baseline_record("A"))
    write(o, baseline_record("B"))
    return o


CANDIDATES = {
    AID: {
        "asof_date": "2022-03-21",
        "inv_growth_yoy": "2.4926",
        "dio_change_days": "45.28",
        "rules_hit": "inv_growth_yoy;dio_change_days",
        "flags": "weak_baseline",
    }
}


def view(out):
    return build_case_view(
        out,
        "dev",
        "C01",
        "run1",
        candidates=CANDIDATES,
        library=LIB,
        tool_units={"growth_yoy": "ratio"},
        indicator_units={"inv_growth_yoy": "ratio", "dio_change_days": "days"},
        indicator_labels={"inv_growth_yoy": "tăng trưởng hàng tồn kho"},
    )


def test_list_cases_only_ok_runs(out):
    assert list_cases(out, "dev") == [("C01", ["run1"])]
    assert list_cases(out, "test") == []


def test_case_view_contents(out):
    v = view(out)
    assert (v.company, v.period, v.direction, v.asof_date) == ("AAA", "FY2021", "up", "2022-03-21")
    assert v.rules_hit == ("inv_growth_yoy", "dio_change_days")
    h = v.hypotheses[0]
    assert (h.code, h.status, h.critical) == ("INV-1", "PARTIAL", "1/2")
    assert h.claim == LIB["h1"].claim
    quant = h.evidence[0]
    assert quant.values == {"value": "50,0%"} and quant.detail == ""  # số đã định dạng, không lặp
    q = h.evidence[1].quotes[0]
    assert (q.text, q.doc_id, q.page) == (QUOTE["quote"], "AAA_2021", 12)
    assert h.missing[0].detail == "thiếu capex FY2021"
    assert v.hypotheses[1].claim == "Giả thuyết ngoài thư viện test"  # không có trong thư viện
    assert any("h2 không có trong thư viện" in w for w in v.warnings)
    assert [(i.name, i.label, i.display) for i in v.indicators] == [
        ("inv_growth_yoy", "tăng trưởng hàng tồn kho", "249,3%"),
        ("dio_change_days", "dio_change_days", "45,3 ngày"),
    ]
    assert v.flags == ("weak_baseline",)
    assert status_counts(v) == {"SUPPORTED": 0, "PARTIAL": 1, "UNVERIFIED": 1, "CONTRADICTED": 0}
    assert v.series == {"hrc_price": [("2021-01", 600.0), ("2021-12", 800.0)]}
    a, b, c = v.compare
    assert a.config == "A" and a.hypotheses == ("A gt 1", "A gt 2", "A gt 3")
    assert (b.unsupported, b.numeric_mismatches, b.citations_ok, b.citations_total) == (2, 1, 1, 2)
    assert c.config == "C"  # C cũng có dòng so sánh (cùng chỉ số bản nháp đầu)


def test_missing_run_raises(out):
    with pytest.raises(FileNotFoundError):
        build_case_view(out, "dev", "C01", "run2", candidates={}, library=LIB)


def test_render_text_has_every_required_part(out):
    t = render_text(view(out), DISCLAIMER)
    assert t.count(DISCLAIMER) == 2  # đầu và cuối
    for part in (
        "BẤT THƯỜNG",
        "Hàng tồn kho tăng",
        "249,3%",
        "45,3 ngày",
        "GIẢ THUYẾT",
        "[PARTIAL] INV-1",
        "Bằng chứng ủng hộ",
        QUOTE["quote"],
        "AAA_2021, trang 12",
        "Còn thiếu",
        "thiếu capex FY2021",
        "GIẢI THÍCH CUỐI",
        "Có thể do tích trữ",
        "SO SÁNH CẤU HÌNH",
        "A gt 1",
    ):
        assert part in t, part


def test_fmt_indicator_uses_report_units():
    u = {"inv_minus_rev_growth": "ratio_diff", "gm_change_pp": "pp", "dio_change_days": "days"}
    assert fmt_indicator("inv_minus_rev_growth", "1.055", u) == "105,5 điểm %"
    assert fmt_indicator("gm_change_pp", "-8.566", u) == "-8,6 điểm %"
    assert fmt_indicator("dio_change_days", "1234.5", u) == "1.234,5 ngày"
    assert fmt_indicator("lạ", "2.5", u).endswith("(không rõ đơn vị)")  # không đoán đơn vị
    assert fmt_indicator("x", "nan", u) == "nan" and fmt_indicator("x", "abc", u) == "abc"


def test_read_candidates(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(
        "﻿company,period,type,direction,asof_date\nAAA,FY2021,inventory_increase,up,2022-03-21\n",
        encoding="utf-8",
    )
    assert read_candidates(p)[AID]["asof_date"] == "2022-03-21"
    assert read_candidates(tmp_path / "không_có.csv") == {}


def test_cli_lists_and_shows(out):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    script = Path(__file__).resolve().parents[1] / "scripts" / "demo.py"
    code = (
        "import sys, runpy; from pathlib import Path; import steel_xai.demo.view as V\n"
        "orig = V.default_inputs\n"
        "def fake():\n"
        "    i = orig()\n"
        "    from dataclasses import replace\n"
        f"    return replace(i, output_dir=Path(r'{out}'))\n"
        "V.default_inputs = fake\n"
        f"sys.argv = ['demo.py'] + sys.argv[1:]\n"
        f"runpy.run_path(r'{script}', run_name='__main__')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code, "--list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert "C01: run1" in r.stdout, r.stderr
    r = subprocess.run(
        [sys.executable, "-c", code, "--case", "C01"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert "[PARTIAL]" in r.stdout and "không phải tư vấn đầu tư" in r.stdout, r.stderr
    r = subprocess.run(
        [sys.executable, "-c", code, "--case", "C99"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert "chưa có kết quả" in r.stdout


def test_streamlit_app_renders(out, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STEEL_XAI_DEMO_OUTPUT_DIR", str(out))
    app = Path(__file__).resolve().parents[1] / "src" / "steel_xai" / "demo" / "app.py"
    at = AppTest.from_file(str(app), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [w.value for w in at.warning].count(DISCLAIMER) == 2
    assert at.title[0].value.startswith("Hàng tồn kho tăng — AAA FY2021")
    headers = [h.value for h in at.header]
    assert "Giải thích cuối" in headers and "Bằng chứng, trích dẫn và phần còn thiếu" in headers
    assert any(QUOTE["quote"] in m.value.replace(chr(92), "") for m in at.markdown)


# ---------------- sửa sau review độc lập Phase 13


def _contradicted_record():
    """C có giả thuyết CONTRADICTED ở hạng 4, check critical thất bại nằm cả ở failed lẫn
    contradictions (như verdict.py làm), và báo cáo bị bộ kiểm từ chối."""
    rec = c_record()
    bad = finding("raw_up", "quant", "critical", "not_satisfied", {"value": "0.1"})
    ranking = [
        {"rank": 1, "hypothesis_id": "h1", "text": "x", "status": "PARTIAL"},
        *(
            {"rank": i, "hypothesis_id": f"u{i}", "text": f"u{i}", "status": "UNVERIFIED"}
            for i in (2, 3)
        ),
        {"rank": 4, "hypothesis_id": "h1", "text": "x", "status": "CONTRADICTED"},
    ]
    verdict = {**rec.outputs["verdicts"][0], "contradictions": [bad], "failed": [bad]}
    outputs = {
        **rec.outputs,
        "ranking": ranking,
        "verdicts": [verdict],
        "report": {"status": "rejected", "markdown": "# Khung", "claims": [], "citations": []},
    }
    return RunRecord(rec.case_id, rec.split, rec.anomaly_id, "C", "run1", "ok", outputs=outputs)


def test_contradictions_always_shown_and_not_duplicated(tmp_path):
    o = tmp_path / "o"
    write(o, _contradicted_record())
    v = build_case_view(
        o,
        "dev",
        "C01",
        "run1",
        candidates=CANDIDATES,
        library=LIB,
        tool_units={"growth_yoy": "ratio"},
    )
    h4 = v.hypotheses[-1]
    assert h4.rank == 4 and [c.check_id for c in h4.contradictions] == ["raw_up"]
    assert h4.failed == ()  # đã ở mục phản bác thì không lặp ở "kỳ vọng không xảy ra"
    t = render_text(v, DISCLAIMER, top=3)
    seg = t[t.index("#4 [CONTRADICTED]") :]
    assert "Bằng chứng phản bác" in seg.split("GIẢI THÍCH CUỐI")[0]  # ngoài top-3 vẫn hiện
    assert "KHÔNG được chấp nhận (rejected)" in t  # không nói "LLM viết" khi bị từ chối


def test_missing_candidates_and_broken_baseline_warn(tmp_path):
    o = tmp_path / "o"
    write(o, c_record())
    broken = o / "dev" / "runs" / "A" / "C01" / "run1.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{không phải json", encoding="utf-8")
    write(o, RunRecord("C01", "dev", AID, "B", "run1", "error", error="LLMError: 429"))
    v = build_case_view(o, "dev", "C01", "run1", candidates={}, library=LIB)
    joined = " | ".join(v.warnings)
    assert "anomaly_candidates.csv" in joined and v.asof_date == "" and v.indicators == []
    assert "A: file kết quả hỏng" in joined and "B: lần chạy lỗi (LLMError: 429)" in joined
    assert [c.config for c in v.compare] == ["C"]


def test_unknown_library_marks_units_unknown(out):
    v = build_case_view(
        out,
        "dev",
        "C01",
        "run1",
        candidates=CANDIDATES,
        library={},
        tool_units={"growth_yoy": "ratio"},
    )
    assert v.hypotheses[0].evidence[0].values["value"].endswith("(không rõ đơn vị)")
    assert any("không có trong thư viện" in w for w in v.warnings)


def test_md_escape_keeps_quotes_verbatim():
    pytest.importorskip("streamlit")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "demo_app_for_test", Path(__file__).resolve().parents[1] / "src/steel_xai/demo/app.py"
    )
    src = spec.loader.get_source("demo_app_for_test")
    ns: dict = {}
    exec(src.split("@st.cache_data")[0], ns)  # chỉ phần định nghĩa, không chạy main()
    esc = ns["md_escape"]("Giá *tăng* 5% (_HRC_) $100")
    assert "*" not in esc.replace("\\*", "") and "_" not in esc.replace("\\_", "")
    assert esc.replace("\\", "") == "Giá *tăng* 5% (_HRC_) $100"


def test_streamlit_empty_test_split_and_reference(out, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("STEEL_XAI_DEMO_OUTPUT_DIR", str(out))
    app = Path(__file__).resolve().parents[1] / "src" / "steel_xai" / "demo" / "app.py"
    at = AppTest.from_file(str(app), default_timeout=120).run()
    at.sidebar.checkbox[0].check().run()
    assert any("Chưa có đáp án cuối" in m.value for m in at.markdown)  # chưa có đáp án cuối
    at.sidebar.radio[0].set_value("test").run()
    assert not at.exception
    assert any("đóng băng" in i.value for i in at.info)  # gợi ý đúng cho tập test
    assert [w.value for w in at.warning].count(DISCLAIMER) == 2


# ---------------- bản HTML dự phòng + ca trình diễn (nhóm chọn 2026-10-25)


def test_html_export_is_self_contained_and_complete(out, tmp_path):
    from steel_xai.demo.html_export import markdown_to_html, write_case_files

    v = view(out)
    paths = write_case_files(tmp_path / "demo", [(v, "chính", "ghi chú <b>x</b>")], DISCLAIMER)
    assert [p.name for p in paths] == ["index.html", "C01_run1.html"]
    page = (tmp_path / "demo" / "C01_run1.html").read_text(encoding="utf-8")
    assert page.count(DISCLAIMER) == 2 and "<script" not in page
    import re

    assert not re.search(r"(src|href)\s*=\s*.?https?:", page)  # không tải gì từ mạng
    for part in (
        "1. Bất thường",
        "249,3%",
        "PARTIAL",
        QUOTE["quote"],
        "AAA_2021, trang 12",
        "thiếu capex FY2021",
        "4. Giải thích cuối",
        "5. So sánh cấu hình",
        "<svg",
        "ghi chú &lt;b&gt;x&lt;/b&gt;",
    ):
        assert part in page, part
    index = (tmp_path / "demo" / "index.html").read_text(encoding="utf-8")
    assert "C01_run1.html" in index and DISCLAIMER in index
    md = markdown_to_html("# T\n\n| a | b |\n|---|---|\n| 1 | <x> |\n\n- `c` **d**\n\n> q")
    assert "<h3>T</h3>" in md and "<td>&lt;x&gt;</td>" in md
    assert "<code>c</code> <b>d</b>" in md and "<blockquote>q</blockquote>" in md


def test_showcase_config(tmp_path):
    from steel_xai.demo.view import load_showcase

    split, items = load_showcase()
    assert split == "dev" and [i.case_id for i in items][:2] == ["C06", "C01"]
    assert items[1].role == "ví dụ ca sai"
    assert load_showcase(tmp_path / "không_có.yaml") == ("dev", [])
