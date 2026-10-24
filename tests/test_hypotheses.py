"""Test schema, loader, validator của thư viện giả thuyết (Phase 6); nội dung chỉ là test."""

import copy
from pathlib import Path

import pytest
import yaml

from steel_xai.config import load_config
from steel_xai.contracts import Hypothesis
from steel_xai.hypotheses import (
    HypothesisError,
    load_hypothesis_file,
    load_library,
    load_library_settings,
    validate_hypothesis,
)

S = load_library_settings()
REPO_LIBRARY = Path(__file__).resolve().parents[1] / "hypotheses"

REVIEWED = {
    "id": "test_hyp",
    "anomaly_type": "inventory_increase",
    "claim": "Giả thuyết thử.",
    "business_rationale": "Lý do thử.",
    "expected_quantitative_evidence": ["chỉ số A tăng"],
    "expected_textual_evidence": ["đoạn văn B"],
    "contradictory_evidence": ["chỉ số A giảm"],
    "required_external_data": [],
    "critical_missing_evidence": ["thiếu liên kết trực tiếp"],
    "sources": ["Báo cáo ngành X, 2023"],
    "status": "reviewed",
    "direction": "up",
    "reviewed_by": "Nguyễn Văn A",
    "checks": [
        {
            "id": "a_up",
            "kind": "quant",
            "importance": "critical",
            "polarity": "support",
            "tool": "growth_yoy",
            "params": {"metric": "capex"},
            "pass_when": {"op": ">", "value": 0},
        },
        {
            "id": "b_text",
            "kind": "text",
            "importance": "supporting",
            "polarity": "contradict",
            "queries": ["dự án bị hủy"],
        },
    ],
}


def make(**changes):
    h = copy.deepcopy(REVIEWED)
    for k, v in changes.items():
        if v is ...:
            h.pop(k, None)
        else:
            h[k] = v
    return h


def errors(raw, settings=S):
    return validate_hypothesis(raw, settings)


def write(dirpath, name, raw):
    (dirpath / f"{name}.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


def test_valid_reviewed_hypothesis_has_no_errors():
    assert errors(REVIEWED) == []


def test_repo_config_and_library_load_and_only_drafts_so_far():
    lib = load_library()
    assert lib.reviewed == ()  # nội dung thư viện do nhóm soạn; Claude chỉ để bản nháp
    assert lib.drafts and all(h.status == "draft" for h in lib.drafts)
    cov = lib.coverage()
    assert set(cov) == set(load_config("anomaly")["anomaly_types"])
    assert all(c["missing"] == S.min_reviewed_per_type for c in cov.values())


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "anomaly_type",
        "claim",
        "business_rationale",
        "expected_quantitative_evidence",
        "expected_textual_evidence",
        "contradictory_evidence",
        "required_external_data",
        "critical_missing_evidence",
    ],
)
def test_missing_any_of_the_nine_required_fields_is_rejected_even_for_draft(field):
    raw = make(status="draft", **{field: ...})
    assert any(f"thiếu trường bắt buộc {field}" in e for e in errors(raw))


def test_draft_with_empty_content_is_structurally_valid():
    raw = make(
        status="draft",
        reviewed_by=None,
        claim="",
        business_rationale="",
        sources=[],
        checks=[],
        contradictory_evidence=[],
        critical_missing_evidence=[],
    )
    assert errors(raw) == []


@pytest.mark.parametrize(
    ("changes", "needle"),
    [
        ({"sources": []}, "thiếu sources"),
        ({"sources": ...}, "thiếu sources"),
        ({"reviewed_by": None}, "thiếu reviewed_by"),
        ({"reviewed_by": "  "}, "thiếu reviewed_by"),
        ({"claim": ""}, "claim không được rỗng"),
        ({"business_rationale": " "}, "business_rationale không được rỗng"),
        ({"contradictory_evidence": []}, "contradictory_evidence không được rỗng"),
        ({"critical_missing_evidence": []}, "critical_missing_evidence không được rỗng"),
        (
            {"expected_quantitative_evidence": [], "expected_textual_evidence": []},
            "ít nhất một expected",
        ),
        ({"checks": []}, "thiếu check critical"),
    ],
)
def test_reviewed_requires_content_sources_reviewer_and_checks(changes, needle):
    assert any(needle in e for e in errors(make(**changes)))


