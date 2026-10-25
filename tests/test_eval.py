"""Test Phase 12a: danh sách ca, nhãn, kappa, giải quyết bất đồng, freeze, ánh xạ mù, ca sai.
Mọi ca/nhãn ở đây là dữ liệu giả (AAA/BBB); không có nội dung đánh giá thật."""

import copy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import CaseRecord
from steel_xai.eval import (
    BlindItem,
    CaseContext,
    FreezeError,
    LabelSet,
    RagIndex,
    build_manifest,
    cohen_kappa,
    diff_manifest,
    disagreements,
    failure_rows,
    final_references,
    load_cases,
    load_eval_settings,
    load_label_sets,
    make_blind_sheet,
    manifest_sha,
    mapping_kappa,
    person_key,
    read_candidates,
    reference_agreement,
    reference_kappa,
    require_frozen,
    source_problem,
    status_kappa,
    summarize_failures,
    taxonomy_from_library,
    unblind,
    validate_adjudication,
    validate_cases,
    validate_failures,
    validate_label_file,
    validate_mapping,
    write_freeze,
)
from steel_xai.eval.labels import Label
from steel_xai.hypotheses import load_library
from steel_xai.hypotheses.library import load_library_settings

REPO = Path(__file__).resolve().parents[1]
CFG = load_config("eval")
LIB_S = load_library_settings()
RAG = RagIndex(
    frozenset({"AAA_financial_statements_2021_consolidated"}),
    frozenset({("AAA", 2021), ("BBB", 2022)}),
)
TAX = {
    "inventory_increase": {"inv_up": "up", "inv_down": "down", "inv_both": "both"},
    "gross_margin_change": {"gm_price": "both"},
}
A1, A2, A3 = "Nguyễn Văn An", "Trần Thị Bình", "Lê Văn Cường"


def settings(tmp_path=None, **changes):
    cfg = copy.deepcopy(CFG)
    cfg["split"] = {"dev": 1, "test": 2}
    if tmp_path is not None:
        cfg["paths"] = {
            "cases": str(tmp_path / "cases.yaml"),
            "labels_dir": str(tmp_path / "labels"),
            "adjudication": str(tmp_path / "adjudication.yaml"),
        }
        cfg["freeze_file"] = str(tmp_path / "configs" / "eval_freeze.json")
    cfg.update(changes)
    return load_eval_settings(cfg)


S = settings()
CTX = CaseContext(
    companies=frozenset({"AAA", "BBB"}),
    anomaly_types=frozenset(TAX),
    start_year=2016,
    end_year=2023,
    reference_only_from_year=2024,
    known={
        "AAA|FY2021|inventory_increase|up": True,
        "BBB|FY2022|inventory_increase|down": True,
        "AAA|FY2022|gross_margin_change|down": True,
        "BBB|FY2019|gross_margin_change|up": False,
    },
)


def case_rows():
    return [
        {"case_id": "C1", "anomaly_id": "AAA|FY2021|inventory_increase|up", "split": "dev"},
        {"case_id": "C2", "anomaly_id": "BBB|FY2022|inventory_increase|down", "split": "test"},
        {"case_id": "C3", "anomaly_id": "AAA|FY2022|gross_margin_change|down", "split": "test"},
    ]


CASES = validate_cases({"cases": case_rows()}, S, CTX).cases
SRC = {
    "title": "Báo cáo thường niên 2022 của công ty",
    "date": "2023-04-01",
    "url": "https://x.vn/a",
}


def label(case_id, ref, statuses=None, note="", sources=None):
    return {
        "case_id": case_id,
        "reference_set": ref,
        "statuses": statuses or {},
        "sources": [SRC] if sources is None else sources,
        "note": note,
    }


def good_file(who=A1):
    return {
        "annotator": who,
        "labels": [
            label("C1", ["inv_up"]),
            label("C2", ["inv_down"], {"inv_down": "SUPPORTED"}),
            label("C3", ["gm_price", "other"], note="giả thuyết ngoài thư viện: chi phí logistics"),
        ],
    }


def lf(raw, name="a.yaml"):
    return validate_label_file(raw, CASES, TAX, S, LIB_S, RAG, name)


def labelset(who, refs, statuses=None):
    statuses = statuses or {}
    return LabelSet(
        who,
        {c: Label(c, frozenset(r), statuses.get(c, {}), (SRC,)) for c, r in refs.items()},
    )


# ---------------- config, contract, mẫu


def test_repo_eval_config_loads_and_templates_exist():
    s = load_eval_settings()
    # nhóm giảm từ 5/13 (spec) còn 4/7 ca, ghi ở DEVIATIONS_FROM_MIDTERM.md (2026-10-25)
    assert s.split == {"dev": 4, "test": 7} and s.annotators == 2 and s.other_id == "other"
    assert s.failure_causes == (
        "retrieval_miss", "rule_wrong", "llm_classification_wrong", "ambiguous_reference",
        "data_missing",
    )  # fmt: skip
    assert "khảo sát khả thi" in s.disclaimer
    t = REPO / "data" / "eval" / "templates"
    for name in ("cases.yaml", "labels.yaml", "adjudication.yaml", "blind_mapping.csv",
                 "failure_analysis.csv", "README.md"):  # fmt: skip
        assert (t / name).is_file(), name


