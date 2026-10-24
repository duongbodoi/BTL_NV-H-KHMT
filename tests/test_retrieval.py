"""Test Phase 7: chunk theo cấu trúc, BM25, lọc as-of ép trong code, provenance, không rò rỉ."""

import copy
import datetime as dt
import random
from dataclasses import replace

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.parsing import parse_text
from steel_xai.retrieval import (
    BM25Retriever,
    Chunk,
    build_chunks,
    chunk_document,
    is_heading,
    load_retrieval_settings,
    parse_asof,
    read_chunks,
    split_text,
    tokenize,
    write_chunks,
)

CFG = load_config("retrieval")
S = load_retrieval_settings()


def settings(**changes):
    return replace(S, **changes)


def C(cid, text, *, company="AAA", year=2020, published="2021-03-01", section=None, kind="text"):
    return Chunk(
        chunk_id=cid,
        doc_id=f"{company}_{year}",
        company=company,
        period=f"FY{year}",
        period_year=year,
        doc_published=published,
        published_source="audit_report_date" if published else None,
        page=1,
        page_end=1,
        section=section,
        kind=kind,
        block_ids=(f"{company}_{year}:p0001:b000",),
        text=text,
    )


def R(chunks, **kw):
    return BM25Retriever(chunks, settings(**kw))


ids = lambda res: [e.chunk_id for e in res]  # noqa: E731


# ---------------- config


def test_repo_config_loads_and_asof_filter_is_mandatory():
    assert S.method == "bm25" and S.top_k >= 1 and S.reference_only_from_year == 2024
    bad = copy.deepcopy(CFG)
    bad["enforce_asof_filter"] = False
    with pytest.raises(ConfigError, match="enforce_asof_filter"):
        load_retrieval_settings(bad)
    for key, value in [("method", "dense"), ("top_k", 0), ("document_kinds", ["x"])]:
        bad = copy.deepcopy(CFG)
        bad[key] = value
        with pytest.raises(ConfigError):
            load_retrieval_settings(bad)


# ---------------- tokenizer, as-of parsing


def test_tokenize_normalizes_and_adds_bigrams():
    assert tokenize("Hàng tồn kho", (1, 2)) == ["hàng", "tồn", "kho", "hàng tồn", "tồn kho"]
    assert tokenize("Hàng tồn kho", (1,)) == ["hàng", "tồn", "kho"]
    assert tokenize("Ｈàng ｔồn") == tokenize("Hàng tồn")  # NFKC: ký tự toàn chiều rộng


def test_asof_must_be_a_valid_date():
    assert parse_asof("2023-03-16") == dt.date(2023, 3, 16)
    assert parse_asof(dt.date(2023, 3, 16)) == dt.date(2023, 3, 16)
    for bad in ("2023-13-01", "", None, "16/03/2023", dt.datetime(2023, 3, 16)):
        with pytest.raises((ValueError, TypeError)):
            parse_asof(bad)  # type: ignore[arg-type]


def test_asof_is_a_required_argument():
    r = R([C("a", "hàng tồn kho")])
    with pytest.raises(TypeError):
        r.retrieve("hàng tồn kho", company="AAA")  # type: ignore[call-arg]


# ---------------- chunking


DOC = """===== PAGE 1 =====
BÁO CÁO TÀI CHÍNH

3.6 Hàng tồn kho

Hàng tồn kho được phản ánh theo giá thấp hơn giữa giá gốc và giá trị thuần.

Giá gốc tính theo phương pháp bình quân gia quyền.

===== PAGE 2 =====
9. Hàng tồn kho

<table><tr><td>Nguyên vật liệu</td><td>100</td></tr><tr><td>Thành phẩm</td><td>200</td></tr></table>

Dự phòng giảm giá hàng tồn kho được trích lập khi cần.
"""


def chunks_of(text=DOC, **kw):
    doc = parse_text(text, "AAA_2020")
    return chunk_document(
        doc,
        company="AAA",
        period="FY2020",
        period_year=2020,
        published="2021-03-01",
        published_source="audit_report_date",
        settings=settings(**kw),
    )


