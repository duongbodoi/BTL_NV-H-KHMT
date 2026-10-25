"""Nhãn tham chiếu, Cohen's kappa, giải quyết bất đồng (spec mục 15.2). NHÃN do người gán.

Mỗi người gán một file `data/eval/labels/<tên>.yaml`, độc lập:

    annotator: Nguyễn Văn A
    labels:
      - case_id: C01
        reference_set: [inventory_raw_material_stockpiling]   # id thư viện hoặc `other`
        statuses: {inventory_raw_material_stockpiling: SUPPORTED}   # tùy chọn (verification acc.)
        sources:                                              # độc lập với kho RAG
          - {title: "...", date: "2024-03-20", url: "https://..."}
        note: ""                                              # bắt buộc khi có `other`

Người thứ ba giải quyết bất đồng trong `data/eval/adjudication.yaml`:

    adjudicator: Trần Thị B
    decisions:
      - {case_id: C03, reference_set: [...], statuses: {...}, note: "lý do"}

Kappa (spec 15.4) tính trên các quyết định nhị phân (ca, giả thuyết cùng loại hoặc `other`):
người gán có đưa giả thuyết đó vào đáp án không. Trạng thái có kappa riêng trên các cặp cả hai
cùng gán. Code KHÔNG sinh hay sửa nhãn.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from steel_xai.config import PROJECT_ROOT, load_config
from steel_xai.contracts import CaseRecord, VerdictStatus
from steel_xai.eval.cases import read_yaml
from steel_xai.eval.settings import EvalSettings
from steel_xai.hypotheses.library import LibrarySettings, person_name_problem

_LABEL_KEYS = {"case_id", "reference_set", "statuses", "sources", "note"}
_SOURCE_KEYS = {"title", "date", "url", "doc_id", "page", "note"}
_STATUSES = tuple(s.value for s in VerdictStatus)

# taxonomy: loại bất thường → {id giả thuyết → direction (up | down | both)}
Taxonomy = Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class Label:
    case_id: str
    reference_set: frozenset[str]
    statuses: dict[str, str]
    sources: tuple[dict[str, str], ...]
    note: str = ""


@dataclass(frozen=True)
class LabelSet:
    annotator: str
    labels: dict[str, Label]  # case_id → nhãn
    source: str = ""  # tên file


@dataclass(frozen=True)
class Kappa:
    n: int
    observed: float | None
    expected: float | None
    kappa: float | None  # None: không xác định (không có mục nào, hoặc cả hai luôn cùng một nhãn)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "observed_agreement": self.observed,
            "expected_agreement": self.expected,
            "kappa": self.kappa,
            "note": self.note,
        }


@dataclass
class _Acc:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------- taxonomy, kho RAG


def taxonomy_from_library(hypotheses: Iterable[Any]) -> dict[str, dict[str, str]]:
    """Chỉ giả thuyết `reviewed` (thư viện chính thức) mới là đáp án hợp lệ."""
    tax: dict[str, dict[str, str]] = {}
    for h in hypotheses:
        if h.status == "reviewed":
            tax.setdefault(h.anomaly_type, {})[h.id] = h.direction or ""
    return tax


@dataclass(frozen=True)
class RagIndex:
    """Tài liệu trong kho RAG: doc_id và (công ty, năm) của từng tài liệu."""

    doc_ids: frozenset[str]
    company_years: frozenset[tuple[str, int]]


def load_rag_index(chunks_path: str | Path | None = None) -> RagIndex:
    """Đọc từ file chunk do build_rag_index.py dựng (chính là kho mà cấu hình B/C truy xuất)."""
    if chunks_path is None:
        ds, rt = load_config("dataset"), load_config("retrieval")
        chunks_path = PROJECT_ROOT / ds["paths"]["processed"] / rt["chunks_file"]
    p = Path(chunks_path)
    if not p.is_file():
        raise FileNotFoundError(f"chưa có kho RAG {p}: chạy scripts/build_rag_index.py trước")
    ids, cy = set(), set()
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                ids.add(c["doc_id"])
                m = re.match(r"FY(\d{4})$", str(c.get("period", "")))
                if m and c.get("company"):
                    cy.add((c["company"], int(m.group(1))))
    return RagIndex(frozenset(ids), frozenset(cy))


_DATE = re.compile(r"^(\d{4})(-\d{2}){0,2}$|^\d{1,2}/\d{1,2}/\d{4}$|^\d{1,2}/\d{4}$")
# nguồn trông như BCTC: nếu đúng công ty + năm có trong kho RAG thì nhiều khả năng là CHÍNH tài liệu
# đã nạp (bản PDF trên web, tên khác) → cảnh báo để người duyệt xem lại
_FS_WORDS = re.compile(r"báo cáo tài chính|\bbctc\b|financial statement", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def source_problem(
    src: Any, settings: EvalSettings, rag: RagIndex, company: str | None = None
) -> tuple[str | None, str | None]:
    """(lỗi, cảnh báo) của một nguồn nhãn. Lỗi: nguồn trỏ thẳng tới tài liệu trong kho RAG (doc_id
    hoặc chuỗi doc_id). Cảnh báo: nguồn có dạng BCTC của công ty/năm đã nạp vào kho. Nguồn ghi tự do
    khác (bài báo, báo cáo phân tích) code không kiểm được; người duyệt kiểm."""
    if not isinstance(src, dict):
        return "nguồn phải là mapping {title, date, url | doc_id}", None
    extra = set(src) - _SOURCE_KEYS
    if extra:
        return f"nguồn có trường lạ {sorted(extra)}", None
    bad = [k for k, v in src.items() if v is not None and not isinstance(v, (str, int))]
    if bad:
        return f"trường {bad} phải là chuỗi", None
    title, date = src.get("title"), src.get("date")
    if not isinstance(title, str) or len(title.strip()) < settings.min_source_title_chars:
        return f"title phải dài >= {settings.min_source_title_chars} ký tự", None
    if not isinstance(date, str) or not _DATE.match(date.strip()):
        return "date phải là ngày công bố của nguồn (yyyy, yyyy-mm, yyyy-mm-dd, dd/mm/yyyy)", None
    if not any(isinstance(src.get(k), str) and src[k].strip() for k in ("url", "doc_id")):
        return "cần url hoặc doc_id để truy lại nguồn", None
    if src.get("doc_id") in rag.doc_ids:
        return f"doc_id {src['doc_id']!r} thuộc kho RAG; nguồn nhãn phải độc lập (spec 15.2)", None
    blob = " ".join(str(v) for v in src.values() if v is not None)
    low = blob.lower()
    hit = next((d for d in sorted(rag.doc_ids) if d.lower() in low), None)
    if hit:
        return f"nguồn nhắc tài liệu trong kho RAG ({hit}); nguồn nhãn phải độc lập", None
    if _FS_WORDS.search(blob):
        companies = {
            c for c, _ in rag.company_years if re.search(rf"\b{re.escape(c)}\b", blob, re.I)
        }
        if company:
            companies.add(company)
        # năm của tài liệu, không lấy từ `date` (BCTC năm X công bố năm X+1)
        no_date = " ".join(str(v) for k, v in src.items() if k != "date" and v is not None)
        years = {int(m.group(0)) for m in _YEAR.finditer(no_date)}
        clash = sorted((c, y) for c in companies for y in years if (c, y) in rag.company_years)
        if clash:
            c, y = clash[0]
            return None, (
                f"nguồn trông như BCTC {c} {y}, tài liệu năm đó đã nằm trong kho RAG; "
                "kiểm lại đây không phải cùng tài liệu"
            )
    return None, None


def person_key(name: str) -> str:
    """So trùng người: bỏ dấu, gộp khoảng trắng, không phân biệt hoa/thường ("Nguyen  Van A")."""
    t = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.casefold().split())


# ---------------- một file nhãn


def _check_reference(
    ref: Any, case: CaseRecord, where: str, taxonomy: Taxonomy, settings: EvalSettings, acc: _Acc
) -> frozenset[str] | None:
    if not isinstance(ref, list) or not ref or not all(isinstance(x, str) for x in ref):
        acc.errors.append(f"{where}: reference_set phải là danh sách id không rỗng")
        return None
    if len(set(ref)) != len(ref):
        acc.errors.append(f"{where}: reference_set có id lặp")
    allowed = taxonomy.get(case.anomaly_type, {})
    for x in ref:
        if x == settings.other_id:
            continue
        if x not in allowed:
            acc.errors.append(
                f"{where}: {x!r} không phải giả thuyết reviewed loại {case.anomaly_type} "
                f"(hoặc {settings.other_id!r})"
            )
        elif allowed[x] not in ("both", case.direction):
            acc.warnings.append(
                f"{where}: {x!r} có direction {allowed[x]!r}, ca có hướng {case.direction!r}"
            )
    return frozenset(ref)


def _check_statuses(
    st: Any, case: CaseRecord, where: str, taxonomy: Taxonomy, acc: _Acc
) -> dict[str, str]:
    if st in (None, {}):
        return {}
    if not isinstance(st, dict):
        acc.errors.append(f"{where}: statuses phải là mapping id → trạng thái")
        return {}
    allowed = taxonomy.get(case.anomaly_type, {})
    out = {}
    for k, v in st.items():
        if k not in allowed:
            acc.errors.append(
                f"{where}: statuses: {k!r} không phải giả thuyết reviewed của loại ca"
            )
        elif v not in _STATUSES:
            acc.errors.append(f"{where}: statuses[{k}] phải thuộc {_STATUSES}")
        else:
            out[k] = v
    return out


def validate_label_file(
    raw: Any,
    cases: Sequence[CaseRecord],
    taxonomy: Taxonomy,
    settings: EvalSettings,
    lib_settings: LibrarySettings,
    rag: RagIndex,
    name: str = "",
) -> tuple[LabelSet | None, list[str], list[str]]:
    acc = _Acc()
    if not isinstance(raw, dict) or set(raw) != {"annotator", "labels"}:
        return None, [f"{name}: file nhãn phải có đúng hai khóa annotator, labels"], []
    who = raw["annotator"]
    if (why := person_name_problem(who, lib_settings)) is not None:
        acc.errors.append(f"{name}: annotator {who!r} {why}")
    items = raw["labels"]
    if not isinstance(items, list):
        return None, [f"{name}: labels phải là danh sách"], []
    by_id = {c.case_id: c for c in cases}
    labels: dict[str, Label] = {}
    for i, item in enumerate(items, start=1):
        where = f"{name} nhãn #{i}"
        if not isinstance(item, dict):
            acc.errors.append(f"{where}: phải là mapping")
            continue
        cid = item.get("case_id")
        where = f"{name} [{cid}]"
        if set(item) - _LABEL_KEYS:
            acc.errors.append(f"{where}: trường lạ {sorted(set(item) - _LABEL_KEYS)}")
        case = by_id.get(cid)
        if case is None:
            acc.errors.append(f"{where}: case_id không có trong danh sách ca")
            continue
        if cid in labels:
            acc.errors.append(f"{where}: gán hai lần")
            continue
        n_err = len(acc.errors)
        ref = _check_reference(item.get("reference_set"), case, where, taxonomy, settings, acc)
        statuses = _check_statuses(item.get("statuses"), case, where, taxonomy, acc)
        note = item.get("note") or ""
        if not isinstance(note, str):
            acc.errors.append(f"{where}: note phải là chuỗi")
            note = ""
        if ref is not None and settings.other_id in ref and not note.strip():
            acc.errors.append(
                f"{where}: có {settings.other_id!r} thì note phải mô tả giả thuyết đó"
            )
        srcs = item.get("sources")
        if not isinstance(srcs, list) or not srcs:
            acc.errors.append(f"{where}: cần ít nhất một nguồn (độc lập với kho RAG)")
            srcs = []
        for j, s in enumerate(srcs, start=1):
            err, warn = source_problem(s, settings, rag, case.anomaly_id.split("|")[0])
            if err is not None:
                acc.errors.append(f"{where} nguồn #{j}: {err}")
            if warn is not None:
                acc.warnings.append(f"{where} nguồn #{j}: {warn}")
        if ref is not None and len(acc.errors) == n_err:
            labels[cid] = Label(cid, ref, statuses, tuple(dict(s) for s in srcs), note)
    missing = [c.case_id for c in cases if c.case_id not in labels]
    if missing:
        acc.warnings.append(f"{name}: chưa gán {len(missing)} ca: {', '.join(missing)}")
    if not isinstance(who, str):
        return None, acc.errors, acc.warnings
    return LabelSet(who.strip(), labels, name), acc.errors, acc.warnings


def load_label_sets(
    cases: Sequence[CaseRecord],
    taxonomy: Taxonomy,
    settings: EvalSettings,
    lib_settings: LibrarySettings,
    rag: RagIndex,
) -> tuple[list[LabelSet], list[str], list[str]]:
    d = settings.labels_dir
    files = sorted(d.glob("*.yaml")) if d.is_dir() else []
    errs: list[str] = []
    stray = (
        sorted(p.name for p in d.iterdir() if p.suffix.lower() in (".yml", ".json"))
        if (d.is_dir())
        else []
    )
    if stray:  # không bỏ sót im lặng một file nhãn đặt sai đuôi
        errs.append(f"file nhãn phải có đuôi .yaml: {stray}")
    warns: list[str] = []
    sets: list[LabelSet] = []
    for p in files:
        ls, e, w = validate_label_file(
            read_yaml(p), cases, taxonomy, settings, lib_settings, rag, p.name
        )
        errs += e
        warns += w
        if ls is not None:
            sets.append(ls)
    if len(files) != settings.annotators:
        errs.append(
            f"cần đúng {settings.annotators} file nhãn trong {d}, có {len(files)} "
            "(mỗi người gán một file, độc lập)"
        )
    names = Counter(person_key(s.annotator) for s in sets)
    errs += [f"hai file nhãn cùng người gán: {n}" for n, k in names.items() if k > 1]
    return sets, errs, warns


# ---------------- kappa


def cohen_kappa(a: Sequence[Any], b: Sequence[Any]) -> Kappa:
    """Cohen's kappa của hai người trên cùng dãy mục. Không xác định khi expected = 1."""
    if len(a) != len(b):
        raise ValueError("hai dãy nhãn phải cùng độ dài")
    n = len(a)
    if n == 0:
        return Kappa(0, None, None, None, "không có mục nào cả hai cùng gán")
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1:
        return Kappa(n, po, pe, None, "hai người luôn cùng một nhãn: kappa không xác định")
    return Kappa(n, po, pe, (po - pe) / (1 - pe))


