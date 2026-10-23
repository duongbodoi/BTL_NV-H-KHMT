# Báo cáo Phase 3 — Financial metric normalization

- **Ngày:** 2026-10-23
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.1, 16, 17, 18, 20 (Phase 3), 23
- **Trạng thái:** phần việc của Claude đã xong, đã qua 1 vòng review độc lập và chạy lại regression. **Chưa COMPLETED** cho tới khi nhóm spot-check và commit (spec mục 25, 28.2). Phase 2 cũng chưa được nhóm đánh dấu COMPLETED; Phase 3 xây trên đầu ra của nó.

---

## Phase này để làm gì?

Phase 2 cho ra giá trị số thô: mỗi ô có nhãn dòng, kỳ, đơn vị, cờ. Phase 3 biến chúng thành **fact có khóa (công ty, kỳ, metric)** với số tiền quy về VND, để Phase 4 (validation) và Phase 5 (anomaly) dùng được.

| Phase sau | Dùng kết quả Phase 3 để |
|---|---|
| Phase 4 | kiểm tổng thành phần (`inventory_total` so với tổng thành phần), đối chiếu BCTC gốc qua `provenance`, xử lý `rejections` / `conflicts` |
| Phase 5 | tính tăng trưởng, số ngày tồn kho, biên gộp từ fact `OK`; fact khác `OK` thành `MISSING` / `NOT_EVALUABLE` |
| Phase 8 | check định lượng đọc fact thay vì đọc bảng thô |

Nguyên tắc: **không biết thì để trống, không đoán** (spec mục 23). Dấu `-` giữ `NOT_APPLICABLE`, không thành 0. Nguồn lệch nhau thì `UNRESOLVED`, không tự chọn.

## 1. Tóm tắt: đã làm được gì

| Đã làm được | Để làm gì |
|---|---|
| `configs/normalization.yaml`: từ điển nhãn cho 9 metric (`inventory_total`, `raw_materials`, `finished_goods`, `work_in_progress`, `construction_in_progress`, `net_revenue`, `cogs`, `gross_profit`, `capex`), mã số TT200, bộ lọc cột Giá gốc/Dự phòng, bảng quy đổi đơn vị, danh sách cờ chặn. | Mọi từ điển nằm trong config, không nằm trong code (spec mục 16). |
| `normalization/mapping.py`: khớp **trọn** nhãn dòng (bỏ số thứ tự đầu dòng, chú thích và công thức cuối dòng), kiểm mã số, cột, ngữ cảnh bảng, kỳ, đơn vị; quy đổi về VND bằng `Decimal`. | Gán đúng ô vào đúng metric; "Doanh thu thuần ra bên ngoài" của bảng bộ phận không bị nhận nhầm là doanh thu thuần. |
| Khóa kỳ: `FY2023` (số dòng cả năm), `2023Q3` (số dòng của quý, hoặc số dư cuối quý). Số dư chỉ nhận ngày cuối quý theo lịch. Số dòng cần năm hoặc quý rõ ràng. | So sánh được giữa OCR và CSV; cột số dư đầu năm (`1/1/2024`) không bị lẫn thành kỳ khác. |
| Mỗi giá trị khớp nhãn hoặc thành `Candidate`, hoặc vào `rejections` kèm lý do (`code_mismatch`, `column_mismatch`, `table_context_mismatch`, `date_not_quarter_end`, `flow_duration_unknown`, `ragged_rows`, `period_*`…). Không có đường thứ ba. Test bất biến kiểm điều này. | Không bỏ im lặng (spec mục 23). |
| `normalization/resolve.py`: gộp candidate. Nguồn có cấu trúc ưu tiên hơn OCR. Hai nguồn OK lệch nhau, hai tài liệu OCR lệch nhau (số trình bày lại), hoặc một nguồn ghi `-` còn nguồn kia có số → `UNRESOLVED` kèm bản ghi `Conflict` giữ đủ các giá trị. Cấu trúc `UNRESOLVED` mà OCR có số → dùng OCR kèm cờ. | Quy tắc ưu tiên nguồn có test, mâu thuẫn không bị chọn im lặng (spec mục 7.1). |
| Dấu: `capex` ghi âm trên báo cáo lưu chuyển tiền tệ → fact lưu độ lớn dương (cờ `sign_flipped`); số dương bất ngờ hoặc `cogs` âm → `UNRESOLVED`. | Không đảo dấu im lặng. |
| `gross_margin = gross_profit / net_revenue` do code tính bằng `Decimal`, `source="derived"`, `inputs` ghi khóa đầu vào. Thiếu đầu vào → `MISSING`; đầu vào `UNRESOLVED` → `UNRESOLVED`; doanh thu bằng 0 → `UNRESOLVED`. | Biên gộp cho Phase 5, không dùng LLM (spec mục 6). |
| `FinancialFact` trong `contracts.py` (spec mục 18), có `to_dict`/`from_dict`; bất biến `value is not None` ⇔ `status == OK`. | Contract dùng chung. |

