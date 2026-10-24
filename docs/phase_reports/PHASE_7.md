# Báo cáo Phase 7 — RAG evidence retrieval

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.2, 10.3, 18, 20 (Phase 7), 23
- **Trạng thái:** phần việc của Claude đã xong, qua 1 vòng review độc lập, lỗi đã sửa. **Chưa COMPLETED**: Phase 6 chưa COMPLETED (chưa có giả thuyết `reviewed`), Phase 5 còn mốc as-of tạm, Phase 3–4 chưa được nhóm xác minh. Phase 7 xây trên các đầu vào chưa xác minh hoàn toàn (spec mục 28.3); nhóm đã quyết định đi tiếp.

## ⚠ Điều nhóm cần biết

- **Mốc as-of vẫn là ngày ký báo cáo kiểm toán** (xấp xỉ, thường ≤ ngày công bố thật; xem Phase 5). Retriever lọc đúng theo ngày này; nếu ngày thật muộn hơn thì kho có thể chứa tài liệu "công bố muộn hơn" mốc thật một chút. Cần thay bằng ngày công bố thật trước khi chạy A/B/C.
- **Kho chỉ có thuyết minh trong BCTC** (bộ OCR ViFinQA); **không có báo cáo thường niên**. Giả thuyết cần sản lượng, cơ cấu sản phẩm, dự án (GM-5, GM-6, INV-2...) sẽ tìm được ít hơn; đây là giới hạn dữ liệu, không phải của retriever.
- **`top_k: 5` và tham số BM25 (`k1 1.5`, `b 0.75`, unigram+bigram) là giá trị khởi điểm của Claude.** Spec yêu cầu nhóm so BM25/dense/hybrid và chọn tham số trên tập dev; chưa làm vì chưa có nhãn dev.
- **Chất lượng truy xuất chưa được đo.** Mới có kiểm tra chức năng và chống rò rỉ. Thử tay vài truy vấn thật cho kết quả đúng mục (ví dụ "dự án mở rộng công suất" trả các bảng "Xây dựng cơ bản dở dang") nhưng có truy vấn trả mục kém liên quan ("thay đổi chính sách kế toán hàng tồn kho" ra mục "Lãi trên cổ phiếu"). Cần đo trên tập dev trước khi tin.

## Phase này để làm gì?

Bước 3b của pipeline (spec mục 6, 10.3): index thuyết minh BCTC theo cấu trúc, truy xuất đoạn gốc làm bằng chứng văn bản cho check `text` của giả thuyết, **ép lọc as-of trong code**, trả đoạn gốc + provenance, **không** trả câu trả lời tổng hợp.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `retrieval/chunking.py` | Chunk theo cấu trúc OCR: tiêu đề mục ("3.6 Hàng tồn kho", chữ in hoa) mở chunk mới và đặt `section`; đoạn liền kề cùng mục gộp tới `max_chars` (1500); bảng là chunk riêng (tách theo hàng nếu dài); câu quá dài tách theo câu/khoảng trắng. Mỗi chunk giữ `doc_id`, `page`, `page_end`, `block_ids`, `section`, `kind`. `Chunk.text` luôn là chuỗi con nguyên văn của block gốc. |
| `retrieval/retriever.py` | `BM25Retriever`: BM25 (k1, b, unigram+bigram theo config). `retrieve` / `retrieve_many` (gộp nhiều truy vấn theo chunk, ghi truy vấn khớp). Trả `RetrievedEvidence` (text gốc, điểm, provenance); không có phần tóm tắt. |
| Lọc as-of (spec 7.2) | `eligible_indices` là nơi duy nhất lọc: đúng công ty; `period_year < reference_only_from_year`; `doc_published` hợp lệ và **≤ as-of**. Chunk không có ngày/ngày hỏng/kiểu lạ **không bao giờ** được trả. `asof` là tham số bắt buộc, phải là ngày hợp lệ. Lọc xảy ra TRƯỚC khi tính điểm. |
| Không rò rỉ gián tiếp | IDF, độ dài trung bình chỉ tính trên tập chunk đủ điều kiện. Có test: thêm 20 chunk "tương lai" không đổi điểm lẫn thứ hạng. Reviewer so retriever toàn kho với retriever chỉ nạp chunk đủ điều kiện trên 36 tổ hợp: điểm trùng tuyệt đối. |
| `retrieval/build.py` | OCR → parse → chunk → gắn ngày công bố từng tài liệu (từ `asof_from_corpus`, tính riêng theo loại báo cáo) → JSONL. Năm tài chính ≥ 2024 **không vào kho**. Tài liệu thiếu ngày vẫn chunk nhưng `doc_published = null` (không truy xuất được) và có trong `notes`. |
| `scripts/build_rag_index.py` | Dựng `data/processed/rag_chunks.jsonl`. Kho hiện tại: **6.698 chunk** (4.637 văn bản, 2.061 bảng) từ 26 tài liệu hợp nhất (3 công ty, 2015–2023). |
| Config | `configs/retrieval.yaml` mở rộng; `enforce_asof_filter` **phải** là `true` (loader từ chối giá trị khác). |

## 2. File

- **Tạo:** `src/steel_xai/retrieval/{settings,chunking,retriever,build}.py`, `scripts/build_rag_index.py`, `tests/test_retrieval.py`, `docs/phase_reports/PHASE_7.md`.
- **Sửa:** `configs/retrieval.yaml`, `retrieval/__init__.py`.

