# Báo cáo Phase 1 — Document parsing

- **Ngày:** 2026-10-22
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 16, 17, 18, 20 — Phase 1
- **Trạng thái:** phần việc của Claude đã xong, đã qua review độc lập và regression test. **Chưa COMPLETED** cho tới khi nhóm spot-check và commit (spec mục 25, 28.2).

---

## Phase này để làm gì?

Hệ thống phải giải thích bất thường bằng **bằng chứng có trích dẫn**. Mỗi đoạn trích phải ghi được nó lấy từ tài liệu nào, trang nào, đoạn nào, và phải khớp nguyên văn với bản gốc (spec mục 10.3, 10.4). Ngoài ra, nhiều con số cần cho kiểm chứng chỉ có trong bảng của thuyết minh BCTC, chẳng hạn cơ cấu tồn kho hay XDCB dở dang (spec mục 7.1).

Đầu vào của dự án là văn bản OCR trích từ PDF: một khối chữ dài, lẫn bảng HTML, lỗi OCR và số trang in lệch với số trang PDF. Ở dạng thô như vậy, các phase sau không có cách nào:

- biết một đoạn văn nằm ở trang nào để ghi trích dẫn;
- tách bảng ra để đọc số;
- chia tài liệu theo cấu trúc để đưa vào RAG.

Phase 1 là bước đầu tiên của pipeline. Phase này biến văn bản OCR thành cấu trúc **Tài liệu → Trang → Block (đoạn văn hoặc bảng)**, và mỗi block luôn mang theo nguồn gốc của nó. Các phase sau dùng kết quả này như sau:

| Phase sau | Dùng kết quả Phase 1 để |
|---|---|
| Phase 2 — Table extraction | lấy các block bảng đã tách ô để đọc số (dòng/cột/kỳ) |
| Phase 7 — RAG | chia chunk theo block, mỗi chunk giữ trang và id block |
| Phase 9 — Kiểm quote nguyên văn | so đoạn trích của LLM với văn bản gốc của block |
| Phase 11 — Báo cáo | in trích dẫn "tài liệu X, trang PDF Y, block Z" |

Phase này **chưa** đọc số, chưa phát hiện bất thường và chưa gọi LLM.

---

## 1. Tóm tắt: đã làm được gì

| Đã làm được | Để làm gì |
|---|---|
| Đọc văn bản OCR và chia theo trang dựa vào marker `===== PAGE N =====`. Số trang dùng là **số trang PDF**, không phải số in trên giấy. Ví dụ: thuyết minh hàng tồn kho của fixture HPG in số "39" nhưng được ghi đúng là trang PDF 40. | Người đọc mở file PDF gốc và nhảy đúng tới trang được trích dẫn. |
| Chia mỗi trang thành block: đoạn văn (ngăn bởi dòng trống) và bảng HTML. Bảng có thể nằm trên một dòng, nhiều dòng, hoặc dính chữ trên cùng dòng. | Phase 7 chia chunk theo cấu trúc tài liệu; Phase 2 biết chỗ nào là bảng để đọc số. |
| Tách bảng HTML thành dòng và ô, giữ `rowspan`/`colspan`, giải mã ký tự như `&amp;`. | Phase 2 có sẵn lưới ô để đọc số mà không phải tự parse HTML. |
| Giữ nguyên văn 100%: không sửa lỗi OCR, không bỏ dòng nào, kể cả rác OCR. Test kiểm từng ký tự. | Quote của LLM về sau được so với **đúng** văn bản gốc. Nếu parser tự sửa chữ, quote thật sẽ bị đánh trượt. |
| Mỗi block có id cố định (ví dụ `doc:p0040:b008`), số trang, số dòng trong file nguồn và `Provenance`. Chạy lại luôn ra cùng id. | Bằng chứng trích dẫn được truy ngược về đúng chỗ; kết quả tái lập được giữa các lần chạy và giữa các máy. |
| Khi không chắc về trang (thiếu marker, trang trùng hoặc giảm, marker sai định dạng), parser báo lỗi thay vì đoán. Bảng hỏng vẫn được giữ, kèm cờ (`flags`). | Theo nguyên tắc "không biết > đoán sai" (mục 23): thà dừng còn hơn ghi trích dẫn sai trang. |
| Lưu và đọc lại bằng JSON. Khi nạp, kiểm tra id, trang và tài liệu có khớp nhau không. | Kết quả parse lưu được vào `data/processed/` cho các phase sau dùng lại; file bị sửa sai sẽ bị từ chối. |
| Parser viết chung cho mọi tài liệu, không dựa vào tên công ty, số trang hay số dòng cố định. Đã thử trên fixture tổng hợp (công ty giả định) và fixture HPG. | Dùng được cho mọi công ty thép trong danh sách nhóm chốt (spec mục 17). |

