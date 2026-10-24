# Báo cáo Phase 8 — Quantitative verification tools

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.2, 10.1, 10.2, 12, 18, 20 (Phase 8), 23
- **Trạng thái:** phần việc của Claude đã xong, qua 1 vòng review độc lập, lỗi HIGH/MEDIUM đã sửa. **Chưa COMPLETED**: chưa có dữ liệu giá bên ngoài (nhóm thu thập), Phase 6 chưa có giả thuyết `reviewed`, Phase 3–5 còn điểm tạm. Phase 8 xây trên đầu vào chưa xác minh hoàn toàn (spec mục 28.3); nhóm đã quyết định đi tiếp.

## ⚠ Điều nhóm cần biết

- **Dữ liệu giá mới có quặng sắt** (`data/external/iron_ore_price.xlsx` của nhóm → `iron_ore_price.csv`, 120 tháng 2015-01..2024-12, $/dmtu). **Cập nhật 2026-10-24:** thêm `hrc_price` và `coking_coal_price` từ bảng VSA của nhóm (`data/external/vsa_prices.xlsx`, 2019-01..2022-12, USD/tấn, ô khoảng giá lấy trung bình; xem mục 3c). `steel_price`, `usd_vnd` chưa có nên check dùng chúng vẫn `NOT_EVALUABLE`. Vì chuỗi bắt đầu từ 2015-01, các ca FY2015–FY2016 thường thiếu tháng cho cửa sổ so sánh (ví dụ "chỉ có 6/12 tháng"): nên bổ sung giá từ 2014 nếu muốn dùng các năm đầu. Nhập từ Excel: `scripts/import_price_xlsx.py`, xem `docs/EXTERNAL_DATA.md`.
- **Độ trễ giá (`lag_months`: 3, 3, 1, 1, 0) và `publication_lag_days: 35` là đề xuất khởi điểm của Claude**, không phải quyết định của nhóm (spec mục 10, "có độ trễ"; mục 7.2). Cần chốt theo vòng quay tồn kho và nguồn thật.
- **Các ngưỡng `pass_when` trong `library_draft.yaml` (±20%, 0,33, 0,67, 0,3...) vẫn là phỏng đoán của Claude**; Phase 8 chỉ thực thi chúng, không kiểm chúng đúng. Nhóm rà trước khi duyệt giả thuyết.
- **Hướng của giả thuyết:** mỗi giả thuyết khai `direction` (`up | down | both`) và chỉ chạy trên bất thường cùng hướng (`hypothesis_applies`). Thư viện nhóm cập nhật 2026-10-24 đã có 7 giả thuyết tồn kho GIẢM (DEC-1..7), nên cả hai hướng của tồn kho đều có giả thuyết. Giả thuyết chưa khai `direction` không áp dụng cho hướng nào.
- **Chỉ tiêu Phase 3 đã trích thêm (2026-10-24, chỉ sửa `configs/normalization.yaml`):** `accounts_payable` (mã 311), `goods_in_transit` (dòng thuyết minh hàng tồn kho), `depreciation` và `inventory_change_cf` (báo cáo lưu chuyển tiền tệ; nhận diện bảng bằng nhãn "Lưu chuyển tiền thuần từ hoạt động kinh doanh" vì bảng của NKG không có cột mã số). Fact cũ giữ nguyên 100% (so sánh trước/sau trên 29 tài liệu); giá trị đối chiếu tay với OCR gốc (HPG 2021–2022, HSG 2021–2022) khớp. **Chưa trích được:** NKG cho hai chỉ tiêu lưu chuyển tiền tệ (OCR gộp dòng trong bảng NKG), HSG cho `accounts_payable`/`goods_in_transit` từ 2019 (header hai tầng), và `inventory_provision_charge`, `segment_revenue`, `export_revenue_share`, `production_volume`, `cip_transfer_to_fixed_assets` (cần báo cáo thường niên hoặc không có dạng bảng đơn giản): các check dùng chúng vẫn `NOT_EVALUABLE`.
- Trên dữ liệu thật sau cập nhật (22 giả thuyết, 11 bất thường): 248 kết quả check = 88 PASS, 61 FAIL, 99 NOT_EVALUABLE (phần lớn do thiếu các chuỗi giá còn lại và chỉ tiêu chưa trích). Sau khi thêm HRC và than cốc: **93 PASS, 64 FAIL, 91 NOT_EVALUABLE** (mục 3c).

## Phase này để làm gì?