@pytest.mark.parametrize(
    ("key", "value"),
    [("split", {"dev": 5}), ("annotators", 1), ("other_id", ""), ("failure_causes", []),
     ("disclaimer", ""), ("paths", None), ("min_unfreeze_reason_chars", 0)],
)  # fmt: skip
def test_bad_eval_config_is_rejected(key, value):
    cfg = copy.deepcopy(CFG)
    cfg[key] = value
    with pytest.raises(ConfigError):
        load_eval_settings(cfg)


def test_case_record_roundtrip_and_split_is_checked():
    c = CaseRecord("C1", "test", "AAA|FY2021|inventory_increase|up", ("inv_up",), (SRC,),
                   {"inv_up": "PARTIAL"}, "n")  # fmt: skip
    assert CaseRecord.from_dict(c.to_dict()) == c
    assert (c.anomaly_type, c.direction) == ("inventory_increase", "up")
    with pytest.raises(ValueError, match="split"):
        CaseRecord("C1", "train", "AAA|FY2021|inventory_increase|up")


def test_repo_library_gives_a_taxonomy_of_reviewed_hypotheses_only():
    lib = load_library()
    tax = taxonomy_from_library([*lib.reviewed, *lib.drafts])
    assert set(tax) <= set(load_config("anomaly")["anomaly_types"])
    assert sum(len(v) for v in tax.values()) == len(lib.reviewed)


# ---------------- danh sách ca


def test_valid_cases_pass_and_counts_are_strict_only_when_freezing():
    cc = validate_cases({"cases": case_rows()}, S, CTX)
    assert cc.ok and [c.case_id for c in cc.cases] == ["C1", "C2", "C3"]
    rows = case_rows()[:2]
    loose = validate_cases({"cases": rows}, S, CTX)
    assert loose.ok and any("số ca test" in w for w in loose.warnings)
    strict = validate_cases({"cases": rows}, S, CTX, strict_counts=True)
    assert not strict.ok and any("số ca test" in e for e in strict.errors)


@pytest.mark.parametrize(
    ("change", "msg"),
    [
        ({"case_id": "C 1"}, "case_id"),
        ({"split": "train"}, "split"),
        ({"anomaly_id": "AAA|2021|inventory_increase|up"}, "FYyyyy"),
        ({"anomaly_id": "ZZZ|FY2021|inventory_increase|up"}, "dataset.companies"),
        ({"anomaly_id": "AAA|FY2015|inventory_increase|up"}, "cửa sổ"),
        ({"anomaly_id": "AAA|FY2021|revenue_drop|up"}, "anomaly_types"),
        ({"anomaly_id": "AAA|FY2021|inventory_increase|flat"}, "up | down"),
        ({"anomaly_id": "AAA|FY2020|inventory_increase|up"}, "anomaly_candidates"),
        ({"extra": 1}, "trường lạ"),
    ],
)
def test_bad_case_is_rejected(change, msg):
    rows = case_rows()
    rows[0] = {**rows[0], **change}
    cc = validate_cases({"cases": rows}, S, CTX)
    assert not cc.ok and any(msg in e for e in cc.errors), cc.errors


def test_reference_only_years_cannot_be_cases():
    ctx = replace(CTX, end_year=2025, known=None)
    rows = case_rows()
    rows[0]["anomaly_id"] = "AAA|FY2024|inventory_increase|up"
    cc = validate_cases({"cases": rows}, S, ctx)
    assert any("nguồn đáp án" in e for e in cc.errors)
    assert any("anomaly_candidates.csv" in w for w in cc.warnings)  # không có file: cảnh báo


def test_duplicates_empty_and_non_candidate():
    rows = case_rows() + [{**case_rows()[0], "case_id": "C9"}]
    cc = validate_cases({"cases": rows}, S, CTX)
    assert any("anomaly_id lặp" in e for e in cc.errors)
    rows = case_rows() + [{**case_rows()[0]}]
    assert any("case_id lặp" in e for e in validate_cases({"cases": rows}, S, CTX).errors)
    assert not validate_cases({"cases": []}, S, CTX).ok
    assert not validate_cases({"ca": []}, S, CTX).ok
    rows = case_rows()
    rows[2] = {"case_id": "C3", "anomaly_id": "BBB|FY2019|gross_margin_change|up", "split": "test"}
    cc = validate_cases({"cases": rows}, S, CTX)
    assert cc.ok and any("candidate=false" in w for w in cc.warnings)