def _common(a: LabelSet, b: LabelSet) -> list[str]:
    return sorted(set(a.labels) & set(b.labels))


def reference_kappa(
    a: LabelSet, b: LabelSet, cases: Sequence[CaseRecord], taxonomy: Taxonomy, other_id: str
) -> Kappa:
    """Kappa trên quyết định nhị phân: (ca, giả thuyết ÁP DỤNG ĐƯỢC cho ca) có trong đáp án không.

    Áp dụng được = cùng loại, direction là `both` hoặc trùng hướng ca, cộng `other`. Bỏ giả thuyết
    ngược hướng để các mục "cả hai cùng không chọn" hiển nhiên không thổi phồng kappa. Giả thuyết
    ngược hướng mà một người vẫn chọn thì giữ (đó là bất đồng thật). `other` là MỘT mục: hai người
    cùng chọn `other` nhưng mô tả khác nhau vẫn tính là đồng ý. Báo kèm `reference_agreement`.
    """
    xa: list[bool] = []
    xb: list[bool] = []
    for cid, items in _items(a, b, cases, taxonomy, other_id):
        for h in items:
            xa.append(h in a.labels[cid].reference_set)
            xb.append(h in b.labels[cid].reference_set)
    return cohen_kappa(xa, xb)


def _items(
    a: LabelSet, b: LabelSet, cases: Sequence[CaseRecord], taxonomy: Taxonomy, other_id: str
) -> list[tuple[str, list[str]]]:
    by_id = {c.case_id: c for c in cases}
    out = []
    for cid in _common(a, b):
        c = by_id[cid]
        tax = taxonomy.get(c.anomaly_type, {})
        chosen = a.labels[cid].reference_set | b.labels[cid].reference_set
        items = {h for h, d in tax.items() if d in ("both", c.direction)} | (chosen & set(tax))
        out.append((cid, sorted(items | {other_id})))
    return out