Bước 3a của pipeline (spec mục 6, 10.2): biến các `checks` quant của thư viện giả thuyết thành phép tính thật bằng code (không LLM), trả `PASS | FAIL | NOT_EVALUABLE` kèm giá trị và nguồn đầu vào; thiếu dữ liệu không bao giờ thành 0 hay FAIL.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `contracts.CheckResult`, `CheckInput`, `CheckStatus` (mục 18) | Trạng thái, giá trị từng thành phần, đầu vào có nguồn (`Provenance` của fact hoặc mô tả file giá + cờ), `reason`. `NOT_EVALUABLE` bắt buộc có lý do. |
| `verification/tools.py` | **Registry 14 tool**, mọi phép tính bằng `Decimal`: `growth_yoy`, `growth_gap`, `days_inventory_change` (số ngày thực của năm tài chính), `ratio` (kỳ riêng cho tử/mẫu), `ratio_change`, `ratio_of_changes` (chế độ `change`/`level`), `share_of_change`, `share_shift`, `growth_vs_peer`, `growth_vs_history`, `lagged_external_series`, `percentile_vs_history`, `growth_vs_series`. Tên khớp trường `tool` trong thư viện; `known_tools` trong `configs/hypotheses.yaml` được test so với registry. |
| `verification/evaluate.py` | `pass_when` là **dữ liệu**: `op` (`> >= < <=`), `value`, cờ `abs`, `any`, `signed_by_anomaly_direction`, `signed_opposite_to_anomaly_direction`. `validate_check` trả danh sách lỗi (kiểu tham số, tên metric đã biết, tên chuỗi giá, cờ lạ) mà không văng lỗi thô. `run_check`: sai cấu hình → lỗi lớn tiếng; thiếu dữ liệu → `NOT_EVALUABLE`. `hypothesis_applies` lọc theo hướng. |
| Quy tắc gộp thành phần | Mặc định "tất cả": có thành phần trượt → FAIL; mọi thành phần tính được đều đạt nhưng còn thành phần thiếu → **NOT_EVALUABLE** (không PASS với nửa dữ liệu). `any`: một thành phần đạt → PASS; không có thành phần nào đạt mà còn thiếu → NOT_EVALUABLE. |
| `verification/data.py` | `FactStore` (chống rò rỉ): bỏ fact năm > kỳ đang xét; số so sánh (`comparative_only`) của năm ≥ kỳ đang xét coi là chưa có; năm ≥ mốc tham chiếu bị loại; giá trị không hữu hạn bỏ qua; khóa trùng mâu thuẫn bị loại **và báo rõ là mâu thuẫn**. `ExternalStore`: CSV tháng, chặt (ngày hợp lệ, giá dương, không lặp tháng). |
| Chống rò rỉ giá (spec 7.2) | Giá tháng M chỉ dùng nếu cuối tháng M + `publication_lag_days` ≤ as-of. Phân vị chỉ trên lịch sử đã công bố đến cuối năm tài chính. |
| Tài khoản đối ứng | `contra_metrics: [inventory_provision]`: tăng trưởng tính trên độ lớn (dự phòng lưu số âm; trước khi sửa, hai check dự phòng luôn NOT_EVALUABLE). |
| `verification/price_import.py`, `scripts/import_price_xlsx.py` | Nhập giá tháng từ `.xlsx` (thư viện chuẩn): ngày `2015M01`/`2015-01`/số ngày Excel, bỏ dòng tiêu đề, mọi dòng dữ liệu sai làm cả lần nhập lỗi (nêu số dòng). |
| `scripts/run_quant_checks.py` | Chạy check của mọi giả thuyết (kể cả `draft`, để nhóm xem) trên các bất thường ứng viên thật → `data/processed/quant_check_results.csv`. |

## 2. File

- **Tạo:** `src/steel_xai/verification/{settings,data,context,tools,evaluate}.py`, `configs/verification.yaml`, `scripts/run_quant_checks.py`, `docs/EXTERNAL_DATA.md`, `tests/test_verification.py`, `tests/test_verification_review.py`, `docs/phase_reports/PHASE_8.md`.
- **Sửa:** `contracts.py` (`CheckStatus`, `CheckInput`, `CheckResult`), `config.py` (`verification` vào `CONFIG_NAMES`), `configs/hypotheses.yaml` (`known_tools` = registry), `verification/__init__.py`, `hypotheses/library_draft.yaml` (xem mục 3).

## 3. Thay đổi `hypotheses/library_draft.yaml` (chỉ phần `checks` do Claude đề xuất)

Review phát hiện vài check ngữ nghĩa sai; đã chỉnh trong khối `checks` (nội dung nhóm viết không đổi): INV-1 và GM-1 dùng `any: true` cho "quặng sắt **hoặc** HRC" / "HRC hoặc thép xây dựng"; GM-3 `input_price_moved` đổi sang `signed_by_anomaly_direction` (giá đầu vào đổi cùng chiều biên gộp, bỏ `abs` để khỏi lẫn với GM-2); GM-4 `provision_charge_large` thêm `numerator_mode: level` (chi phí trích lập là dòng chảy, so với mức thay đổi lợi nhuận gộp); GM-7 `cip_transfer_*` dùng `denominator_period: prior_year_end` (luồng trong năm / số dư đầu kỳ). Nhóm nên đọc lại các check này.