def test_load_cases_reports_missing_file_and_reads_candidates_csv(tmp_path):
    s = settings(tmp_path)
    assert not load_cases(s, CTX).ok
    (tmp_path / "cases.yaml").write_text(yaml.safe_dump({"cases": case_rows()}), encoding="utf-8")
    assert load_cases(s, CTX).ok
    p = tmp_path / "cand.csv"
    p.write_text(
        "﻿rank,company,period,type,direction,candidate\n"
        "1,AAA,FY2021,inventory_increase,up,true\n2,BBB,FY2019,gross_margin_change,down,false\n",
        encoding="utf-8",
    )
    assert read_candidates(p) == {
        "AAA|FY2021|inventory_increase|up": True,
        "BBB|FY2019|gross_margin_change|down": False,
    }


# ---------------- kappa


def test_cohen_kappa_known_values():
    # bảng 2x2 kinh điển: yes/yes 20, yes/no 5, no/yes 10, no/no 15 → po=0,7, pe=0,5, kappa=0,4
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    k = cohen_kappa(a, b)
    assert k.n == 50 and k.observed == pytest.approx(0.7) and k.expected == pytest.approx(0.5)
    assert k.kappa == pytest.approx(0.4)
    assert cohen_kappa([1, 0, 1], [1, 0, 1]).kappa == pytest.approx(1.0)
    assert cohen_kappa([1, 0], [0, 1]).kappa == pytest.approx(-1.0)
    same = cohen_kappa([1, 1, 1], [1, 1, 1])
    assert same.kappa is None and same.observed == 1.0 and "không xác định" in same.note
    assert cohen_kappa([], []).kappa is None
    with pytest.raises(ValueError):
        cohen_kappa([1], [1, 0])


def test_reference_kappa_uses_hypotheses_applicable_to_the_case_direction_plus_other():
    a = labelset(A1, {"C1": ["inv_up"], "C3": ["gm_price"]})
    b = labelset(A2, {"C1": ["inv_up", "inv_both"], "C3": ["gm_price"], "C2": ["inv_down"]})
    k = reference_kappa(a, b, CASES, TAX, "other")
    # C1 (up): inv_up, inv_both, other; bỏ inv_down (ngược hướng, không ai chọn); lệch 1
    # C3: gm_price, other; khớp hết. C2 chỉ một người gán → bỏ
    assert k.n == 5 and k.observed == pytest.approx(4 / 5)
    exp = (2 * 3 + 3 * 2) / 25  # a: 2 có/3 không; b: 3 có/2 không
    assert k.expected == pytest.approx(exp)
    assert k.kappa == pytest.approx((4 / 5 - exp) / (1 - exp))
    # giả thuyết ngược hướng mà một người vẫn chọn: giữ lại (bất đồng thật)
    b2 = labelset(A2, {"C1": ["inv_up", "inv_down"], "C3": ["gm_price"]})
    assert reference_kappa(a, b2, CASES, TAX, "other").n == 6


def test_kappa_does_not_grow_with_opposite_direction_hypotheses():
    a = labelset(A1, {"C1": ["inv_up"]})
    b = labelset(A2, {"C1": ["inv_both"]})
    extra = {f"inv_down_{i}": "down" for i in range(20)}
    big = {**TAX, "inventory_increase": {**TAX["inventory_increase"], **extra}}
    assert reference_kappa(a, b, CASES, big, "other") == reference_kappa(a, b, CASES, TAX, "other")


def test_reference_agreement_per_case():
    a = labelset(A1, {"C1": ["inv_up"], "C3": ["gm_price"]})
    b = labelset(A2, {"C1": ["inv_up", "inv_both"], "C3": ["gm_price"]})
    r = reference_agreement(a, b)
    assert r["cases"] == 2 and r["exact_match"] == 0.5 and r["mean_jaccard"] == pytest.approx(0.75)
    assert reference_agreement(a, labelset(A2, {}))["exact_match"] is None


def test_status_kappa_only_on_pairs_both_labelled():
    a = labelset(A1, {"C2": ["inv_down"]}, {"C2": {"inv_down": "SUPPORTED", "inv_up": "PARTIAL"}})
    b = labelset(A2, {"C2": ["inv_down"]}, {"C2": {"inv_down": "PARTIAL"}})
    k = status_kappa(a, b)
    assert k.n == 1 and k.observed == 0.0


# ---------------- file nhãn


def test_good_label_file_passes():
    ls, errs, warns = lf(good_file())
    assert errs == [] and warns == []
    assert ls.annotator == A1 and ls.labels["C3"].reference_set == {"gm_price", "other"}
    assert ls.labels["C2"].statuses == {"inv_down": "SUPPORTED"}