def reference_agreement(a: LabelSet, b: LabelSet) -> dict[str, Any]:
    """Thước đo theo ca, đọc kèm kappa: tỷ lệ ca hai tập đáp án trùng hẳn, Jaccard trung bình."""
    common = _common(a, b)
    if not common:
        return {"cases": 0, "exact_match": None, "mean_jaccard": None}
    jac = []
    for cid in common:
        x, y = a.labels[cid].reference_set, b.labels[cid].reference_set
        jac.append(len(x & y) / len(x | y))
    exact = sum(a.labels[c].reference_set == b.labels[c].reference_set for c in common)
    return {
        "cases": len(common),
        "exact_match": exact / len(common),
        "mean_jaccard": sum(jac) / len(jac),
    }


def status_kappa(a: LabelSet, b: LabelSet) -> Kappa:
    xa: list[str] = []
    xb: list[str] = []
    for cid in _common(a, b):
        sa, sb = a.labels[cid].statuses, b.labels[cid].statuses
        for h in sorted(set(sa) & set(sb)):
            xa.append(sa[h])
            xb.append(sb[h])
    return cohen_kappa(xa, xb)


def disagreements(a: LabelSet, b: LabelSet) -> dict[str, list[str]]:
    """case_id → mô tả bất đồng (đáp án khác nhau, hoặc trạng thái khác/thiếu một bên)."""
    out: dict[str, list[str]] = {}
    for cid in _common(a, b):
        la, lb = a.labels[cid], b.labels[cid]
        why = []
        if la.reference_set != lb.reference_set:
            why.append(
                f"đáp án: {a.annotator}={sorted(la.reference_set)} / "
                f"{b.annotator}={sorted(lb.reference_set)}"
            )
        for h in sorted(set(la.statuses) | set(lb.statuses)):
            if la.statuses.get(h) != lb.statuses.get(h):
                why.append(f"trạng thái {h}: {la.statuses.get(h)} / {lb.statuses.get(h)}")
        if why:
            out[cid] = why
    return out