## 3c. Cập nhật 2026-10-24 (giá HRC và than cốc từ VSA)

- Nhóm thêm `Giá nguyên vật liệu.xlsx` (chuyển thành `data/external/vsa_prices.xlsx`), xác nhận "thép cuộn" = HRC và chọn **trung bình hai đầu** cho ô khoảng giá. Importer có hai tùy chọn mới, mặc định tắt: `midpoint_ranges` (ô `a-b`, hậu tố `USD/T`; khoảng ngược hoặc chữ lạ vẫn lỗi) và `trailing_empty_ok` (chỉ bỏ dòng thiếu giá ở **cuối** bảng). Test mới trong `tests/test_price_import.py`, có test CSV khớp xlsx.
- 8 check đổi từ `NOT_EVALUABLE`: 5 → PASS, 3 → FAIL, đều ở FY2021–FY2022 (HPG FY2021/FY2022, HSG FY2022, NKG FY2021). Các ca FY2016–FY2018 không đổi vì chuỗi bắt đầu 2019-01; `input_price_low` của HSG FY2021 vẫn `NOT_EVALUABLE` (lịch sử HRC 33 < 36 tháng).
- Ảnh hưởng tới Phase 10 (lần chạy thử, chỉ định lượng): 7 → 9 SUPPORTED, 18 → 15 PARTIAL, 29 → 30 CONTRADICTED. `margin_selling_price` lên SUPPORTED (hạng 1) ở HPG FY2022 và NKG FY2021, nhưng xuống CONTRADICTED ở HSG FY2022 (HRC tăng 9,7% trong năm tài chính 10/2021–9/2022 trong khi biên gộp giảm).
- Chưa làm: giá 2015–2018 và 2023, link nguồn từng tháng, chuỗi phế liệu.

## 3b. Cập nhật 2026-10-24 (sau khi nhóm thêm dữ liệu giá và thư viện mới)

- **Thư viện mới (`library_draft_new.yaml` → `hypotheses/library_draft.yaml`)**: 22 giả thuyết (15 cũ không đổi nội dung + 7 giả thuyết giảm tồn kho DEC-1..7), trường mới `direction`. Claude soạn `checks` cho DEC-1..7 (đề xuất, nhóm rà); điều kiện của chúng viết thẳng theo chiều giảm, không dùng cờ có dấu. Đã kiểm tự động: nội dung nhóm viết giữ nguyên.
- **Tool mới `metric_level`** (giá trị metric đúng dấu báo cáo, cho `inventory_change_cf`: dương nghĩa là hàng tồn kho giảm, giải phóng tiền).
- **Bỏ `applicable_directions` khỏi `configs/verification.yaml`** vì hướng nay nằm ở chính giả thuyết (một nguồn sự thật).
- **Phần cập nhật này CHƯA qua review độc lập** (importer, trường `direction`, DEC checks, cấu hình trích chỉ tiêu mới). Test tự động: 786 passed.

## 4. Quyết định thiết kế (nhóm nên đọc)

- **Chưa kết luận thì NOT_EVALUABLE, không PASS/FAIL đoán.** Đây là luật quan trọng nhất cho false-SUPPORTED (spec mục 15).
- **Sai tên metric là lỗi lớn tiếng** (so với danh sách metric của Phase 3 + `planned_metrics`), không lẫn với "thiếu dữ liệu".
- **`peer` chỉ dùng công ty đã công bố tại as-of** (cần map ngày công bố); công ty chưa biết ngày, công bố muộn hơn, hoặc năm tài chính kết thúc muộn hơn bị loại và ghi vào `reason` kể cả khi check PASS.
- **Check quant chỉ nhận fact `OK`**; không nhận giá trị mâu thuẫn trùng khóa, không hữu hạn, hoặc có ngày kết thúc năm tài chính không nhất quán giữa các fact.
- **`pass_when.value` phải là số thật** (không chuỗi/bool/NaN), tránh YAML tạo giá trị lạ.

## 5. Tests