Kết quả trên fixture HPG (chỉ là ví dụ): 15 fact (13 từ OCR, 2 `gross_margin` dẫn xuất), không có conflict. Giá trị nguyên vật liệu tại 31/12/2024 là `20.324.528.389.776` VND (trang 40), khớp số đã dùng để spot-check Phase 2. Biên gộp FY2024 = 18.497.549.127.684 / 138.855.112.131.387 ≈ 0,1332.

## 2. File tạo

- `configs/normalization.yaml`
- `src/steel_xai/normalization/settings.py`
- `src/steel_xai/normalization/mapping.py`
- `src/steel_xai/normalization/resolve.py`
- `src/steel_xai/normalization/pipeline.py`
- `tests/test_normalization.py`
- `docs/phase_reports/PHASE_3.md`

## 3. File sửa

- `src/steel_xai/contracts.py`: thêm `FinancialFact`.
- `src/steel_xai/config.py`: thêm `"normalization"` vào `CONFIG_NAMES`.
- `src/steel_xai/normalization/__init__.py`: export API.
- `README.md`, `AI_USAGE_LOG.md`

## 4. Quyết định thiết kế

- **Khớp nhãn trọn, không khớp chuỗi con**, cộng mã số khi dòng có mã. Metric có `codes` mà dòng OCR không có mã → loại (`code_missing`). Lý do: cùng nhãn "Hàng tồn kho" ở mã 140 (số thuần) và 141 (số gốc), và mã 10 vừa là doanh thu thuần vừa là "Biến động hàng tồn kho" trên báo cáo lưu chuyển tiền tệ.
- **Thành phần tồn kho lấy cột Giá gốc**, không lấy sau dự phòng, và chỉ từ bảng có dòng "Thành phẩm" (tách khỏi bảng chi phí dở dang dài hạn cùng nhãn). `inventory_total` là số thuần trên bảng cân đối (mã 140).
- **Số dư chỉ nhận ngày cuối quý theo lịch.** Cột `1/1/2024` bị loại (`date_not_quarter_end`) thay vì suy thành 31/12/2023, vì số đầu năm của báo cáo sau có thể đã trình bày lại.
- **CSV chỉ có năm:** số dư gán `Q4`, số dòng gán `FY`.
- **Số dòng có ngày trần** (ví dụ `30/6/2023`) bị loại; phải có từ "năm" mới là số cả năm. Chuỗi "31 tháng 12" được hiểu là ngày, không phải độ dài kỳ.
- **`-` ở một nguồn còn nguồn khác có số = mâu thuẫn** (`not_applicable_vs_value`). Ô trống ở một nguồn thì không chặn nguồn kia (thiếu dữ liệu, không phải khẳng định).
- **CSV với metric `flip_sign`:** lấy độ lớn và ghi `sign_not_checked`, vì quy ước dấu của nguồn do nhóm chốt sau. Metric chỉ có `expected_sign` (ví dụ `cogs`) vẫn bị kiểm dấu ở cả hai nguồn.
- **Dung sai so nguồn mặc định 0** (phải bằng nhau tuyệt đối sau quy đổi VND); config bắt buộc thuộc `[0, 1)`.
- **Config được kiểm chặt:** cờ chặn gõ sai tên, alias trùng, đơn vị đích khác 1, tháng quý lạ, tử/mẫu dẫn xuất khác `kind`, chuỗi rỗng trong bộ lọc cột đều báo `ConfigError`.
- **`structured_aliases` để trống** vì nhóm chưa chốt nguồn CSV; test dùng alias tổng hợp.

## 5. Tests

