# Báo cáo Phase 8 — Quantitative verification tools

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.2, 10.1, 10.2, 12, 18, 20 (Phase 8), 23
- **Trạng thái:** phần việc của Claude đã xong, qua 1 vòng review độc lập, lỗi HIGH/MEDIUM đã sửa. **Chưa COMPLETED**: chưa có dữ liệu giá bên ngoài (nhóm thu thập), Phase 6 chưa có giả thuyết `reviewed`, Phase 3–5 còn điểm tạm. Phase 8 xây trên đầu vào chưa xác minh hoàn toàn (spec mục 28.3); nhóm đã quyết định đi tiếp.

## ⚠ Điều nhóm cần biết

- **Không có dữ liệu giá bên ngoài trong repo.** Mọi check dùng giá (`lagged_external_series`, `percentile_vs_history`, `growth_vs_series`) hiện ra `NOT_EVALUABLE` với lý do "chưa có dữ liệu chuỗi giá…". Nhóm thu thập theo `docs/EXTERNAL_DATA.md` (quặng sắt: World Bank Pink Sheet, spec mục 7.1).
- **Độ trễ giá (`lag_months`: 3, 3, 1, 1, 0) và `publication_lag_days: 35` là đề xuất khởi điểm của Claude**, không phải quyết định của nhóm (spec mục 10, "có độ trễ"; mục 7.2). Cần chốt theo vòng quay tồn kho và nguồn thật.
- **Các ngưỡng `pass_when` trong `library_draft.yaml` (±20%, 0,33, 0,67, 0,3...) vẫn là phỏng đoán của Claude**; Phase 8 chỉ thực thi chúng, không kiểm chúng đúng. Nhóm rà trước khi duyệt giả thuyết.
- **Thư viện chỉ có nguyên nhân của tồn kho TĂNG.** Giả thuyết tồn kho không được chạy trên ca tồn kho GIẢM (`applicable_directions` trong `configs/verification.yaml`): trên dữ liệu thật 14 cặp bị bỏ qua. Nếu nhóm muốn phân tích tồn kho giảm thì cần soạn giả thuyết cho hướng đó.
- **Nhiều metric chưa trích** (`goods_in_transit`, `accounts_payable`, `inventory_provision_charge`, `segment_revenue`, `export_revenue_share`, `production_volume`, `depreciation`, `cip_transfer_to_fixed_assets`): check dùng chúng `NOT_EVALUABLE` tới khi Phase 3 trích thêm. Trên dữ liệu thật: 192 kết quả check = 49 PASS, 29 FAIL, 114 NOT_EVALUABLE (phần lớn do thiếu giá và metric).

## Phase này để làm gì?

Bước 3a của pipeline (spec mục 6, 10.2): biến các `checks` quant của thư viện giả thuyết thành phép tính thật bằng code (không LLM), trả `PASS | FAIL | NOT_EVALUABLE` kèm giá trị và nguồn đầu vào; thiếu dữ liệu không bao giờ thành 0 hay FAIL.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `contracts.CheckResult`, `CheckInput`, `CheckStatus` (mục 18) | Trạng thái, giá trị từng thành phần, đầu vào có nguồn (`Provenance` của fact hoặc mô tả file giá + cờ), `reason`. `NOT_EVALUABLE` bắt buộc có lý do. |
| `verification/tools.py` | **Registry 13 tool**, mọi phép tính bằng `Decimal`: `growth_yoy`, `growth_gap`, `days_inventory_change` (số ngày thực của năm tài chính), `ratio` (kỳ riêng cho tử/mẫu), `ratio_change`, `ratio_of_changes` (chế độ `change`/`level`), `share_of_change`, `share_shift`, `growth_vs_peer`, `growth_vs_history`, `lagged_external_series`, `percentile_vs_history`, `growth_vs_series`. Tên khớp trường `tool` trong thư viện; `known_tools` trong `configs/hypotheses.yaml` được test so với registry. |
| `verification/evaluate.py` | `pass_when` là **dữ liệu**: `op` (`> >= < <=`), `value`, cờ `abs`, `any`, `signed_by_anomaly_direction`, `signed_opposite_to_anomaly_direction`. `validate_check` trả danh sách lỗi (kiểu tham số, tên metric đã biết, tên chuỗi giá, cờ lạ) mà không văng lỗi thô. `run_check`: sai cấu hình → lỗi lớn tiếng; thiếu dữ liệu → `NOT_EVALUABLE`. `hypothesis_applies` lọc theo hướng. |
| Quy tắc gộp thành phần | Mặc định "tất cả": có thành phần trượt → FAIL; mọi thành phần tính được đều đạt nhưng còn thành phần thiếu → **NOT_EVALUABLE** (không PASS với nửa dữ liệu). `any`: một thành phần đạt → PASS; không có thành phần nào đạt mà còn thiếu → NOT_EVALUABLE. |
| `verification/data.py` | `FactStore` (chống rò rỉ): bỏ fact năm > kỳ đang xét; số so sánh (`comparative_only`) của năm ≥ kỳ đang xét coi là chưa có; năm ≥ mốc tham chiếu bị loại; giá trị không hữu hạn bỏ qua; khóa trùng mâu thuẫn bị loại **và báo rõ là mâu thuẫn**. `ExternalStore`: CSV tháng, chặt (ngày hợp lệ, giá dương, không lặp tháng). |
| Chống rò rỉ giá (spec 7.2) | Giá tháng M chỉ dùng nếu cuối tháng M + `publication_lag_days` ≤ as-of. Phân vị chỉ trên lịch sử đã công bố đến cuối năm tài chính. |
| Tài khoản đối ứng | `contra_metrics: [inventory_provision]`: tăng trưởng tính trên độ lớn (dự phòng lưu số âm; trước khi sửa, hai check dự phòng luôn NOT_EVALUABLE). |
| `scripts/run_quant_checks.py` | Chạy check của mọi giả thuyết (kể cả `draft`, để nhóm xem) trên các bất thường ứng viên thật → `data/processed/quant_check_results.csv`. |