## 3. Quyết định thiết kế (nhóm nên đọc)

- **Phạm vi mặc định = tài liệu của chính công ty đang xét**; muốn dùng tài liệu peer phải truyền `companies=[...]` (và `company` phải nằm trong đó). Tài liệu peer vẫn bị lọc theo ngày công bố của chính nó so với as-of của ca.
- **Chỉ báo cáo hợp nhất** (`document_kinds: [consolidated]`), cùng Phase 3–5. Thêm `separate` được hỗ trợ (ngày công bố tính riêng theo loại).
- **Tài liệu FY2023 có ngày ký tháng 3/2024 vẫn vào kho** vì "năm 2024 trở đi" được hiểu theo **năm tài chính** (spec mục 3.3). Nó chỉ truy xuất được khi as-of ≥ ngày đó, tức chỉ cho ca FY2023. **Nhóm cần xác nhận cách hiểu này.**
- **Tài liệu năm y có thể chứa số so sánh của y-1** (chấp nhận được: đã công bố tại mốc as-of của y). Reviewer đã quét: tài liệu không chứa số liệu hay sự kiện của kỳ sau, chỉ có ngày đáo hạn khoản vay, thời hạn thuê đất, thời gian khấu hao.
- **Không rerank, không dense/hybrid**: spec nói BM25 trước, chỉ thêm khi đo thấy cần.

## 4. Tests

`tests/test_retrieval.py` (30 ca) + toàn repo **680 passed**; `ruff check` và `ruff format --check` sạch. Gồm: config; tokenizer; as-of là tham số bắt buộc và phải hợp lệ; chunking (mục, bảng, nguyên văn, tách câu); xếp hạng xác định; không khớp thì trả rỗng; **chống rò rỉ as-of**: chunk sau as-of không bao giờ ra (kể cả ngày bằng as-of thì được), chunk tương lai không chiếm chỗ top-k, ngày thiếu/hỏng/kiểu lạ, năm tham khảo, điểm không đổi khi thêm tài liệu tương lai, kiểm ngẫu nhiên 40 mốc × 3 truy vấn; phạm vi công ty; build (loại năm 2024, tài liệu thiếu ngày không lộ ra, ngày theo từng loại báo cáo, từ chối as-of sai).

Lệnh: `.venv\Scripts\python -m pytest -q`, `scripts/build_rag_index.py`.

## 5. Review độc lập

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 0 | 2 | 3 |

Reviewer quét 132 mốc as-of × truy vấn top-50 trên kho thật: 0 vi phạm; kiểm điểm so với kho chỉ chứa chunk đủ điều kiện: trùng tuyệt đối. Đã sửa:
- MEDIUM: khi gộp nhiều block vượt `max_chars`, các mảnh nhận sai `page`/`block_ids` (74 chunk có thể trích sai trang). Nay mỗi block tự tách, mảnh luôn mang đúng trang và block của mình; xác minh lại trên kho thật: 0 sai.
- MEDIUM: bật thêm `separate` làm hai báo cáo cùng kỳ ra hai ngày khác nhau (conflict) và cả báo cáo hợp nhất mất ngày. Nay tính ngày theo từng loại và `notes` ghi rõ lý do.
- LOW: `company` bị bỏ qua khi truyền `companies` (nay lỗi); crash khi `doc_published` không phải chuỗi (nay bỏ qua chunk); cache khóa theo ngày (nay khóa theo tập chunk đủ điều kiện, 28 mốc ngày chỉ còn 1 mục); `build_chunks` nhận as-of tùy chỉnh sai định dạng (nay từ chối).

## 6. Known limitations

- Chất lượng truy xuất chưa đo; tham số BM25/top_k chưa chỉnh trên dev.
- Chỉ có thuyết minh BCTC, không có báo cáo thường niên (xem trên).
- Tokenizer theo âm tiết (không tách từ tiếng Việt); không xử lý lỗi OCR; từ đồng nghĩa không khớp ("tích trữ" ≠ "dự trữ").
- HSG FY2015 (314 chunk trong tập chưa lọc) vĩnh viễn không truy xuất được vì không trích được ngày ký kiểm toán (ngoài cửa sổ chọn ca).
- Mốc as-of tạm (xem đầu báo cáo). Hiệu năng: ~35 ms cho mỗi tập đủ điều kiện mới; kho 6.7k chunk nên không đáng kể.
- Chưa có kiểm trích dẫn nguyên văn bằng code (Phase 9) và chưa nối với `checks` của giả thuyết (Phase 8–9).

## 7. Chưa làm vì ngoài scope

Kiểm trích dẫn (Phase 9); LLM phân loại bằng chứng; chạy `queries` của check từ thư viện giả thuyết; so BM25/dense/hybrid trên dev; reranker; báo cáo thường niên.

## 8. Hard-code công ty

Không. Danh sách công ty và mốc năm đọc từ `configs/dataset.yaml`; không có tên công ty trong `src/`.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-24 — Phase 7".

## 10. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới. Kho RAG chỉ gồm thuyết minh BCTC (không có báo cáo thường niên) là giới hạn dữ liệu của bộ OCR, nên được ghi nhận ở trên; nhóm có thể cân nhắc thêm vào `docs/DRAFT_SPEC_CHANGES.md` nếu giữa kỳ hứa có báo cáo thường niên.