@pytest.mark.parametrize(
    ("mutate", "msg"),
    [
        (lambda f: f.update(annotator="Claude Opus"), "AI"),
        (lambda f: f.update(annotator="TBD"), "giữ chỗ"),
        (lambda f: f.update(annotator="<Họ và tên>"), "giữ chỗ"),
        (lambda f: f.update(annotator="Họ và tên"), "giữ chỗ"),
        (lambda f: f["labels"][0].update(reference_set=[]), "không rỗng"),
        (lambda f: f["labels"][0].update(reference_set=["gm_price"]), "không phải giả thuyết"),
        (lambda f: f["labels"][0].update(reference_set=["inv_up", "inv_up"]), "lặp"),
        (lambda f: f["labels"][2].update(note=""), "note phải mô tả"),
        (lambda f: f["labels"][0].update(statuses={"inv_up": "MAYBE"}), "statuses"),
        (lambda f: f["labels"][0].update(statuses={"other": "SUPPORTED"}), "statuses"),
        (lambda f: f["labels"][0].update(sources=[]), "ít nhất một nguồn"),
        (lambda f: f["labels"][0].update(case_id="C99"), "không có trong danh sách ca"),
        (lambda f: f["labels"].append(label("C1", ["inv_up"])), "hai lần"),
        (lambda f: f["labels"][0].update(extra=1), "trường lạ"),
    ],
)
def test_bad_label_file_is_rejected(mutate, msg):
    f = good_file()
    mutate(f)
    _, errs, _ = lf(f)
    assert any(msg in e for e in errs), errs


def test_direction_mismatch_and_unlabelled_cases_are_warnings():
    f = good_file()
    f["labels"][0]["reference_set"] = ["inv_down"]  # ca C1 hướng up
    f["labels"].pop()
    _, errs, warns = lf(f)
    assert errs == []
    assert any("direction" in w for w in warns) and any("chưa gán 1 ca: C3" in w for w in warns)


@pytest.mark.parametrize(
    ("src", "msg"),
    [
        ({**SRC, "doc_id": "AAA_financial_statements_2021_consolidated"}, "thuộc kho RAG"),
        ({**SRC, "url": "data/raw/AAA/2021/AAA_financial_statements_2021_consolidated/x.txt"},
         "nhắc tài liệu trong kho RAG"),
        ({"title": "Xem aaa_financial_statements_2021_consolidated", "date": "2022",
          "url": "https://x"}, "kho RAG"),
        ({"title": "ngắn", "date": "2023", "url": "https://x"}, "title"),
        ({"title": "Báo cáo thường niên", "url": "https://x"}, "date"),
        ({"title": "Báo cáo thường niên", "date": "năm ngoái", "url": "https://x"}, "date"),
        ({"title": "Báo cáo thường niên", "date": "2023"}, "url hoặc doc_id"),
        ({**SRC, "doc_id": ["x"]}, "phải là chuỗi"),
        ({**SRC, "author": "x"}, "trường lạ"),
        ("https://x", "mapping"),
    ],
)  # fmt: skip
def test_label_sources_must_be_independent_of_the_rag_corpus(src, msg):
    err, _ = source_problem(src, S, RAG)
    assert err is not None and msg in err, err


def test_sources_outside_the_rag_corpus_are_fine():
    assert source_problem(SRC, S, RAG) == (None, None)
    later = {**SRC, "doc_id": "AAA_financial_statements_2024_consolidated"}  # năm tham chiếu
    assert source_problem(later, S, RAG) == (None, None)
    for d in ("2024", "2024-03", "2024-03-20", "20/3/2024", "3/2024"):
        assert source_problem({**SRC, "date": d}, S, RAG)[0] is None, d


FS21 = {"title": "BCTC hợp nhất 2021", "date": "2022", "url": "https://aaa.vn/bctc.pdf"}


@pytest.mark.parametrize(
    ("src", "company", "warned"),
    [
        (FS21, "AAA", True),
        ({**FS21, "title": "Báo cáo tài chính BBB năm 2022", "url": "https://x"}, None, True),
        ({**FS21, "title": "BCTC hợp nhất 2024"}, "AAA", False),
        ({**FS21, "title": "Báo cáo thường niên 2021", "url": "https://x"}, "AAA", False),
        ({**FS21, "url": "https://x"}, "BBB", False),  # BBB 2021 không có trong kho
    ],
)  # fmt: skip
def test_financial_statement_of_a_company_year_in_the_rag_corpus_is_flagged(src, company, warned):
    err, warn = source_problem(src, S, RAG, company)
    assert err is None and (warn is not None) == warned, warn


def test_person_key_normalizes_spacing_case_and_accents():
    assert (
        person_key("Nguyễn  Văn An") == person_key("nguyen van an") == person_key("NGUYỄN VĂN AN")
    )
    assert person_key("Đỗ Đức") == "do duc"
    assert person_key(A1) != person_key(A2)


