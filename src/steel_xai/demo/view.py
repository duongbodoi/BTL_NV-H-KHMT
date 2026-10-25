"""Lớp dữ liệu của demo (Phase 13): dựng nội dung trình bày một ca từ kết quả ĐÃ chạy.

Không gọi LLM, không chạy lại pipeline: demo đọc `RunRecord` của Phase 12 (cấu hình C, kèm A/B để so
sánh), chỉ báo bất thường do engine tính (`anomaly_candidates.csv`) và thư viện giả thuyết. Nhờ vậy
lúc trình bày không phụ thuộc mạng hay hạn mức API, và mọi thứ hiển thị là đúng thứ đã được đo.
Streamlit (`demo/app.py`) và CLI (`scripts/demo.py`) dùng chung lớp này. Mọi số do code định dạng
bằng CÙNG hàm với báo cáo (`report.numbers.display`), theo đơn vị khai trong `configs/report.yaml`.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from steel_xai.contracts import RunRecord

STATUS_ORDER = ("SUPPORTED", "PARTIAL", "UNVERIFIED", "CONTRADICTED")
STATUS_VI = {
    "SUPPORTED": "Được ủng hộ",
    "PARTIAL": "Ủng hộ một phần",
    "UNVERIFIED": "Chưa kiểm được",
    "CONTRADICTED": "Bị phản bác",
}
# Chỉ báo hiển thị (theo loại bất thường); tên giống `configs/report.yaml: indicator_units`
INDICATORS = {
    "inventory_increase": (
        "inv_growth_yoy",
        "inv_minus_rev_growth",
        "dio_change_days",
        "inv_growth_vs_peer",
        "inv_growth_z",
    ),
    "gross_margin_change": ("gm_change_pp", "gm_change_vs_peer_pp", "gm_change_z"),
}
UNKNOWN_UNIT = " (không rõ đơn vị)"


@dataclass(frozen=True)
class Quote:
    text: str
    doc_id: str
    page: int | None
    polarity: str  # support | contradict


@dataclass(frozen=True)
class CheckView:
    check_id: str
    kind: str  # quant | text
    importance: str  # critical | supporting
    outcome: str
    detail: str
    values: dict[str, str]
    quotes: tuple[Quote, ...] = ()


@dataclass(frozen=True)
class HypothesisView:
    rank: int
    hypothesis_id: str
    code: str
    claim: str
    status: str
    critical: str  # "đạt/tổng" check critical
    evidence: tuple[CheckView, ...]
    contradictions: tuple[CheckView, ...]
    missing: tuple[CheckView, ...]
    failed: tuple[CheckView, ...]  # KHÔNG gồm check đã nằm trong `contradictions`


@dataclass(frozen=True)
class CompareView:
    """Một dòng so sánh A / B / C trên cùng ca, cùng lần chạy (số liệu BẢN NHÁP ĐẦU)."""

    config: str
    hypotheses: tuple[str, ...]  # top-3, đúng thứ tự cấu hình xếp
    unsupported: int | None
    numeric_mismatches: int | None
    claims: int | None
    citations_ok: int
    citations_total: int


@dataclass(frozen=True)
class Indicator:
    name: str
    label: str
    display: str


@dataclass
class CaseView:
    case_id: str
    split: str
    run_id: str
    anomaly_id: str
    company: str
    period: str
    anomaly_type: str
    direction: str
    asof_date: str
    indicators: list[Indicator]
    rules_hit: tuple[str, ...]
    flags: tuple[str, ...]
    hypotheses: list[HypothesisView]
    report_markdown: str
    report_status: str
    model: str
    series: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    compare: list[CompareView] = field(default_factory=list)
    reference: tuple[str, ...] = ()  # đáp án tham chiếu CUỐI (nếu có), chỉ để đối chiếu
    warnings: list[str] = field(default_factory=list)  # dữ liệu thiếu/lệch: hiện cho người xem


# ---------------- đọc dữ liệu


def run_dir(output_dir: Path, split: str) -> Path:
    return output_dir / split / "runs"


def run_hint(split: str) -> str:
    if split == "test":
        return (
            "Tập test chỉ chạy sau khi đã gán nhãn xong và đóng băng "
            "(`python scripts/eval_tools.py freeze`), rồi `python scripts/run_eval.py run "
            "--split test`."
        )
    return "Chạy trước: `python scripts/run_eval.py run --split dev --cases C01 --runs 1`."


def list_cases(output_dir: Path, split: str) -> list[tuple[str, list[str]]]:
    """[(case_id, [run_id...])] có kết quả C status=ok."""
    base = run_dir(output_dir, split) / "C"
    out = []
    if not base.is_dir():
        return out
    for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        runs = []
        for p in sorted(case_dir.glob("*.json")):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("status") == "ok":
                    runs.append(p.stem)
            except (OSError, ValueError):
                continue
        if runs:
            out.append((case_dir.name, runs))
    return out


def _load(
    output_dir: Path, split: str, config: str, case_id: str, run_id: str
) -> tuple[RunRecord | None, str]:
    """(bản ghi status=ok hoặc None, lý do khi None; rỗng nếu chỉ là chưa chạy)."""
    p = run_dir(output_dir, split) / config / case_id / f"{run_id}.json"
    if not p.is_file():
        return None, ""
    try:
        rec = RunRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, f"{config}: file kết quả hỏng ({type(e).__name__}: {e})"
    if rec.status != "ok":
        return None, f"{config}: lần chạy lỗi ({rec.error.splitlines()[0] if rec.error else ''})"
    return rec, ""


def read_candidates(path: Path) -> dict[str, dict[str, str]]:
    """anomaly_id → dòng của anomaly_candidates.csv (chỉ báo do engine tính)."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {
            f"{r['company']}|{r['period']}|{r['type']}|{r['direction']}": r
            for r in csv.DictReader(f)
        }