# ---------------- giải quyết bất đồng, đáp án cuối


@dataclass(frozen=True)
class Decision:
    case_id: str
    reference_set: frozenset[str] | None
    statuses: dict[str, str]
    note: str


def validate_adjudication(
    raw: Any,
    cases: Sequence[CaseRecord],
    annotators: Sequence[str],
    taxonomy: Taxonomy,
    settings: EvalSettings,
    lib_settings: LibrarySettings,
) -> tuple[str | None, dict[str, Decision], list[str]]:
    acc = _Acc()
    if raw is None:
        return None, {}, []
    if not isinstance(raw, dict) or set(raw) != {"adjudicator", "decisions"}:
        return None, {}, ["adjudication: phải có đúng hai khóa adjudicator, decisions"]
    who = raw["adjudicator"]
    if (why := person_name_problem(who, lib_settings)) is not None:
        acc.errors.append(f"adjudication: adjudicator {who!r} {why}")
    elif person_key(who) in {person_key(n) for n in annotators}:
        acc.errors.append("adjudication: người giải quyết phải là thành viên thứ ba (spec 15.2)")
    who_ok = not acc.errors
    items = raw["decisions"] or []
    if not isinstance(items, list):
        return None, {}, ["adjudication: decisions phải là danh sách"]
    by_id = {c.case_id: c for c in cases}
    out: dict[str, Decision] = {}
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            acc.errors.append(f"adjudication #{i}: phải là mapping")
            continue
        cid = item.get("case_id")
        where = f"adjudication [{cid}]"
        if set(item) - {"case_id", "reference_set", "statuses", "note"}:
            acc.errors.append(f"{where}: trường lạ")
        case = by_id.get(cid)
        if case is None or cid in out:
            acc.errors.append(f"{where}: case_id không có trong danh sách ca hoặc bị lặp")
            continue
        n_err = len(acc.errors)
        note = item.get("note")
        if not isinstance(note, str) or not note.strip():
            acc.errors.append(f"{where}: cần note ghi lý do quyết định (spec 15.2: ghi lại)")
            note = ""
        ref = None
        if "reference_set" in item:
            ref = _check_reference(item["reference_set"], case, where, taxonomy, settings, acc)
        statuses = _check_statuses(item.get("statuses"), case, where, taxonomy, acc)
        if len(acc.errors) == n_err:  # quyết định có lỗi KHÔNG được dùng (như nhãn có lỗi)
            out[cid] = Decision(cid, ref, statuses, note)
    if not who_ok:  # người giải quyết không hợp lệ: không dùng quyết định nào
        out = {}
    return (who.strip() if isinstance(who, str) else None), out, acc.errors