def test_load_label_sets_needs_exactly_two_independent_files(tmp_path):
    s = settings(tmp_path)
    d = tmp_path / "labels"
    d.mkdir()
    (d / "an.yaml").write_text(yaml.safe_dump(good_file(A1), allow_unicode=True), encoding="utf-8")
    sets, errs, _ = load_label_sets(CASES, TAX, s, LIB_S, RAG)
    assert len(sets) == 1 and any("cần đúng 2 file" in e for e in errs)
    (d / "an2.yaml").write_text(yaml.safe_dump(good_file(A1), allow_unicode=True), encoding="utf-8")
    _, errs, _ = load_label_sets(CASES, TAX, s, LIB_S, RAG)
    assert any("cùng người gán" in e for e in errs)
    (d / "an2.yaml").write_text(
        yaml.safe_dump(good_file("nguyen  van an"), allow_unicode=True), encoding="utf-8"
    )
    _, errs, _ = load_label_sets(CASES, TAX, s, LIB_S, RAG)
    assert any("cùng người gán" in e for e in errs)  # khác dấu/khoảng trắng: vẫn một người
    (d / "x.yml").write_text("a: 1", encoding="utf-8")
    _, errs, _ = load_label_sets(CASES, TAX, s, LIB_S, RAG)
    assert any("đuôi .yaml" in e for e in errs)
    (d / "x.yml").unlink()
    (d / "an2.yaml").write_text(yaml.safe_dump(good_file(A2), allow_unicode=True), encoding="utf-8")
    sets, errs, _ = load_label_sets(CASES, TAX, s, LIB_S, RAG)
    assert errs == [] and {x.annotator for x in sets} == {A1, A2}


# ---------------- bất đồng, đáp án cuối


def adj(decisions, who=A3):
    return validate_adjudication(
        {"adjudicator": who, "decisions": decisions}, CASES, [A1, A2], TAX, S, LIB_S
    )


def test_agreed_cases_need_no_adjudication_and_sources_are_merged():
    a = labelset(A1, {"C1": ["inv_up"], "C2": ["inv_down"], "C3": ["gm_price"]})
    b = labelset(A2, {"C1": ["inv_up"], "C2": ["inv_down"], "C3": ["gm_price"]})
    b = replace(
        b,
        labels={
            **b.labels,
            "C1": replace(b.labels["C1"], sources=(SRC, {**SRC, "url": "https://y"})),
        },
    )
    assert disagreements(a, b) == {}
    final, errs = final_references(CASES, a, b, {})
    assert errs == [] and [c.reference_set for c in final] == [
        ("inv_up",),
        ("inv_down",),
        ("gm_price",),
    ]
    assert len(final[0].reference_sources) == 2  # gộp, bỏ trùng


def test_disagreements_need_a_third_person_decision():
    a = labelset(A1, {"C1": ["inv_up"], "C2": ["inv_down"], "C3": ["gm_price"]},
                 {"C2": {"inv_down": "SUPPORTED"}})  # fmt: skip
    b = labelset(A2, {"C1": ["inv_up", "other"], "C2": ["inv_down"], "C3": ["gm_price"]},
                 {"C2": {"inv_down": "PARTIAL"}})  # fmt: skip
    dis = disagreements(a, b)
    assert set(dis) == {"C1", "C2"} and "trạng thái inv_down" in dis["C2"][0]
    _, errs = final_references(CASES, a, b, {})
    assert len(errs) == 2 and all("chưa giải quyết" in e for e in errs)
    who, dec, errs = adj([
        {"case_id": "C1", "reference_set": ["inv_up"], "note": "other không đủ nguồn"},
        {"case_id": "C2", "statuses": {"inv_down": "PARTIAL"}, "note": "thiếu thuyết minh"},
    ])  # fmt: skip
    assert who == A3 and errs == []
    final, errs = final_references(CASES, a, b, dec)
    assert errs == []
    by = {c.case_id: c for c in final}
    assert by["C1"].reference_set == ("inv_up",) and by["C2"].reference_statuses == {
        "inv_down": "PARTIAL"
    }


def test_adjudication_rules():
    assert any("thứ ba" in e for e in adj([], who=A1)[2])
    assert any("AI" in e for e in adj([], who="ChatGPT Bot")[2])
    assert any("note" in e for e in adj([{"case_id": "C1", "reference_set": ["inv_up"]}])[2])
    assert any("không có trong" in e for e in adj([{"case_id": "X", "note": "n"}])[2])
    wrong_type = {"case_id": "C1", "reference_set": ["gm_price"], "note": "n"}
    assert any("không phải giả thuyết" in e for e in adj([wrong_type])[2])
    a = labelset(A1, {"C1": ["inv_up"], "C2": ["inv_down"], "C3": ["gm_price"]})
    b = labelset(A2, {"C1": ["inv_both"], "C2": ["inv_down"], "C3": ["gm_price"]})
    _, dec, _ = adj([{"case_id": "C1", "statuses": {}, "note": "chỉ ghi chú"}])
    _, errs = final_references(CASES, a, b, dec)
    assert any("thiếu reference_set" in e for e in errs)
    a2 = labelset(A1, {"C1": ["inv_up"]})
    _, errs = final_references(CASES, a2, b, {})
    assert any("chưa đủ hai người" in e for e in errs)