# ---------------- định dạng số


def fmt_number(raw: Any, unit: str | None) -> str:
    """Số do code tính → chuỗi Việt theo đơn vị (cùng hàm với báo cáo). Không rõ đơn vị: in số
    kèm ghi chú thay vì đoán."""
    from steel_xai.report.numbers import display

    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return str(raw)
    if not d.is_finite():
        return str(raw)
    if unit is None:
        return display(d, "number") + UNKNOWN_UNIT
    try:
        return display(d, unit)
    except (ValueError, KeyError):
        return display(d, "number") + UNKNOWN_UNIT


def fmt_indicator(name: str, raw: str, units: Mapping[str, str] | None = None) -> str:
    """Chỉ báo bất thường theo `indicator_units` của báo cáo."""
    try:
        if not math.isfinite(float(raw)):
            return raw
    except ValueError:
        return raw
    return fmt_number(raw, (units or {}).get(name))


# ---------------- dựng view


def _check(f: Mapping[str, Any], unit: str | None) -> CheckView:
    q = f.get("quant") or {}
    values = {k: fmt_number(v, unit) for k, v in (q.get("values") or {}).items() if v is not None}
    # diễn giải chỉ hữu ích khi check KHÔNG có giá trị (thiếu dữ liệu); có giá trị thì nó chỉ lặp
    # lại số thô
    detail = "" if values else (q.get("reason") or f.get("detail") or "")
    quotes = tuple(
        Quote(
            e.get("quote", ""),
            (e.get("provenance") or {}).get("doc_id", ""),
            (e.get("provenance") or {}).get("page"),
            e.get("polarity", ""),
        )
        for e in f.get("text") or []
    )
    return CheckView(
        f.get("check_id", ""),
        f.get("kind", ""),
        f.get("importance", ""),
        f.get("outcome", ""),
        str(detail),
        values,
        quotes,
    )


def hypothesis_views(
    rec: RunRecord,
    library: Mapping[str, Any],
    tool_units: Mapping[str, str] | None = None,
    warnings: list[str] | None = None,
) -> list[HypothesisView]:
    verdicts = {v["hypothesis_id"]: v for v in rec.outputs.get("verdicts") or []}
    units = tool_units or {}
    out = []
    for r in rec.outputs.get("ranking") or []:
        hid = r["hypothesis_id"]
        v = verdicts.get(hid, {})
        h = library.get(hid)
        if h is None and warnings is not None:
            warnings.append(
                f"giả thuyết {hid} không có trong thư viện hiện tại: số của nó không rõ đơn vị"
            )
        tools = {c.id: c.tool for c in getattr(h, "checks", ())}

        def conv(fs, tools=tools):
            out_ = []
            for f in fs or []:
                tool = tools.get(f.get("check_id"))
                out_.append(_check(f, units.get(tool) if tool else None))
            return tuple(out_)

        contra = conv(v.get("contradictions"))
        contra_ids = {c.check_id for c in contra}
        out.append(
            HypothesisView(
                rank=int(r["rank"]),
                hypothesis_id=hid,
                code=(getattr(h, "code", None) or "") if h is not None else "",
                claim=getattr(h, "claim", None) or r.get("text", ""),
                status=r.get("status") or v.get("status", ""),
                critical=f"{v.get('critical_passed', 0)}/{v.get('critical_total', 0)}",
                evidence=conv(v.get("evidence")),
                contradictions=contra,
                missing=conv(v.get("missing")),
                # check critical thất bại được tính là phản bác thì chỉ hiện ở mục phản bác
                failed=tuple(c for c in conv(v.get("failed")) if c.check_id not in contra_ids),
            )
        )
    return out


