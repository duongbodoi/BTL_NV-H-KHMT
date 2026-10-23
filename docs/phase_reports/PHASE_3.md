# Báo cáo Phase 3 — Financial metric normalization (bản cập nhật theo dữ liệu năm)

- **Ngày:** 2026-10-23 (cập nhật sau quyết định của nhóm về dữ liệu)
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.1, 16, 17, 18, 20 (Phase 3), 23
- **Trạng thái:** bản đầu (khóa kỳ theo quý) đã được nhóm commit (`4d2b8fa`). Bản này **thay khóa kỳ sang năm tài chính** và thêm các quyết định mới; đã qua 2 vòng review độc lập. Chưa COMPLETED cho tới khi nhóm spot-check và commit (spec mục 25, 28.2).

## Quyết định của nhóm đã áp dụng

| Quyết định | Áp dụng |
|---|---|
| Dữ liệu là BCTC **năm**, nguồn chính OCR ViFinQA, không có nguồn có cấu trúc | Khóa kỳ `FY<năm>` kèm `fiscal_period_end`. Nhánh CSV giữ nguyên nhưng không dùng; không điền `structured_aliases`. Quý bị loại (`not_annual`). |
| Công ty HPG, HSG, NKG; HSG kết thúc năm tài chính 30/9 | `configs/dataset.yaml`; `fiscal_period_end` đọc từ văn bản tài liệu; không giả định 31/12. |
| Số bị điều chỉnh ở năm sau | Giá trị chính của năm N lấy từ báo cáo năm N; số so sánh trong báo cáo N+1 lưu riêng (`result.comparatives`); khác thì fact chính có cờ `restated_later`, **không** thành `UNRESOLVED`. Chỉ có số so sánh → dùng nó kèm cờ `comparative_only`. |
| `-` khi cộng tổng coi là 0 | Thuộc Phase 4 (xem `PHASE_4.md`). Ở Phase 3 `-` vẫn là `NOT_APPLICABLE`, không có giá trị. |

## 1. Tóm tắt: đã làm được gì

- **Từ điển 11 metric** trong `configs/normalization.yaml`: tồn kho thuần (mã 140), tồn kho giá gốc (141), dự phòng giảm giá (149), nguyên vật liệu, thành phẩm, dở dang, XDCB dở dang, doanh thu thuần, giá vốn, lợi nhuận gộp, capex; cộng `gross_margin` dẫn xuất. Nhãn dùng chung (140 và 141 cùng ghi "Hàng tồn kho") được tách bằng mã số.
- **Khóa kỳ năm tài chính.** Số dư chỉ nhận ngày cuối tháng (cột `1/1/2024` bị loại, `date_not_month_end`). Số dòng cần năm hoặc ngày kèm từ "năm"; quý, 6 tháng, ngày trần đều bị loại có lý do.
- **Ngữ cảnh tài liệu** (`normalization/context.py`): ngày kết thúc năm tài chính của báo cáo = ngày cuối tháng xuất hiện nhiều nhất và nhiều hơn rõ rệt ngày xếp sau (hòa → `None`, không đoán). Dùng để giải header tương đối "Năm nay / Năm trước / Số cuối năm / Số đầu năm" (HSG 2017) và điền `fiscal_period_end` cho header chỉ có năm. Đơn vị lấy từ văn bản ("trình bày bằng đồng Việt Nam") chỉ khi không có câu nào nói theo bậc nghìn/triệu/tỷ **và** bảng không ghi một đơn vị lạ hay mâu thuẫn.
- **Gộp nguồn.** Cấu trúc > OCR (nhánh không dùng). Hai tài liệu cùng năm báo cáo lệch nhau, hai `fiscal_period_end` khác nhau cho cùng năm, hoặc `-` ở một nơi còn nơi khác có số → `UNRESOLVED` kèm bản ghi `Conflict`.
- **Dấu.** Giá vốn lưu độ lớn dương (HSG ghi trong ngoặc; cờ `sign_normalized`). Capex đảo về dương (`sign_flipped`), dương bất ngờ → `UNRESOLVED`. Dự phòng giữ dấu âm như báo cáo.
- **`gross_margin`** do code tính, mang `fiscal_period_end` của đầu vào và các cờ `comparative_only`/`restated_later`; đầu vào lệch ngày khóa sổ → `UNRESOLVED`.
- **Sửa Phase 2** (phát hiện nhờ fixture NKG 2023): ô header hai tầng "Tại ngày 31 tháng 12 năm" (năm ở dòng dưới) làm cả bảng mất header; đã nhận dạng ô ngày thiếu năm là header.

Kết quả trên 5 fixture (ví dụ, kỳ vọng nhóm sẽ tự spot-check):

