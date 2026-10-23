# Báo cáo Phase 4 — Validation (tối giản)

- **Ngày:** 2026-10-23
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.3, 18, 20 (Phase 4), 23
- **Trạng thái:** phần việc của Claude đã xong, đã qua 1 vòng review độc lập. **Chưa COMPLETED**: cần nhóm spot-check, điền mẫu đối chiếu và commit. Phase 4 xây trên Phase 2-3 mà nhóm chưa đối chiếu với PDF gốc (nhóm hoãn, theo chỉ đạo), nên báo cáo này **chuyển tiếp chưa xác minh** các số đó.

## Phase này để làm gì?

Dữ liệu chỉ có từ OCR, không có nguồn có cấu trúc để đối chiếu chéo, nên chất lượng số phải được chứng minh bằng (1) quan hệ kế toán giữa các số, (2) nhất quán năm tài chính, (3) đối chiếu ngẫu nhiên với BCTC gốc do người thực hiện. Phase 4 gắn cờ vào fact; **không sửa số, không đổi trạng thái**.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `configs/validation.yaml` | Quan hệ kế toán nằm trong dữ liệu: `gross_profit = net_revenue - cogs`; `inventory_total = inventory_gross + inventory_provision`; Σ(nguyên vật liệu, thành phẩm, dở dang) ≤ `inventory_gross`. Dung sai **0,1%**. `nil_as_zero: true`. |
| `validate_facts` | Mỗi check ra `PASS` / `FAIL` / `NOT_EVALUABLE` kèm giá trị đầu vào và danh sách đầu vào thiếu. `FAIL` gắn `validation_failed:<quan_hệ>` lên mọi fact đầu vào. |
| `-` khi cộng tổng | Coi là 0 **và gắn cờ `nil_as_zero`** lên fact đó (chỉ khi check thực sự được tính). Cấu hình tắt được. Thiếu (`MISSING`/`UNKNOWN`), `UNRESOLVED` hoặc không có fact **không bao giờ thành 0**: check `NOT_EVALUABLE` và liệt kê thiếu gì. HPG 2022 (không có thuyết minh tồn kho) ra `raw_materials / finished_goods / work_in_progress = MISSING`. |
| Chặn trên | Nếu các thành phần đã biết đã vượt tổng thì `FAIL` dù còn thiếu thành phần khác (cờ `partial_sum`). |
| Năm tài chính | Các đầu vào của một quan hệ khác ngày khóa sổ → `NOT_EVALUABLE`. `fiscal_end_consistent`: ngày khóa sổ của từng công ty ổn định qua các năm (so "cuối tháng M", không so ngày nên 28/29 tháng 2 không bị báo thay đổi). `peer_fiscal_end_aligned`: giữa các công ty cùng `FY`; khác nhau (ví dụ HSG 30/9 so với HPG 31/12) → cờ `peer_fiscal_end_differs` lên các fact liên quan. |
| `non_negative` | Doanh thu thuần, tồn kho (thuần và giá gốc) không âm. Không áp cho giá vốn/capex vì chuẩn hóa đã đổi về độ lớn dương. |
| Đối chiếu mẫu | `draw_sample(facts, n, seed)` bốc **20** fact OK (không phải số dẫn xuất) ngẫu nhiên nhưng tái lập; `write_sample_template` ghi CSV có cột `reference_value`/`reference_note` để **người** điền số trên BCTC gốc; `reconciliation_report` + `render_markdown` so sánh. Số tham chiếu đọc theo định dạng VN; `1,000` mơ hồ hoặc phần trăm bị từ chối; metric lưu độ lớn (giá vốn, capex) so theo trị tuyệt đối; dòng trùng khóa chỉ tính một lần; fact chưa OK được báo riêng. Cỡ mẫu chưa đủ 20 → `sample_size_ok = false`, in rõ. |

Kết quả trên 5 fixture: mọi đẳng thức lợi nhuận gộp và tồn kho thuần **PASS** khi đủ dữ liệu, trùng số tính tay. Bất biến chặn trên PASS trên HPG 2024 và NKG 2019. NKG 2019 `NOT_EVALUABLE` (doanh thu thuần là ô dồn số; giá vốn và dự phòng không trích được). Chưa FAIL nào trên dữ liệu thật.

## 2. File