def series_from_verdicts(rec: RunRecord) -> dict[str, list[tuple[str, float]]]:
    """Chuỗi giá ngoài mà các check của C đã dùng (đầu vào do code đọc, theo kỳ/tháng)."""
    pts: dict[str, dict[str, float]] = {}
    for v in rec.outputs.get("verdicts") or []:
        for group in ("evidence", "contradictions", "failed", "missing"):
            for f in v.get(group) or []:
                for inp in (f.get("quant") or {}).get("inputs") or []:
                    if inp.get("kind") != "external":
                        continue
                    try:
                        pts.setdefault(inp["name"], {})[str(inp["period"])] = float(inp["value"])
                    except (KeyError, TypeError, ValueError):
                        continue
    return {k: sorted(v.items()) for k, v in pts.items()}


def compare_view(rec: RunRecord, top_k: int = 3) -> CompareView:
    rep = rec.outputs.get("report") or {}
    first = rep.get("first_draft") or {}
    cits = rep.get("citations") or []
    return CompareView(
        config=rec.config,
        hypotheses=tuple(
            r["text"] for r in (rec.outputs.get("ranking") or []) if r["rank"] <= top_k
        ),
        unsupported=first.get("unsupported"),
        numeric_mismatches=first.get("numeric_mismatches"),
        claims=first.get("claims"),
        citations_ok=sum(bool(c.get("verified")) for c in cits),
        citations_total=len(cits),
    )


def report_label(status: str) -> str:
    """Mô tả phần giải thích cuối theo kết quả bộ kiểm (đúng sự thật, kể cả khi bị từ chối)."""
    if status in ("accepted", "accepted_after_retry"):
        retry = " sau khi sinh lại" if status == "accepted_after_retry" else ""
        return (
            f"LLM viết từ kết quả đã kiểm chứng; code đã đối chiếu số, nguồn, nội dung cấm và "
            f"chấp nhận{retry}."
        )
    if status in ("rejected", "llm_error"):
        return (
            f"Bản viết của LLM KHÔNG được chấp nhận ({status}): báo cáo dưới đây chỉ gồm phần do "
            "code dựng (bảng số, trạng thái, nguồn)."
        )
    return "Không có thông tin bộ kiểm."


def build_case_view(
    output_dir: Path,
    split: str,
    case_id: str,
    run_id: str,
    *,
    candidates: Mapping[str, Mapping[str, str]],
    library: Mapping[str, Any],
    reference: Sequence[str] = (),
    baselines: Sequence[str] = ("A", "B"),
    tool_units: Mapping[str, str] | None = None,
    indicator_units: Mapping[str, str] | None = None,
    indicator_labels: Mapping[str, str] | None = None,
) -> CaseView:
    rec, why = _load(output_dir, split, "C", case_id, run_id)
    if rec is None:
        raise FileNotFoundError(
            f"không có kết quả C dùng được cho {split}/{case_id}/{run_id}"
            + (f": {why}" if why else "")
        )
    company, period, atype, direction = rec.anomaly_id.split("|")
    warnings: list[str] = []
    row = candidates.get(rec.anomaly_id)
    if row is None:
        warnings.append(
            "không tìm thấy bất thường trong data/processed/anomaly_candidates.csv: thiếu chỉ báo "
            "và mốc as-of (chạy lại scripts/build_anomaly_candidates.py); bảng số trong báo cáo "
            "bên dưới vẫn đúng với lần chạy"
        )
        row = {}
    names = INDICATORS.get(atype, ())
    labels = indicator_labels or {}
    rep = rec.outputs.get("report") or {}
    view = CaseView(
        case_id=case_id,
        split=split,
        run_id=run_id,
        anomaly_id=rec.anomaly_id,
        company=company,
        period=period,
        anomaly_type=atype,
        direction=direction,
        asof_date=row.get("asof_date", ""),
        indicators=[
            Indicator(n, labels.get(n, n), fmt_indicator(n, row[n], indicator_units))
            for n in names
            if row.get(n)
        ],
        rules_hit=tuple(x for x in (row.get("rules_hit") or "").split(";") if x),
        flags=tuple(x for x in (row.get("flags") or "").split(";") if x),
        hypotheses=hypothesis_views(rec, library, tool_units, warnings),
        report_markdown=rep.get("markdown", ""),
        report_status=rep.get("status", ""),
        model=f"{rec.llm.get('provider')}/{rec.llm.get('model')}",
        series=series_from_verdicts(rec),
        reference=tuple(reference),
        warnings=warnings,
    )
    for cfg in baselines:
        b, why = _load(output_dir, split, cfg, case_id, run_id)
        if b is not None:
            view.compare.append(compare_view(b))
        elif why:
            warnings.append(why)
    view.compare.append(compare_view(rec))  # C, cùng chỉ số bản nháp đầu
    return view