## 2. File tạo

- `src/steel_xai/contracts.py`
- `src/steel_xai/parsing/document.py`
- `tests/test_parsing_document.py`
- `tests/fixtures/synthetic_ocr_document.txt`
- `docs/phase_reports/PHASE_1.md`

## 3. File sửa

- `src/steel_xai/parsing/__init__.py`
- `README.md`
- `AI_USAGE_LOG.md`

## 4. Quyết định thiết kế

- **`page` = số trang PDF lấy từ marker.** Số trang in trên tài liệu chỉ là một block text bình thường.
- **Marker không rõ ràng thì báo lỗi.** Các trường hợp sau đều raise `ParseError`: không có marker, có nội dung trước marker đầu, trang trùng hoặc giảm, trang 0, dòng giống marker nhưng sai định dạng. Trang bị khuyết (nhảy số) vẫn được chấp nhận, nhưng hiện ra qua `Document.missing_pages`.
- **Block id có dạng `<doc_id>:p<4 số>:b<3 số>`**, theo vị trí. Không dùng hash nội dung vì header lặp sẽ trùng id. Khi dựng lại từ JSON, `Document` kiểm tra id, trang và doc_id có khớp nhau không.
- **Không mất nội dung:** mọi ký tự không trắng thuộc đúng một block, đúng thứ tự. Test kiểm điều này trên cả hai fixture.
- **Bảng hỏng vẫn được giữ** cùng `flags`, không bị bỏ đi. Comment, script và style được che lại trước khi dò thẻ `<table>`.
- **Chuẩn hóa BOM và CRLF.** `content_sha256` được tính trên văn bản đã chuẩn hóa, nên không đổi theo cài đặt `autocrlf` của git.
- **Định dạng marker nằm trong code** (`PAGE_MARKER_RE`), không đưa vào config. Đây là quy ước định dạng đầu vào, không phải ngưỡng hay tham số nghiên cứu.
- Chỉ dùng thư viện chuẩn (`html.parser`), không thêm dependency.

## 5. Tests

`tests/test_parsing_document.py` có 53 test case (tính cả parametrize):
- trang lấy theo marker, trang trống, trang khuyết;
- giữ nguyên văn và rác OCR;
- bảng một dòng dính text, bảng nhiều dòng, bảng không đóng, bảng lồng, span hỏng, comment trong bảng, thẻ khối trong ô;
- id ổn định và tra ngược được, provenance, số dòng, phủ nội dung;
- roundtrip JSON và từ chối JSON không nhất quán;
- CRLF/BOM, file không phải UTF-8, marker biến thể và marker méo;
- một test tích hợp trên fixture HPG.

## 6. Commands đã chạy

```bash
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

## 7. Kết quả

| Passed | Skipped | Failed |
|---|---|---|
| 99 | 0 | 0 |

- Ruff check và ruff format: sạch.
- Trên fixture HPG: 67 trang, 800 block, 74 bảng. Không bảng nào bị gắn cờ, không có trang khuyết.

## 8. Known limitations

- Chưa nhận diện heading/section. Header và footer lặp lại vẫn là block riêng. Hai việc này để cho chunking ở Phase 7.
- Chưa lọc rác OCR (con dấu, công thức LaTeX giả), vì theo nguyên tắc giữ nguyên văn.
- Chỉ hỗ trợ một định dạng marker trang. Định dạng OCR khác cần chuyển đổi trước khi đưa vào parser.
- Ô bảng `rowspan` không được nhân bản xuống các dòng dưới. Việc mở rộng lưới thuộc Phase 2.

## 9. Chưa làm vì ngoài scope

- Parse số kiểu Việt Nam, xác định dòng/cột/kỳ (Phase 2).
- Chunking và index RAG, lọc as-of (Phase 7).

## 10. Hard-code công ty

Không có. Parser không phụ thuộc vào tên công ty, số trang, số dòng hay năm. Chỉ test tích hợp mới chứa vài kỳ vọng riêng của fixture HPG (trang 40, số in "39"), với vai trò ví dụ.

## 11. AI_USAGE_LOG.md

Đã thêm mục "2026-10-22 — Phase 1". Kết quả review độc lập: 0 CRITICAL, 1 HIGH, 2 MEDIUM và một số LOW, tất cả đã sửa.

Phần người cần xác minh:
- Đối chiếu block `...:p0040:b008` với trang PDF 40 của bản gốc.
- Quy ước marker trang trong đầu vào.
- Ý nghĩa các cờ bảng.
- Không đưa fixture 2024 vào kho RAG.
- Quyết định có commit fixture HPG hay không.

## 12. Khác báo cáo giữa kỳ

Không có điểm khác mới. Không sửa `DEVIATIONS_FROM_MIDTERM.md`.