def test_invalid_adjudication_never_reaches_the_final_reference():
    a = labelset(A1, {"C1": ["inv_up"], "C2": ["inv_down"], "C3": ["gm_price"]})
    b = labelset(A2, {"C1": ["inv_both"], "C2": ["inv_down"], "C3": ["gm_price"]})
    _, dec, errs = adj([{"case_id": "C1", "reference_set": ["typo_id"]}])  # id sai + thiếu note
    assert len(errs) == 2 and dec == {}
    final, ferrs = final_references(CASES, a, b, dec)
    assert any("C1" in e and "chưa giải quyết" in e for e in ferrs)
    assert "C1" not in {c.case_id for c in final}
    ok = [{"case_id": "C1", "reference_set": ["inv_up"], "note": "theo thuyết minh"}]
    _, dec, errs = adj(ok, who="nguyễn văn an")  # trùng người gán (khác hoa/thường)
    assert any("thứ ba" in e for e in errs) and dec == {}
    _, dec, errs = adj(ok, who="<Họ và tên người thứ ba>")
    assert any("giữ chỗ" in e for e in errs) and dec == {}


# ---------------- freeze


def freeze_root(tmp_path):
    (tmp_path / "configs").mkdir(exist_ok=True)
    (tmp_path / "hypotheses").mkdir(exist_ok=True)
    (tmp_path / "configs" / "a.yaml").write_bytes(b"x: 1\n")
    (tmp_path / "hypotheses" / "h.yaml").write_bytes(b"h: 1\n")
    return tmp_path


def prompts(v="p1"):
    return lambda: {"llm_evidence": v, "report": "r1"}


REFS = {"C1": ("inv_up",), "C2": ("inv_down",), "C3": ("gm_price",)}
FINAL = tuple(replace(c, reference_set=REFS[c.case_id]) for c in CASES)
REASON = "sửa lỗi parse số trong bảng thuyết minh sau khi đóng băng"
GLOBS = ["configs/*.yaml", "hypotheses/*.yaml"]


def frozen(tmp_path, cases=FINAL):
    root = freeze_root(tmp_path)
    s = settings(tmp_path, freeze_globs=GLOBS)
    write_freeze(s.freeze_file, build_manifest(s, cases, root=root, prompts=prompts()), A1)
    return root, s


def test_freeze_roundtrip_detects_changes_but_not_line_endings(tmp_path):
    root, s = frozen(tmp_path)
    m = build_manifest(s, FINAL, root=root, prompts=prompts())
    assert set(m["files"]) == {"configs/a.yaml", "hypotheses/h.yaml"}  # bỏ chính eval_freeze.json
    assert [c["case_id"] for c in m["test"]["cases"]] == ["C2", "C3"]
    chk = require_frozen(s, FINAL, root=root, prompts=prompts())
    assert chk.ok and chk.frozen_sha256 == chk.current_sha256 == manifest_sha(m)
    (root / "configs" / "a.yaml").write_bytes(b"x: 1\r\n")  # chỉ đổi xuống dòng
    assert require_frozen(s, FINAL, root=root, prompts=prompts()).ok
    (root / "configs" / "a.yaml").write_bytes(b"x: 2\n")
    with pytest.raises(FreezeError, match="file đã đổi: configs/a.yaml"):
        require_frozen(s, FINAL, root=root, prompts=prompts())


def test_freeze_detects_prompt_case_reference_and_file_set_changes(tmp_path):
    root, s = frozen(tmp_path)
    m = build_manifest(s, FINAL, root=root, prompts=prompts())

    def diff(cases=FINAL, p="p1"):
        return diff_manifest(m, build_manifest(s, cases, root=root, prompts=prompts(p)))

    assert diff(p="p2") == ["prompt đã đổi: llm_evidence"]
    moved = [replace(c, split="dev") if c.case_id == "C2" else c for c in FINAL]
    assert {"danh sách ca test đã đổi", "danh sách ca dev đã đổi"} <= set(diff(moved))
    # sửa đáp án/trạng thái chuẩn sau khi đóng băng (ví dụ cho khớp top-3 của C)
    relabel = [replace(c, reference_set=("inv_both",)) if c.case_id == "C2" else c for c in FINAL]
    assert diff(relabel) == ["đáp án tham chiếu test đã đổi"]
    st = {"inv_down": "PARTIAL"}
    restat = [replace(c, reference_statuses=st) if c.case_id == "C2" else c for c in FINAL]
    assert diff(restat) == ["đáp án tham chiếu test đã đổi"]
    (root / "configs" / "b.yaml").write_bytes(b"y: 1\n")
    (root / "hypotheses" / "h.yaml").unlink()
    assert diff() == ["file mới (chưa đóng băng): configs/b.yaml", "file đã xóa: hypotheses/h.yaml"]