def test_headings_start_chunks_and_set_the_section():
    cs = chunks_of()
    assert [c.section for c in cs] == [
        "BÁO CÁO TÀI CHÍNH",
        "3.6 Hàng tồn kho",
        "9. Hàng tồn kho",
        "9. Hàng tồn kho",
        "9. Hàng tồn kho",
    ]
    assert [c.kind for c in cs] == ["text", "text", "text", "table", "text"]
    body = cs[1]
    assert "bình quân gia quyền" in body.text and body.page == 1 and body.page_end == 1


def test_every_chunk_has_provenance_and_text_is_verbatim_from_blocks():
    doc = parse_text(DOC, "AAA_2020")
    blocks = {b.block_id: b for b in doc.iter_blocks()}
    for c in chunks_of():
        assert c.block_ids and c.chunk_id.startswith("AAA_2020:c")
        p = c.provenance()
        assert p.doc_id == "AAA_2020" and p.chunk_id == c.chunk_id and p.source_text == c.text
        source = "\n".join(blocks[b].text for b in c.block_ids)
        for line in c.text.split("\n"):
            if line.strip():
                assert line.strip() in source


def test_tables_can_be_excluded():
    assert all(c.kind == "text" for c in chunks_of(include_tables=False))


def test_split_text_pieces_are_verbatim_substrings_and_bounded():
    text = " ".join(f"Câu số {i} nói về hàng tồn kho." for i in range(60))
    parts = split_text(text, 200)
    assert len(parts) > 1 and all(len(p) <= 200 and p in text for p in parts)
    assert "".join(p.replace(" ", "") for p in parts) == text.replace(" ", "")
    long_word = "x" * 500
    assert all(len(p) <= 200 for p in split_text(long_word, 200))
    assert split_text("ngắn", 200) == ["ngắn"]


def test_heading_detection():
    doc = parse_text(
        "===== PAGE 1 =====\n3.6 Hàng tồn kho\n\nCâu văn dài kết thúc bằng dấu chấm.\n", "d"
    )
    blocks = list(doc.iter_blocks())
    assert is_heading(blocks[0]) and not is_heading(blocks[1])


# ---------------- BM25 xếp hạng cơ bản


def test_ranking_prefers_the_matching_chunk_and_is_deterministic():
    cs = [
        C("a", "dự phòng giảm giá hàng tồn kho tăng mạnh"),
        C("b", "chi phí lãi vay và tỷ giá"),
        C("c", "hàng tồn kho tăng do dự án mở rộng công suất"),
    ]
    r = R(cs)
    res = r.retrieve("dự án mở rộng công suất", company="AAA", asof="2022-01-01")
    assert ids(res)[0] == "c" and "b" not in ids(res)
    assert res == r.retrieve("dự án mở rộng công suất", company="AAA", asof="2022-01-01")
    assert res[0].score > 0 and res[0].queries == ("dự án mở rộng công suất",)


def test_no_match_returns_empty_not_a_guess():
    r = R([C("a", "chi phí lãi vay")])
    assert r.retrieve("thép cuộn cán nóng", company="AAA", asof="2022-01-01") == []
    assert r.retrieve("", company="AAA", asof="2022-01-01") == []


def test_top_k_limits_and_validates():
    cs = [C(f"c{i}", f"hàng tồn kho số {i}") for i in range(10)]
    r = R(cs)
    assert len(r.retrieve("hàng tồn kho", company="AAA", asof="2022-01-01", top_k=3)) == 3
    with pytest.raises(ValueError):
        r.retrieve("hàng tồn kho", company="AAA", asof="2022-01-01", top_k=0)


def test_result_is_source_text_not_a_synthesized_answer():
    r = R([C("a", "Hàng tồn kho tăng do tích trữ nguyên liệu.", section="9. Hàng tồn kho")])
    (e,) = r.retrieve("tích trữ nguyên liệu", company="AAA", asof="2022-01-01")
    assert e.text == "Hàng tồn kho tăng do tích trữ nguyên liệu."
    assert e.provenance().source_text == e.text and e.section == "9. Hàng tồn kho"
    assert set(e.to_dict()) >= {"chunk_id", "doc_id", "page", "text", "score", "doc_published"}