@pytest.mark.parametrize(
    "name", ["Claude", "Claude Sonnet 5.5", "GPT-4", "AI", "an LLM", "ChatGPT"]
)
def test_reviewer_must_be_human(name):
    assert any("reviewed_by" in e for e in errors(make(reviewed_by=name)))


def test_human_name_containing_ai_as_substring_is_accepted():
    assert errors(make(reviewed_by="Hải Nguyễn")) == []


def test_reviewed_without_a_critical_check_is_rejected():
    raw = make()
    raw["checks"][0]["importance"] = "supporting"
    assert any("thiếu check critical" in e for e in errors(raw))


def test_reviewed_without_a_contradicting_check_is_rejected():
    raw = make()
    raw["checks"][1]["polarity"] = "support"
    assert any("phản bác" in e for e in errors(raw))


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda c: c.pop("tool"), "cần `tool`"),
        (lambda c: c.pop("params"), "cần `params`"),
        (lambda c: c.pop("pass_when"), "cần `pass_when`"),
        (lambda c: c.update(pass_when={}), "cần `pass_when`"),
        (lambda c: c.update(queries=["x"]), "không có `queries`"),
        (lambda c: c.update(kind="other"), "phải thuộc"),
        (lambda c: c.update(importance="high"), "phải thuộc"),
        (lambda c: c.update(polarity="neutral"), "phải thuộc"),
        (lambda c: c.update(id="Bad Id"), "phải khớp"),
        (lambda c: c.update(extra=1), "trường lạ"),
    ],
)
def test_quant_check_structure(mutate, needle):
    raw = make()
    mutate(raw["checks"][0])
    assert any(needle in e for e in errors(raw))


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda c: c.pop("queries"), "cần `queries`"),
        (lambda c: c.update(queries=[]), "cần `queries`"),
        (lambda c: c.update(queries=[""]), "cần `queries`"),
        (lambda c: c.update(tool="growth_yoy"), "không có tool"),
    ],
)
def test_text_check_structure(mutate, needle):
    raw = make()
    mutate(raw["checks"][1])
    assert any(needle in e for e in errors(raw))


def test_duplicate_check_ids_unknown_fields_type_and_status_are_rejected():
    raw = make()
    raw["checks"][1]["id"] = "a_up"
    assert any("check id lặp" in e for e in errors(raw))
    assert any("trường lạ 'typo'" in e for e in errors(make(typo=1)))
    assert any("anomaly_type" in e for e in errors(make(anomaly_type="ebitda_drop")))
    assert any("status" in e for e in errors(make(status="approved")))
    assert any("mapping" in e for e in errors([1, 2]))


def test_known_tools_restricts_quant_tools_when_configured():
    from dataclasses import replace

    s = replace(S, known_tools=frozenset({"ratio"}))
    assert any("known_tools" in e for e in errors(REVIEWED, s))
    assert errors(REVIEWED, replace(S, known_tools=frozenset({"growth_yoy"}))) == []


def test_only_reviewed_hypotheses_enter_the_pipeline(tmp_path):
    write(tmp_path, "inv_ok", make(id="inv_ok"))
    write(tmp_path, "inv_draft", make(id="inv_draft", status="draft", reviewed_by=None))
    write(tmp_path, "gm_ok", make(id="gm_ok", anomaly_type="gross_margin_change"))
    lib = load_library(tmp_path, S)
    assert [h.id for h in lib.reviewed] == ["gm_ok", "inv_ok"]
    assert [h.id for h in lib.drafts] == ["inv_draft"]
    assert [h.id for h in lib.for_type("inventory_increase")] == ["inv_ok"]
    assert lib.coverage()["inventory_increase"] == {
        "reviewed": 1,
        "draft": 1,
        "missing": S.min_reviewed_per_type - 1,
    }


def test_invalid_reviewed_file_fails_the_whole_load_not_silently_dropped(tmp_path):
    write(tmp_path, "good", make(id="good"))
    write(tmp_path, "bad", make(id="bad", sources=[]))
    with pytest.raises(HypothesisError, match=r"bad\.yaml.*sources"):
        load_library(tmp_path, S)


def test_file_name_must_match_id_so_duplicate_ids_cannot_hide(tmp_path):
    write(tmp_path, "one", make(id="same"))
    write(tmp_path, "two", make(id="same"))
    with pytest.raises(HypothesisError, match="tên file phải là <id>.yaml"):
        load_library(tmp_path, S)