| Fixture | Fact | Ghi chú |
|---|---|---|
| HPG 2024 | 17 | nguyên vật liệu `20.324.528.389.776`, thành phẩm `12.155.686.194.056`, tồn kho thuần `46.091.222.189.472` (trang 40, 7), biên gộp FY2024 ≈ 0,1332 |
| HPG 2022 | 14 | không có bảng thuyết minh tồn kho → không có fact thành phần |
| HSG 2017 | 16 | `fiscal_period_end = 2017-09-30`; giá vốn `(21.730.791.206.018)` → `21.730.791.206.018` |
| NKG 2019 | 18 | doanh thu thuần là ô dồn số → `UNRESOLVED`, biên gộp không đoán |
| NKG 2023 | 20 | mã số ở cột đầu, header hai tầng |

## 2. File

- **Tạo:** `src/steel_xai/normalization/context.py`, `tests/test_review_round2.py`, `docs/DRAFT_SPEC_CHANGES.md`.
- **Sửa:** `configs/normalization.yaml`, `configs/dataset.yaml` (HPG/HSG/NKG, `frequency: annual`, `reconciliation_sample_n: 20`), `configs/anomaly.yaml` (`compare_order: [yoy]`), `normalization/{settings,mapping,resolve,pipeline,__init__}.py`, `contracts.py` (`FinancialFact.fiscal_period_end`), `extraction/tables.py` (header ngày thiếu năm), `tests/test_normalization.py` (viết lại cho khóa năm), `tests/test_extraction_tables.py`, `README.md`, `AI_USAGE_LOG.md`.

## 3. Tests

Theo fixture của nhóm đã thêm mỗi file một test ngắn: NKG 2019 (ô dồn số → `UNRESOLVED`), HSG 2017 (giá vốn âm, kỳ kết thúc 30/9), NKG 2023 (mã số ở cột đầu, tiêu đề hai tầng), HPG 2022 (thiếu thuyết minh tồn kho → không có thành phần; Phase 4 báo `MISSING`). Số test: `test_normalization.py` 94, `test_review_round2.py` 19 (nửa là Phase 3), 1 test mới ở `test_extraction_tables.py`. Toàn bộ repo: **448 passed, 0 skipped, 0 failed**; ruff check và format sạch.

## 4. Review độc lập

| Vòng | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| 1 (bản quý) | 0 | 1 | 5 | 5 |
| 2 (bản năm + Phase 4) | 0 | 2 | 6 | 8 |

Vòng 2 (liên quan Phase 3) đã sửa: đơn vị lạ bị đơn vị văn bản ghi đè (sai 10^3–10^6 mà vẫn OK); ngày ký báo cáo thắng phiếu làm lệch cả năm tài chính; `restated_later` bỏ sót khi một bên là `-`; cờ nguồn không truyền sang số dẫn xuất; văn bản NFD không đọc được ngày. Các mục LOW còn mở: xem mục 5.

## 5. Known limitations

- **Báo cáo hợp nhất và riêng lẻ chưa được phân biệt trong khóa fact.** Thư mục ViFinQA có cả `consolidated` và `separate`; hai loại cùng năm sẽ xung đột thành `UNRESOLVED`. Hiện chỉ nên nạp `consolidated`. Nhóm cần chốt (thêm chiều vào khóa hay lọc ở khâu nạp).
- Header "(đã điều chỉnh)" của Phase 2 chỉ là cờ `restated`, chưa tham gia quy tắc nào.
- **Ngày kết thúc năm tài chính suy từ văn bản** (heuristic phiếu đa số). Khi hòa hoặc không có, header tương đối bị loại (`report_period_unknown`). Cần nhóm spot-check `fiscal_period_end` của từng tài liệu thật.
- Số đầu năm (`1/1/2024`) bị loại; số dư năm trước lấy từ cột "Số đầu năm"/so sánh nếu header ghi rõ.
- Dung sai so nguồn mặc định 0; chưa có quy tắc cho số trình bày lại ngoài cờ `restated_later`.
- Cột Dự phòng của thuyết minh chưa là metric; thành phần tồn kho chỉ là một phần của tổng giá gốc.
- Kế thừa các giới hạn heuristic của Phase 2.

## 6. Chưa làm vì ngoài scope

Phát hiện bất thường (Phase 5); thư viện giả thuyết (Phase 6, nhóm soạn); adapter vnstock; phân biệt hợp nhất/riêng lẻ.

## 7. Hard-code công ty

Không có trong `src`/`configs` logic. `configs/dataset.yaml` chứa danh sách công ty do nhóm chốt (đúng chỗ theo spec). Chỉ test dùng tên công ty của fixture làm ví dụ.

## 8. Khác báo cáo giữa kỳ

**Có** (dữ liệu năm thay quý, chỉ YoY, 3 công ty, mùa vụ không kiểm được). Chưa ghi vào `DEVIATIONS_FROM_MIDTERM.md`; đã soạn **bản nháp** ở `docs/DRAFT_SPEC_CHANGES.md` để nhóm duyệt.