def test_multiple_queries_are_merged_by_chunk():
    cs = [C("a", "tích trữ nguyên liệu giá thấp"), C("b", "dự án mở rộng công suất")]
    res = R(cs).retrieve_many(
        ["tích trữ nguyên liệu", "nguyên liệu giá thấp", "dự án mở rộng"],
        company="AAA",
        asof="2022-01-01",
    )
    assert sorted(ids(res)) == ["a", "b"]
    a = next(e for e in res if e.chunk_id == "a")
    assert a.queries == ("tích trữ nguyên liệu", "nguyên liệu giá thấp")


# ---------------- CHỐNG RÒ RỈ AS-OF (bắt buộc, spec mục 7.2)


def test_chunk_published_after_asof_is_never_returned():
    cs = [
        C("old", "hàng tồn kho tăng", year=2019, published="2020-03-10"),
        C("same_day", "hàng tồn kho tăng", year=2020, published="2021-03-01"),
        C(
            "future",
            "hàng tồn kho tăng rất mạnh hàng tồn kho hàng tồn kho",
            year=2021,
            published="2022-03-01",
        ),
    ]
    r = R(cs)
    got = ids(r.retrieve("hàng tồn kho", company="AAA", asof="2021-03-01"))
    assert "future" not in got and set(got) == {"old", "same_day"}  # ngày công bố == as-of: được
    assert ids(r.retrieve("hàng tồn kho", company="AAA", asof="2021-02-28")) == ["old"]
    assert "future" in ids(r.retrieve("hàng tồn kho", company="AAA", asof="2022-03-01"))


def test_future_chunk_with_best_match_does_not_displace_eligible_chunks_from_top_k():
    cs = [C(f"ok{i}", f"hàng tồn kho {i}", published="2020-01-01") for i in range(3)]
    cs += [C(f"fut{i}", "hàng tồn kho " * 5, year=2021, published="2022-01-01") for i in range(5)]
    got = ids(R(cs).retrieve("hàng tồn kho", company="AAA", asof="2020-06-01", top_k=3))
    assert sorted(got) == ["ok0", "ok1", "ok2"]


def test_unknown_publication_date_is_never_retrievable():
    cs = [C("known", "hàng tồn kho"), C("unknown", "hàng tồn kho hàng tồn kho", published=None)]
    got = ids(R(cs).retrieve("hàng tồn kho", company="AAA", asof="2030-01-01"))
    assert got == ["known"]


def test_reference_only_years_are_never_retrievable_even_if_dates_look_old():
    cs = [
        C("ok", "hàng tồn kho", year=2023),
        C("ref", "hàng tồn kho", year=2024, published="2020-01-01"),
    ]
    assert ids(R(cs).retrieve("hàng tồn kho", company="AAA", asof="2030-01-01")) == ["ok"]


def test_malformed_published_date_is_not_retrievable():
    cs = [C("bad", "hàng tồn kho", published="không phải ngày"), C("ok", "hàng tồn kho")]
    assert ids(R(cs).retrieve("hàng tồn kho", company="AAA", asof="2030-01-01")) == ["ok"]


def test_scores_and_ranking_do_not_depend_on_documents_published_after_asof():
    base = [
        C("a", "dự phòng giảm giá hàng tồn kho", published="2020-01-01"),
        C("b", "hàng tồn kho tăng do mở rộng công suất", published="2020-01-01"),
        C("c", "chi phí lãi vay", published="2020-01-01"),
    ]
    future = [
        C(
            f"f{i}",
            "dự phòng giảm giá hàng tồn kho mở rộng công suất " * 3,
            year=2021,
            published="2022-01-01",
        )
        for i in range(20)
    ]
    q = "dự phòng giảm giá hàng tồn kho"
    without = R(base).retrieve(q, company="AAA", asof="2020-06-01")
    with_future = R(base + future).retrieve(q, company="AAA", asof="2020-06-01")
    assert [(e.chunk_id, e.score) for e in without] == [(e.chunk_id, e.score) for e in with_future]