def test_missing_directory_and_broken_yaml(tmp_path):
    with pytest.raises(HypothesisError, match="thư mục"):
        load_library(tmp_path / "nope", S)
    (tmp_path / "x.yaml").write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(HypothesisError, match="không đọc được"):
        load_library(tmp_path, S)


def test_hypothesis_roundtrip_through_dict_and_file(tmp_path):
    write(tmp_path, "test_hyp", REVIEWED)
    (h,) = load_hypothesis_file(tmp_path / "test_hyp.yaml", S)
    assert isinstance(h, Hypothesis) and h.status == "reviewed"
    assert Hypothesis.from_dict(h.to_dict()) == h
    assert h.checks[0].params == {"metric": "capex"} and h.checks[1].queries == ("dự án bị hủy",)


def test_hypothesis_contract_rejects_bad_status():
    with pytest.raises(ValueError):
        Hypothesis.from_dict({**REVIEWED, "status": "approved"})


def test_repo_library_has_no_company_names():
    names = [c.lower() for c in load_config("dataset")["companies"]]
    for p in REPO_LIBRARY.glob("*.yaml"):
        text = p.read_text(encoding="utf-8").lower()
        assert not any(n in text for n in names), p.name


# ---------------- vòng review độc lập


@pytest.mark.parametrize("ext", [".yml", ".YAML", ".json"])
def test_wrongly_named_library_file_is_an_error_not_silently_skipped(tmp_path, ext):
    (tmp_path / f"inv_ok{ext}").write_text(yaml.safe_dump(REVIEWED), encoding="utf-8")
    with pytest.raises(HypothesisError, match="đuôi .yaml"):
        load_library(tmp_path, S)


def test_duplicate_yaml_key_is_rejected_instead_of_last_value_winning(tmp_path):
    text = yaml.safe_dump(make(id="dup_key", reviewed_by="Claude Opus"), allow_unicode=True)
    (tmp_path / "dup_key.yaml").write_text(text + "reviewed_by: Nguyen Van A\n", encoding="utf-8")
    with pytest.raises(HypothesisError, match="khóa trùng"):
        load_library(tmp_path, S)


@pytest.mark.parametrize(
    "name",
    [
        "ClaudeBot Nguyen",
        "Cla\u200bude Nguyen",
        "\u0421laude Nguyen",  # chữ С Cyrillic
        "GPT4 Nguyen",
        "Opus Haiku",
        "Nguyen Llama",
        "A.I. Reviewer",
        "TBD",
        "n/a",
        "Nhóm",
        "Sonnet",
        "a",
    ],
)
def test_reviewer_filter_catches_obfuscated_and_placeholder_names(name):
    assert any("reviewed_by" in e for e in errors(make(reviewed_by=name)))


def test_draft_cannot_carry_a_non_human_reviewer():
    raw = make(status="draft", reviewed_by="Claude Sonnet")
    assert any("reviewed_by" in e for e in errors(raw))


def test_check_fields_with_falsy_garbage_are_rejected():
    raw = make()
    raw["checks"][0]["queries"] = 0
    raw["checks"][0]["pass_when"] = {"zzz": None}
    raw["checks"][1].update(tool=0, params=0, pass_when=False)
    errs = " | ".join(errors(raw))
    assert "không có `queries`" in errs and "pass_when" in errs and "không có tool" in errs


def test_reviewed_needs_a_critical_support_check():
    raw = make()
    raw["checks"][0].update(importance="supporting")
    raw["checks"][1].update(importance="critical")  # chỉ có critical phản bác
    assert any("critical polarity support" in e for e in errors(raw))


def test_reviewed_text_must_not_name_a_company():
    code = load_config("dataset")["companies"][0]
    raw = make(claim=f"Tồn kho tăng ở {code} vì mở rộng.")
    assert any("không gắn công ty" in e for e in errors(raw))
    assert errors(make(sources=[f"Báo cáo {code} 2023"])) == []  # nguồn được phép nêu tên


def test_sources_must_be_more_than_a_placeholder():
    assert any("source" in e for e in errors(make(sources=["x"])))