def test_cannot_freeze_without_final_references_or_overwrite_silently(tmp_path):
    root = freeze_root(tmp_path)
    s = settings(tmp_path, freeze_globs=GLOBS)
    with pytest.raises(FreezeError, match="chưa có đáp án cuối"):
        write_freeze(s.freeze_file, build_manifest(s, CASES, root=root, prompts=prompts()), A1)
    m = build_manifest(s, FINAL, root=root, prompts=prompts())
    first = write_freeze(s.freeze_file, m, A1)
    assert first["history"] == []
    with pytest.raises(FreezeError, match="đã có"):
        write_freeze(s.freeze_file, m, A2)
    (root / "configs" / "a.yaml").write_bytes(b"x: 3\n")
    m2 = build_manifest(s, FINAL, root=root, prompts=prompts())
    second = write_freeze(s.freeze_file, m2, A2, overwrite=True)
    assert second["history"] == [
        {"frozen_at": first["frozen_at"], "frozen_by": A1, "manifest_sha256": manifest_sha(m)}
    ]
    assert second["manifest_sha256"] == manifest_sha(m2) != manifest_sha(m)


def test_never_run_test_without_a_freeze_and_reasons_must_say_something(tmp_path):
    root = freeze_root(tmp_path)
    s = settings(tmp_path, freeze_globs=GLOBS)
    with pytest.raises(FreezeError, match="chưa đóng băng"):
        require_frozen(s, FINAL, REASON, root=root, prompts=prompts())  # lý do không cứu được
    write_freeze(s.freeze_file, build_manifest(s, FINAL, root=root, prompts=prompts()), A1)
    (root / "configs" / "a.yaml").write_bytes(b"x: 9\n")
    for bad in (None, "ngắn", "aaaaaaaaaaaaaaaaaaaa", "sửa sửa sửa sửa sửa"):
        with pytest.raises(FreezeError, match="--unfreeze-reason"):
            require_frozen(s, FINAL, bad, root=root, prompts=prompts())
    chk = require_frozen(s, FINAL, REASON, root=root, prompts=prompts())
    d = chk.to_dict()
    assert not chk.ok and d["unfreeze_reason"] == REASON and d["differences"]
    assert d["frozen_sha256"] != d["current_sha256"]