def test_randomized_asof_never_returns_a_later_chunk():
    rng = random.Random(7)
    words = ["tồn", "kho", "dự", "phòng", "thép", "công", "suất", "giá", "vốn", "nguyên", "liệu"]
    cs = []
    for i in range(80):
        y = rng.randint(2015, 2023)
        day = dt.date(y + 1, 1, 1) + dt.timedelta(days=rng.randint(0, 120))
        text = " ".join(rng.choices(words, k=12))
        cs.append(C(f"c{i}", text, year=y, published=day.isoformat()))
    r = R(cs)
    for _ in range(40):
        cut = dt.date(2016, 1, 1) + dt.timedelta(days=rng.randint(0, 3000))
        for q in ("tồn kho", "dự phòng giá vốn", "công suất nguyên liệu"):
            for e in r.retrieve(q, company="AAA", asof=cut, top_k=10):
                assert dt.date.fromisoformat(e.doc_published) <= cut
                assert e.period != "FY2024"


def test_company_scope_defaults_to_own_documents_and_peers_still_respect_asof():
    cs = [
        C("own", "hàng tồn kho", company="AAA"),
        C("peer_ok", "hàng tồn kho", company="BBB", published="2021-01-01"),
        C("peer_late", "hàng tồn kho", company="BBB", published="2023-01-01"),
    ]
    r = R(cs)
    assert ids(r.retrieve("hàng tồn kho", company="AAA", asof="2022-01-01")) == ["own"]
    both = ids(
        r.retrieve("hàng tồn kho", company="AAA", asof="2022-01-01", companies=["AAA", "BBB"])
    )
    assert sorted(both) == ["own", "peer_ok"]


def test_duplicate_chunk_ids_are_rejected():
    with pytest.raises(ValueError, match="chunk_id trùng"):
        R([C("a", "x"), C("a", "y")])


# ---------------- build + JSONL


def test_jsonl_roundtrip(tmp_path):
    cs = chunks_of()
    path = tmp_path / "c.jsonl"
    write_chunks(cs, path)
    assert read_chunks(path) == cs


def test_build_chunks_on_a_tiny_corpus_excludes_reference_years_and_flags_missing_dates(tmp_path):
    def doc(year, audit_line):
        d = tmp_path / "AAA" / str(year) / f"AAA_financial_statements_{year}_consolidated"
        d.mkdir(parents=True)
        (d / f"AAA_financial_statements_{year}_consolidated_extracted.txt").write_text(
            "===== PAGE 1 =====\n"
            f"Báo cáo tài chính hợp nhất cho năm kết thúc ngày 31 tháng 12 năm {year}\n\n"
            f"{audit_line}\n\n3.6 Hàng tồn kho\n\nHàng tồn kho tính theo bình quân gia quyền.\n",
            encoding="utf-8",
        )

    doc(2021, "Hà Nội, ngày 15 tháng 3 năm 2022")
    doc(2022, "không có ngày ký")
    doc(2024, "Hà Nội, ngày 15 tháng 3 năm 2025")
    res = build_chunks(tmp_path, ["AAA"], S)
    docs = {c.doc_id for c in res.chunks}
    assert docs == {
        "AAA_financial_statements_2021_consolidated",
        "AAA_financial_statements_2022_consolidated",
    }
    assert any("2024" in n and "không vào kho" in n for n in res.notes)
    assert any("2022" in n and "chưa có ngày công bố" in n for n in res.notes)
    assert {c.doc_published for c in res.chunks if "2022" in c.doc_id} == {None}
    r = BM25Retriever(res.chunks, S)
    got = r.retrieve("bình quân gia quyền", company="AAA", asof="2030-01-01", top_k=20)
    assert got and all("2021" in e.doc_id for e in got)  # tài liệu thiếu ngày không bao giờ lộ ra