## 2. File

- **Tạo:** `src/steel_xai/verification/{settings,data,context,tools,evaluate}.py`, `configs/verification.yaml`, `scripts/run_quant_checks.py`, `docs/EXTERNAL_DATA.md`, `tests/test_verification.py`, `tests/test_verification_review.py`, `docs/phase_reports/PHASE_8.md`.
- **Sửa:** `contracts.py` (`CheckStatus`, `CheckInput`, `CheckResult`), `config.py` (`verification` vào `CONFIG_NAMES`), `configs/hypotheses.yaml` (`known_tools` = registry), `verification/__init__.py`, `hypotheses/library_draft.yaml` (xem mục 3).

## 3. Thay đổi `hypotheses/library_draft.yaml` (chỉ phần `checks` do Claude đề xuất)

Review phát hiện vài check ngữ nghĩa sai; đã chỉnh trong khối `checks` (nội dung nhóm viết không đổi): INV-1 và GM-1 dùng `any: true` cho "quặng sắt **hoặc** HRC" / "HRC hoặc thép xây dựng"; GM-3 `input_price_moved` đổi sang `signed_by_anomaly_direction` (giá đầu vào đổi cùng chiều biên gộp, bỏ `abs` để khỏi lẫn với GM-2); GM-4 `provision_charge_large` thêm `numerator_mode: level` (chi phí trích lập là dòng chảy, so với mức thay đổi lợi nhuận gộp); GM-7 `cip_transfer_*` dùng `denominator_period: prior_year_end` (luồng trong năm / số dư đầu kỳ). Nhóm nên đọc lại các check này.

## 4. Quyết định thiết kế (nhóm nên đọc)

- **Chưa kết luận thì NOT_EVALUABLE, không PASS/FAIL đoán.** Đây là luật quan trọng nhất cho false-SUPPORTED (spec mục 15).
- **Sai tên metric là lỗi lớn tiếng** (so với danh sách metric của Phase 3 + `planned_metrics`), không lẫn với "thiếu dữ liệu".
- **`peer` chỉ dùng công ty đã công bố tại as-of** (cần map ngày công bố); công ty chưa biết ngày, công bố muộn hơn, hoặc năm tài chính kết thúc muộn hơn bị loại và ghi vào `reason` kể cả khi check PASS.
- **Check quant chỉ nhận fact `OK`**; không nhận giá trị mâu thuẫn trùng khóa, không hữu hạn, hoặc có ngày kết thúc năm tài chính không nhất quán giữa các fact.
- **`pass_when.value` phải là số thật** (không chuỗi/bool/NaN), tránh YAML tạo giá trị lạ.

## 5. Tests

`tests/test_verification.py` (~55) + `tests/test_verification_review.py` (~60). Toàn repo **765 passed**; `ruff check`/`ruff format --check` sạch. Gồm: giá trị tính tay cho **mọi** tool (kể cả năm nhuận, trung vị peer, z-score mẫu); NOT_EVALUABLE cho thiếu/≤0/không đổi/lịch sử ngắn; điều kiện (biên, `abs`, dấu theo hướng, `any` vs tất cả, thành phần thiếu); rò rỉ (số so sánh, năm tương lai, năm tham chiếu, peer công bố muộn, giá chưa công bố tại as-of, phân vị không dùng giá tương lai); CSV giá chặt; `validate_check` không văng lỗi thô với dữ liệu rác; registry khớp thư viện và config; nhất quán với Phase 5 (`inv_minus_rev_growth`, `dio_change_days`, `inv_growth_yoy`, `gm_change_pp`) cả trên dữ liệu tổng hợp lẫn thật (7/7 ca khớp, lệch < 1e-5).

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