def final_references(
    cases: Sequence[CaseRecord], a: LabelSet, b: LabelSet, decisions: Mapping[str, Decision]
) -> tuple[tuple[CaseRecord, ...], list[str]]:
    """Ca kèm đáp án cuối. Ca chưa đủ hai nhãn, hoặc bất đồng chưa được giải quyết: báo lỗi."""
    dis = disagreements(a, b)
    out: list[CaseRecord] = []
    errs: list[str] = []
    for c in cases:
        la, lb = a.labels.get(c.case_id), b.labels.get(c.case_id)
        if la is None or lb is None:
            errs.append(f"{c.case_id}: chưa đủ hai người gán")
            continue
        ref, statuses = la.reference_set, dict(la.statuses)
        if c.case_id in dis:
            d = decisions.get(c.case_id)
            if d is None:
                errs.append(f"{c.case_id}: bất đồng chưa giải quyết ({'; '.join(dis[c.case_id])})")
                continue
            if la.reference_set != lb.reference_set:
                if d.reference_set is None:
                    errs.append(
                        f"{c.case_id}: bất đồng đáp án nhưng quyết định thiếu reference_set"
                    )
                    continue
                ref = d.reference_set
            conflict = {
                h for h in set(la.statuses) | set(lb.statuses)
                if la.statuses.get(h) != lb.statuses.get(h)
            }  # fmt: skip
            undecided = sorted(conflict - set(d.statuses))
            if undecided:
                errs.append(f"{c.case_id}: trạng thái bất đồng chưa quyết: {undecided}")
                continue
            statuses = {h: v for h, v in la.statuses.items() if h not in conflict}
            statuses.update(d.statuses)
        seen: set[str] = set()
        srcs = []
        for s in (*la.sources, *lb.sources):
            k = json.dumps(s, sort_keys=True, ensure_ascii=False)
            if k not in seen:
                seen.add(k)
                srcs.append(s)
        out.append(
            replace(
                c,
                reference_set=tuple(sorted(ref)),
                reference_sources=tuple(srcs),
                reference_statuses=statuses,
            )
        )
    return tuple(out), errs