- **Tạo:** `configs/validation.yaml`; `src/steel_xai/validation/{settings,checks,reconciliation}.py`; `tests/test_validation.py`; `tests/test_review_round2.py` (nửa Phase 4); `docs/phase_reports/PHASE_4.md`.
- **Sửa:** `validation/__init__.py`, `config.py` (thêm `validation` vào `CONFIG_NAMES`), `README.md`, `AI_USAGE_LOG.md`.

## 3. Quyết định thiết kế

- **Không có `reconciliation_report` tự điền.** Claude không biết số trên PDF; chỉ bốc mẫu và so sánh với số nhóm nhập (spec mục 21.1).
- **Dung sai 0,1% cho cả ba quan hệ**; thành phần tồn kho là giá gốc, tổng là số thuần nên so qua `inventory_gross` và `inventory_provision` thay vì so trực tiếp.
- **Config bắt lỗi gõ sai tên metric** bằng cách đối chiếu mặc định với `normalization.yaml`.
- **Số so sánh (`result.comparatives`) không đi qua validation**; chỉ fact chính.

## 4. Tests

Tối giản theo yêu cầu: 39 test trong `test_validation.py` + 19 test hồi quy trong `test_review_round2.py` (khoảng 30 test thuộc Phase 4), gồm: quan hệ PASS/FAIL/dung sai; `nil_as_zero` bật/tắt; thiếu/UNRESOLVED không thành 0; thành phần thiếu; chặn trên; năm tài chính (peer, thay đổi, năm nhuận); bốc mẫu xác định; báo cáo đối chiếu (khớp, lệch, thiếu, trùng, mơ hồ, độ lớn); xác định và không phụ thuộc thứ tự; ví dụ fixture HPG 2022/2024 và HSG-vs-HPG.

Lệnh: `.venv\Scripts\python -m pytest -q`, `ruff check .`, `ruff format --check .`. Kết quả toàn repo: **448 passed, 0 skipped, 0 failed**; ruff sạch.

## 5. Review độc lập

Vòng 2: 0 CRITICAL, 2 HIGH (Phase 3), 6 MEDIUM, 8 LOW. Liên quan Phase 4 đã sửa: quan hệ vẫn PASS khi đầu vào lệch ngày khóa sổ; năm nhuận bị báo thay đổi ngày khóa sổ; đối chiếu đọc sai dấu số trong ngoặc (giá vốn/capex) và `1,000`; dòng tham chiếu trùng làm tăng cỡ mẫu; gõ sai tên metric không bị bắt; check chặn trên bỏ sót khi thiếu một thành phần; fact `UNRESOLVED` có tham chiếu làm thấp tỷ lệ lệch; `non_negative` vô hiệu cho giá vốn/capex.

## 6. Known limitations

- **Chưa có số liệu đối chiếu thật.** `reconciliation_sample_n = 20` đã đặt, nhưng nhóm chưa điền tham chiếu từ BCTC gốc; tỷ lệ lệch hiện chưa có.
- Chỉ ba quan hệ kế toán. Chưa kiểm tổng bảng cân đối (tài sản = nguồn vốn), chưa kiểm lưu chuyển tiền tệ.
- Thành phần tồn kho chưa liệt kê hết (hàng hóa, công cụ dụng cụ, hàng đang đi đường...), nên chỉ kiểm chặn trên, không kiểm đẳng thức.
- `fiscal_end_consistent` tính ngày "phổ biến nhất" theo số fact, không theo số năm (ca hòa chọn theo thứ tự chữ cái).
- Một đẳng thức mà cả ba vế đều là `-` cho `PASS` 0 = 0 (có cờ `nil_as_zero`): hợp lệ về hình thức nhưng ít ý nghĩa.
- Kế thừa giới hạn chưa phân biệt báo cáo hợp nhất/riêng lẻ của Phase 3.

## 7. Chưa làm vì ngoài scope

Phase 5 (bất thường, peer theo năm tài chính, số ngày tồn kho dùng độ dài năm tài chính thực); điền mẫu đối chiếu; `docs/DATA_CARD.md` (Phase 14).

## 8. Hard-code công ty

Không. Không có tên công ty, trang, năm hay nhãn riêng một công ty trong `src`.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-23 — Phase 3 (cập nhật) và Phase 4" kèm "phần người cần xác minh".

## 10. Khác báo cáo giữa kỳ

Có, xem `docs/DRAFT_SPEC_CHANGES.md` (chờ nhóm duyệt; chưa sửa spec hay `DEVIATIONS_FROM_MIDTERM.md`).