def test_repo_library_is_the_teams_draft_and_none_is_reviewed_yet():
    lib = load_library()
    assert lib.reviewed == ()
    by_type = {t: [h for h in lib.drafts if h.anomaly_type == t] for t in S.anomaly_types}
    assert all(len(v) >= 6 for v in by_type.values())
    assert all(h.status == "draft" and h.reviewed_by is None for h in lib.drafts)
    assert all(h.code for h in lib.drafts)
    inv4 = next(h for h in lib.drafts if h.code == "INV-4")
    assert inv4.testable_with_annual_data == "no"  # YAML `no` (bool) được chuẩn hóa


def test_file_with_a_hypotheses_list_loads_every_item_and_reports_the_bad_one(tmp_path):
    ok1, ok2 = make(id="one"), make(id="two", anomaly_type="gross_margin_change")
    (tmp_path / "lib.yaml").write_text(
        yaml.safe_dump({"hypotheses": [ok1, ok2]}, allow_unicode=True), encoding="utf-8"
    )
    lib = load_library(tmp_path, S)
    assert [h.id for h in lib.reviewed] == ["one", "two"]
    bad = make(id="bad_one", sources=[])
    (tmp_path / "lib.yaml").write_text(
        yaml.safe_dump({"hypotheses": [ok1, bad]}, allow_unicode=True), encoding="utf-8"
    )
    with pytest.raises(HypothesisError, match=r"lib\.yaml \[bad_one\].*sources"):
        load_library(tmp_path, S)


def test_duplicate_ids_across_list_and_single_files_are_rejected(tmp_path):
    (tmp_path / "lib.yaml").write_text(
        yaml.safe_dump({"hypotheses": [make(id="same")]}, allow_unicode=True), encoding="utf-8"
    )
    write(tmp_path, "same", make(id="same"))
    with pytest.raises(HypothesisError, match="id giả thuyết lặp"):
        load_library(tmp_path, S)


def test_source_codes_must_exist_in_the_catalog_when_reviewed():
    from dataclasses import replace

    with_catalog = replace(S, catalog_codes=frozenset({"S1"}))
    assert errors(make(sources=["S1"]), with_catalog) == []
    assert any("không có trong danh mục" in e for e in errors(make(sources=["S9"]), with_catalog))
    no_catalog = replace(S, catalog_codes=None)
    assert any("không có file danh mục" in e for e in errors(make(sources=["S1"]), no_catalog))
    assert errors(make(sources=["S1"]), no_catalog) != []
    assert errors(make(sources=["Báo cáo ngành 2023"]), no_catalog) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"testable_with_annual_data": "maybe"},
        {"extension_beyond_midterm": "yes please"},
        {"code": ""},
        {"source_strength": 3},
    ],
)
def test_optional_metadata_is_type_checked(changes):
    assert errors(make(**changes)) != []


def test_every_team_draft_has_checks_and_would_pass_review_once_signed_off():
    """Chỉ còn thiếu việc của người: nguồn, người duyệt, critical_missing_evidence của 2 ca rỗng."""
    for h in load_library().drafts:
        assert any(c.importance == "critical" and c.polarity == "support" for c in h.checks), h.id
        assert any(c.polarity == "contradict" for c in h.checks), h.id
        raw = h.to_dict()
        raw.update(status="reviewed", sources=["Nguồn thử nghiệm 2023"], reviewed_by="Nguyễn Văn A")
        if not raw["critical_missing_evidence"]:
            raw["critical_missing_evidence"] = ["(nhóm điền)"]
        for c in raw["checks"]:
            c["queries"] = c["queries"] or None
        assert errors(raw) == [], h.id


# ---------------- trường direction


def test_direction_is_validated_and_required_for_reviewed():
    assert errors(make(direction="down")) == [] and errors(make(direction="both")) == []
    assert any("direction phải thuộc" in e for e in errors(make(direction="sideways")))
    assert any("thiếu direction" in e for e in errors(make(direction=None)))
    # bản draft không bắt buộc, nhưng nếu khai thì phải đúng
    assert errors(make(status="draft", reviewed_by=None, direction=None)) == []
    assert any(
        "direction" in e for e in errors(make(status="draft", reviewed_by=None, direction="x"))
    )


def test_team_library_declares_a_direction_and_covers_both_directions_of_inventory():
    lib = load_library()
    assert all(h.direction in ("up", "down", "both") for h in lib.drafts)
    inv = [h for h in lib.drafts if h.anomaly_type == "inventory_increase"]
    assert {h.direction for h in inv} >= {"up", "down"} and len(inv) >= 12