def test_config_dir_env_var_cannot_bypass_the_freeze(tmp_path, monkeypatch):
    root, s = frozen(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("STEEL_XAI_CONFIG_DIR", str(other))
    with pytest.raises(FreezeError, match="STEEL_XAI_CONFIG_DIR"):
        require_frozen(s, FINAL, REASON, root=root, prompts=prompts())
    monkeypatch.setenv("STEEL_XAI_CONFIG_DIR", str(root / "configs"))  # đúng configs/ thì được
    assert require_frozen(s, FINAL, root=root, prompts=prompts()).ok


def test_freeze_needs_files_to_hash(tmp_path):
    s = settings(tmp_path)
    with pytest.raises(FreezeError, match="không khớp file nào"):
        build_manifest(s, CASES, root=tmp_path, prompts=prompts())


def test_repo_manifest_covers_code_data_configs_library_and_prompts():
    m = build_manifest(load_eval_settings(), CASES)
    f = m["files"]
    for must in (
        "configs/eval.yaml",
        "hypotheses/library_draft.yaml",
        "pyproject.toml",
        "src/steel_xai/verification/verdict.py",
        "src/steel_xai/report/checks.py",
        "scripts/run_report.py",
        "data/external/hrc_price.csv",
    ):
        assert must in f, must
    assert "configs/eval_freeze.json" not in f and not any("__pycache__" in k for k in f)
    assert set(m["prompts"]) == {"baselines", "llm_evidence", "report"}
    assert all(m["prompts"].values())


def test_llm_prompt_fingerprint_covers_user_prompt_wording(monkeypatch):
    from steel_xai.llm import prompts as P

    before = P.prompt_fingerprint()
    monkeypatch.setitem(P._WANTED, "support", "bằng chứng ủng hộ (đã sửa câu chữ)")
    assert P.prompt_fingerprint() != before


# ---------------- ánh xạ mù


ITEMS = [
    BlindItem("C1", "A", "run1", 1, "Tồn kho tăng do tích trữ nguyên liệu"),
    BlindItem("C1", "B", "run1", 1, "Tồn kho tăng do tích trữ nguyên liệu"),
    BlindItem("C1", "A", "run1", 2, "Nhu cầu yếu"),
    BlindItem("C3", "B", "run2", 1, "Giá bán giảm"),
]
SHEET_COLS = {"item_id", "case_id", "anomaly_type", "hypothesis_text"}


def test_blind_sheet_hides_configuration_with_a_secret_random_salt():
    sheet, key = make_blind_sheet(ITEMS, CASES)
    assert all(set(r) == SHEET_COLS for r in sheet)
    assert [r["case_id"] for r in sheet] == ["C1", "C1", "C1", "C3"]
    c1 = [r["item_id"] for r in sheet if r["case_id"] == "C1"]
    assert c1 == sorted(c1)  # thứ tự theo id băm, không theo cấu hình
    assert len({r["item_id"] for r in sheet}) == 4  # cùng văn bản, khác cấu hình → khác id
    salt = key[0]["salt"]
    assert len(salt) == 32 and all(k["salt"] == salt for k in key)
    again, _ = make_blind_sheet(ITEMS, CASES)  # mặc định: salt mới mỗi lần
    assert {r["item_id"] for r in again}.isdisjoint({r["item_id"] for r in sheet})
    assert make_blind_sheet(ITEMS, CASES, salt) == (sheet, key)  # tái lập từ salt trong key
    assert {(k["config"], k["rank"]) for k in key} == {("A", 1), ("B", 1), ("A", 2)}
    with pytest.raises(ValueError, match="salt"):
        make_blind_sheet(ITEMS, CASES, "s")
    with pytest.raises(ValueError, match="case_id"):
        make_blind_sheet([BlindItem("C9", "A", "r", 1, "x")], CASES)
    with pytest.raises(ValueError, match="rỗng"):
        make_blind_sheet([BlindItem("C1", "A", "r", 1, " ")], CASES)
    with pytest.raises(ValueError, match="trùng"):
        make_blind_sheet([ITEMS[0], ITEMS[0]], CASES, "salt-cố-định")


def test_mapping_validation_kappa_and_unblinding():
    sheet, key = make_blind_sheet(ITEMS, CASES)
    a = [{**r, "mapped_id": "inv_up", "annotator": A1} for r in sheet]
    a[-1]["mapped_id"] = "gm_price"
    ids = [k["item_id"] for k in key]
    assert validate_mapping(a, CASES, TAX, "other", LIB_S, ids) == []
    b = [{**r, "mapped_id": x["mapped_id"], "annotator": A2} for r, x in zip(sheet, a, strict=True)]
    b[0]["mapped_id"] = "other"
    k = mapping_kappa(a, b)
    assert k.n == 4 and k.observed == pytest.approx(0.75)
    bad = [dict(r) for r in a]
    bad[0]["mapped_id"] = "gm_price"  # sai loại
    bad[1]["mapped_id"] = ""
    bad[2]["annotator"] = A2
    errs = validate_mapping(bad, CASES, TAX, "other", LIB_S)
    assert any("không phải giả thuyết" in e for e in errs)
    assert any("chưa điền" in e for e in errs) and any("một người điền" in e for e in errs)
    errs = validate_mapping(a[:-1], CASES, TAX, "other", LIB_S, ids)  # xóa mất một dòng
    assert any("thiếu item" in e for e in errs)
    joined = unblind(a, key)
    assert {(j["config"], j["mapped_id"]) for j in joined} >= {("B", "gm_price")}
    with pytest.raises(ValueError, match="key"):
        unblind([{"item_id": "Bxxx", "mapped_id": "inv_up"}], key)


# ---------------- ca sai


TOP3 = {"C1": ["inv_both"], "C2": ["inv_up", "inv_both", "other", "inv_down"], "C3": ["gm_price"]}


def test_failure_rows_validation_and_summary():
    rows = failure_rows(FINAL, TOP3)
    # C1 là dev → bỏ; C2: đáp án ở hạng 4 → sai; C3 đúng
    assert [r["case_id"] for r in rows] == ["C2"] and rows[0]["c_top3"] == "inv_up;inv_both;other"
    filled = [{**rows[0], "cause": "retrieval_miss", "annotator": A1}]
    causes = S.failure_causes
    assert validate_failures(filled, causes, LIB_S, rows) == []
    assert validate_failures([{**filled[0], "cause": "llm"}], causes, LIB_S)
    assert validate_failures([{**filled[0], "annotator": "Claude"}], causes, LIB_S)
    assert any("thiếu ca sai C2" in e for e in validate_failures([], causes, LIB_S, rows))
    extra = [*filled, {**filled[0], "case_id": "C3"}]
    assert any("không phải ca sai" in e for e in validate_failures(extra, causes, LIB_S, rows))
    md = summarize_failures(filled + [{"cause": ""}], causes, S.disclaimer)
    assert "| retrieval_miss | 1 | 50% |" in md and "chưa gán" in md and S.disclaimer in md


def test_failure_rows_never_skip_a_test_case_silently():
    with pytest.raises(ValueError, match="không có kết quả top-3"):
        failure_rows(FINAL, {"C2": ["inv_down"]})  # thiếu C3
    with pytest.raises(ValueError, match="chưa có đáp án"):
        failure_rows(CASES, {"C2": ["x"], "C3": ["y"]})