def status_counts(view: CaseView) -> dict[str, int]:
    out = {s: 0 for s in STATUS_ORDER}
    for h in view.hypotheses:
        if h.status in out:
            out[h.status] += 1
    return out


@dataclass(frozen=True)
class DemoInputs:
    output_dir: Path
    candidates: dict[str, dict[str, str]]
    library: dict[str, Any]
    disclaimer: str
    tool_units: dict[str, str]
    indicator_units: dict[str, str]
    indicator_labels: dict[str, str]
    series_units: dict[str, str]
    references: dict[str, tuple[str, ...]]  # case_id → đáp án CUỐI (rỗng khi chưa có)

    def case_view(self, split: str, case_id: str, run_id: str) -> CaseView:
        return build_case_view(
            self.output_dir,
            split,
            case_id,
            run_id,
            candidates=self.candidates,
            library=self.library,
            reference=self.references.get(case_id, ()),
            tool_units=self.tool_units,
            indicator_units=self.indicator_units,
            indicator_labels=self.indicator_labels,
        )


def default_inputs() -> DemoInputs:
    """Đường dẫn và dữ liệu phụ từ config của repo (chỉ đọc file, không gọi API)."""
    from steel_xai.config import PROJECT_ROOT, load_config
    from steel_xai.eval.settings import load_runner_settings
    from steel_xai.hypotheses import load_library
    from steel_xai.report.settings import load_report_settings

    processed = PROJECT_ROOT / load_config("dataset")["paths"]["processed"]
    refs: dict[str, tuple[str, ...]] = {}
    try:
        from steel_xai.eval.references import load_references

        r = load_references()
        if r.final:
            refs = {c.case_id: tuple(c.reference_set) for c in r.cases}
    except Exception:  # noqa: BLE001 — demo vẫn chạy khi bộ nhãn chưa đủ/hỏng
        refs = {}
    lib = load_library()
    rs = load_report_settings()
    return DemoInputs(
        output_dir=load_runner_settings().output_dir,
        candidates=read_candidates(processed / "anomaly_candidates.csv"),
        # cả draft: kết quả cũ có thể chạy với giả thuyết chưa duyệt; chỉ để hiển thị mã/đơn vị
        library={h.id: h for h in (*lib.drafts, *lib.reviewed)},
        disclaimer=rs.disclaimer,
        tool_units=dict(rs.tool_units),
        indicator_units=dict(rs.indicator_units),
        indicator_labels=dict(rs.indicator_labels),
        series_units=dict(rs.series_units),
        references=refs,
    )


@dataclass(frozen=True)
class ShowcaseItem:
    case_id: str
    role: str
    note: str


def load_showcase(path: Path | None = None) -> tuple[str, list[ShowcaseItem]]:
    """(split, ca trình diễn theo thứ tự) đọc từ configs/demo/showcase.yaml."""
    import yaml

    from steel_xai.config import PROJECT_ROOT

    p = path or PROJECT_ROOT / "configs" / "demo" / "showcase.yaml"
    if not p.is_file():
        return "dev", []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    items = [
        ShowcaseItem(str(c["case_id"]), str(c.get("role", "")), str(c.get("note", "")))
        for c in raw.get("cases") or []
        if isinstance(c, dict) and c.get("case_id")
    ]
    return str(raw.get("split", "dev")), items