71 test case mới (tổng 366). Nhóm chính: nhãn và config; fact từ bảng OCR (số dư, số dòng, mã, cột, ngữ cảnh bảng, đơn vị, dấu, trạng thái ô); CSV (quý, năm, đơn vị, trạng thái, công ty thiếu, khóa trùng); gộp nguồn (ưu tiên, lệch, dung sai, dự phòng OCR, `-` vs số, hai tài liệu lệch, đồng ý); `gross_margin` (chính xác `Decimal`, âm, thiếu, bằng 0, lan truyền `UNRESOLVED`); bất biến (mỗi giá trị khớp nhãn thành đúng một candidate hoặc rejection; deterministic và không phụ thuộc thứ tự; roundtrip JSON); hồi quy từ review; tích hợp HPG.

## 6. Commands đã chạy

```bash
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

## 7. Kết quả

| Passed | Skipped | Failed |
|---|---|---|
| 366 | 0 | 0 |

Ruff check: sạch. Ruff format: đã chạy `ruff format`, không còn thay đổi.

## 8. Known limitations

- **Quý theo lịch.** Công ty có năm tài chính lệch lịch (ví dụ kết thúc 30/9) sẽ có khóa kỳ sai quý. Nhóm xử lý khi chốt danh sách công ty. Chưa có cấu hình năm tài chính theo công ty.
- **CSV chỉ có năm** luôn gán số dư vào `Q4` (cùng giả định lịch).
- **Báo cáo năm sau trình bày lại số năm trước** sẽ thành `ocr_conflict` / `UNRESOLVED`, chưa có quy tắc ưu tiên bản mới hơn. Nhóm cần quyết định cách xử lý số trình bày lại.
- **Số đầu năm bị loại.** Một báo cáo đơn lẻ chỉ cho số dư cuối kỳ của năm đó; cần báo cáo năm trước để có kỳ so sánh.
- **Cột Dự phòng chưa là metric.** `inventory_total` là số thuần; thành phần là giá gốc; hai bên lệch nhau đúng bằng dự phòng và các khoản chưa liệt kê (hàng hóa, công cụ dụng cụ…). Phase 4 phải biết điều này khi kiểm tổng.
- **Từ điển nhãn dựa trên mẫu TT200.** Công ty dùng nhãn khác (ví dụ "Doanh thu thuần" trần) cần bổ sung alias. Metric không có `codes` (các thành phần tồn kho) chỉ được chặn bởi ngữ cảnh bảng và cột, không có mã.
- **`capex` chỉ lấy dòng mua sắm TSCĐ** trên báo cáo lưu chuyển tiền tệ; báo cáo quý lũy kế bị loại (`period_cumulative`).
- **Giả định cờ Phase 2 đủ tin cậy.** Phase 3 kế thừa các giới hạn heuristic của Phase 2 (mục 8 của `PHASE_2.md`).

## 9. Chưa làm vì ngoài scope

- Kiểm tổng thành phần, quan hệ kế toán, báo cáo đối chiếu (Phase 4).
- Phát hiện bất thường, tăng trưởng, số ngày tồn kho (Phase 5).
- Adapter vnstock; Parquet.
- Quy tắc xử lý số trình bày lại; năm tài chính theo công ty.

## 10. Hard-code công ty

Không có. Code và config không chứa tên công ty, trang, dòng hay năm. Nhãn và mã số là quy ước BCTC Việt Nam (TT200) trong `configs/normalization.yaml`. Chỉ test tích hợp cuối `test_normalization.py` chứa kỳ vọng riêng của fixture HPG, với vai trò ví dụ.

## 11. AI_USAGE_LOG.md

Đã thêm mục "2026-10-23 — Phase 3". Review độc lập 1 vòng:

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 1 | 5 | 5 |

HIGH và toàn bộ MEDIUM đã sửa, có test hồi quy. LOW: sửa 3 (test yếu, `cogs` thiếu kiểm dấu, config `quarter_end_months`/`derived`); 2 mục được ghi nhận ở mục 8 (giả định `Q4` cho CSV chỉ có năm; nhãn có chú thích cuối vẫn khớp, được chặn bằng mã số).

Phần người cần xác minh: xem mục tương ứng trong `AI_USAGE_LOG.md`.

## 12. Khác báo cáo giữa kỳ

Không có điểm khác mới. Không sửa `DEVIATIONS_FROM_MIDTERM.md`.