`tests/test_verification.py` (~55) + `tests/test_verification_review.py` (~60). Toàn repo **786 passed** (kèm `tests/test_price_import.py`); `ruff check`/`ruff format --check` sạch. Gồm: giá trị tính tay cho **mọi** tool (kể cả năm nhuận, trung vị peer, z-score mẫu); NOT_EVALUABLE cho thiếu/≤0/không đổi/lịch sử ngắn; điều kiện (biên, `abs`, dấu theo hướng, `any` vs tất cả, thành phần thiếu); rò rỉ (số so sánh, năm tương lai, năm tham chiếu, peer công bố muộn, giá chưa công bố tại as-of, phân vị không dùng giá tương lai); CSV giá chặt; `validate_check` không văng lỗi thô với dữ liệu rác; registry khớp thư viện và config; nhất quán với Phase 5 (`inv_minus_rev_growth`, `dio_change_days`, `inv_growth_yoy`, `gm_change_pp`) cả trên dữ liệu tổng hợp lẫn thật (7/7 ca khớp, lệch < 1e-5).

Lệnh: `.venv\Scripts\python -m pytest -q`, `scripts/run_quant_checks.py`.

## 6. Review độc lập

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 2 | 7 | 9 |

Reviewer không thấy rò rỉ as-of nào; mọi phép tính dùng `Decimal`; chia cho 0/âm/Δ=0 đều ra NOT_EVALUABLE; DIO năm nhuận và cửa sổ giá theo tháng 9 (HSG) đúng. Đã sửa:
- HIGH: `inventory_provision` lưu số âm khiến 2 check (INV-3 `provision_up`, GM-4 `provision_moves`) luôn NOT_EVALUABLE (nay so độ lớn); giả thuyết "tồn kho tăng" chạy trên ca tồn kho giảm và ra PASS ủng hộ nhờ cờ có dấu (nay lọc theo hướng).
- MEDIUM: `ratio` dùng cùng kỳ cho tử và mẫu (nay tách `numerator_period`/`denominator_period`); thành phần thiếu vẫn ra PASS (nay NOT_EVALUABLE); GM-3 `abs` lẫn với GM-2; `ratio_of_changes` không hợp ý GM-4 (thêm chế độ `level`); `validate_check` văng lỗi thô (nay trả lỗi); typo metric lẫn với thiếu dữ liệu (nay lỗi lớn tiếng); số ngày của DIO không có trong `inputs` (nay ghi).
- LOW: input thừa của peer/cửa sổ giá khi tính thất bại; `fiscal_end` lấy từ metric bất kỳ (nay kiểm nhất quán giữa các fact); cửa sổ giá thiếu tháng không cảnh báo (nay cờ `partial_window` + số tháng); docstring phân vị; xung đột trùng khóa báo "thiếu" (nay nêu mâu thuẫn); fact NaN/Infinity; `pass_when.value` kiểu chuỗi; CSV giá kiểm ngày lỏng.
- Chưa sửa (ghi nhận): `percentile_vs_history` đếm cả chính tháng cuối nên luôn > 0 (điều kiện `<= 0` không bao giờ đạt, ngưỡng thấp nhất khả dĩ ≈ 1/n).

## 7. Known limitations

- Không có dữ liệu giá: phần lớn check giá `NOT_EVALUABLE` (mục đầu báo cáo).
- Check phụ thuộc chỉ tiêu Phase 3 chưa trích `NOT_EVALUABLE`; muốn đủ phải mở rộng `configs/normalization.yaml` (việc của Phase 3, không làm ở đây).
- Dữ liệu năm: không kiểm được tính mùa vụ bằng số (INV-4 chỉ có check văn bản).
- Chưa có check so peer/lịch sử trong thư viện: `growth_vs_peer` và `growth_vs_history` có trong registry và có test nhưng chưa giả thuyết nào dùng (spec mục Phase 8 liệt kê "so peer, so lịch sử").
- Mốc as-of tạm (ngày ký kiểm toán) ảnh hưởng lọc peer và giá giống Phase 5/7.
- Thực thi check `text` (RAG) và gộp trạng thái SUPPORTED/PARTIAL/CONTRADICTED/UNVERIFIED thuộc Phase 9–10.

## 8. Chưa làm vì ngoài scope

Gộp trạng thái giả thuyết và xếp hạng (Phase 10); LLM (Phase 9); thu thập dữ liệu giá (nhóm); trích thêm metric ở Phase 3; sửa ngưỡng `pass_when` (nhóm).

## 9. Hard-code công ty

Không. Danh sách công ty, mốc năm đọc từ `configs/dataset.yaml`; tên chuỗi giá, độ trễ, cửa sổ, ngưỡng tối thiểu nằm trong `configs/verification.yaml`.

## 10. AI_USAGE_LOG.md

Đã thêm mục "2026-10-24 — Phase 8".

## 11. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới. Nếu giữa kỳ hứa kiểm chứng dựa trên giá nguyên liệu thì việc chưa có dữ liệu giá nên được nêu ở data card.
