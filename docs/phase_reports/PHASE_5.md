# Báo cáo Phase 5 — Anomaly engine

- **Ngày:** 2026-10-23
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 3.2, 3.3, 7.2, 8, 20 (Phase 5), 23
- **Trạng thái:** **COMPLETED** (2026-10-24, theo yêu cầu của nhóm). Đã qua 2 vòng review độc lập, mọi lỗi HIGH/MEDIUM đã sửa. Lưu ý: Phase 3-4 chưa được nhóm đánh dấu COMPLETED (chưa spot-check PDF, chưa điền mẫu đối chiếu), nên Phase 5 xây trên đầu vào chưa xác minh hoàn toàn (spec mục 28.3); nhóm đã quyết định đi tiếp.

## ⚠ Ngưỡng và as-of vẫn là tạm

- **Ngưỡng** trong `configs/anomaly.yaml` là đề xuất của Claude (đã nâng ngày 2026-10-24), không phải quyết định của nhóm (spec mục 21.1); `provisional_thresholds: true`. Trên cửa sổ chọn ca 2016–2023 (3 công ty, 20–23 kỳ xét được mỗi loại), tỷ lệ kỳ bị gắn cờ: **tồn kho 7/23 (30%), biên gộp 4/20 (20%)**, giảm từ ~70% với ngưỡng đầu. Nhóm vẫn nên rà lại trên tập dev trước khi chạy test. Test dùng ngưỡng cố định ở `tests/fixtures/anomaly_config/`, không phụ thuộc file này.
- **As-of:** nhóm chỉ có năm công bố, không có ngày. Tạm dùng **ngày ký báo cáo kiểm toán** trích tự động từ tài liệu (`asof_source: audit_report_date`; xem `data/processed/asof_dates.csv`). Đây là xấp xỉ, thường sớm hơn hoặc bằng ngày công bố thật; Phase 7 nên coi là mốc tạm. Còn 1 kỳ không trích được (HSG FY2015, ngoài cửa sổ chọn ca).

## Phase này để làm gì?

Bước 1 của pipeline (spec mục 6): từ `FinancialFact` của Phase 3-4, phát hiện hai loại bất thường (tồn kho thay đổi mạnh, biên lợi nhuận gộp thay đổi mạnh), hai chiều, bằng code deterministic, không LLM, và xuất danh sách ứng viên để **người** chọn 18 ca (Claude không chọn ca).

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `contracts.Anomaly` (spec mục 18) | company, period, type, direction (`up`/`down`), indicators, thresholds_hit, baseline_used, severity, flags, asof_date; `to_dict`/`from_dict`. |
| `configs/anomaly.yaml` | Mỗi loại có `requires` (chỉ báo bắt buộc) và `rules` hai phía (`up` > 0, `down` < 0). Nhóm chỉnh ngưỡng tại đây. Validate chặt: thiếu một phía, chỉ báo lạ/lặp, loại lặp... đều `ConfigError`. |
| Chỉ báo tồn kho | tăng trưởng YoY; tăng trưởng tồn kho trừ tăng trưởng doanh thu; **số ngày tồn kho** = tồn kho / giá vốn × số ngày thực của năm tài chính (từ `fiscal_period_end`, kể cả năm nhuận và HSG 30/9); tồn kho/doanh thu; tỷ trọng NVL/thành phẩm/dở dang (nếu có); lệch so trung vị peer; z-score / robust z (MAD). |
| Chỉ báo biên gộp | mức; thay đổi YoY (điểm %); lệch so trung vị **thay đổi** của peer; lệch mức so trung vị peer (chỉ thông tin); z-score / robust z. |
| Nền lịch sử | Chỉ các năm **trước** kỳ đang xét. Đủ `min_changes_mad` (6) → MAD; đủ `min_changes_z` (4) → z-score; không đủ → **không có điểm số**, cờ `weak_baseline`, chỉ còn luật YoY/peer. Phương sai bằng 0 → cờ `zero_variance`. |
| Peer | Các công ty còn lại cùng năm tài chính, cần ≥ `min_peers` (2); thiếu → chỉ báo `None` + cờ `peer_missing`. **Peer có năm tài chính kết thúc muộn hơn bị loại** (cờ `peer_excluded_later_fiscal_end`: số của họ chưa có khi công ty đó khóa sổ). Khác ngày khóa sổ → `peer_fiscal_end_differs`; thiếu ngày → `fiscal_end_unknown`. |
| Chống rò rỉ | Fact từ năm ≥ `reference_only_from_year` (2024) bị loại ngay từ đầu vào; kỳ đang xét không dùng năm sau nó ở nền lịch sử, peer hay số ngày. Có test chứng minh kết quả không đổi khi thêm dữ liệu tương lai. |
| Không đoán | Thiếu/không OK không bao giờ thành 0; chỉ báo bắt buộc thiếu → kỳ "không xét được" nhưng **vẫn nằm trong mẫu số** (`no_data` hoặc `missing:<chỉ báo>`), kể cả công ty trong `dataset.yaml` hoàn toàn không có dữ liệu. Khóa fact trùng với giá trị khác nhau bị loại và ghi vào `result.notes` (không chọn theo thứ tự đầu vào); đơn vị lạ cũng vậy. |
| Cờ truyền từ đầu vào | `input:` (chính công ty, năm y và y-1), `input_peer:` (peer đã dùng), `input_history:` (các năm trước) cho các cờ cấu hình: `restated_later`, `comparative_only`, `validation_failed:*`... |
| Tỷ lệ bất thường | `result.summary()` theo loại: kỳ đã xét / xét được / bị gắn cờ / tỷ lệ (spec mục 8.4). |
| Ứng viên | `candidate_rows` / `write_candidates_csv`: sắp theo `severity` giảm dần để người chọn ca. |
| as-of | `asof_dates` trong config do **nhóm khai** (ngày công bố BCTC); không khai → `asof_date = null` + cờ `asof_date_unknown`. Phase 7 cần ngày này. |

## 2. File

- **Tạo:** `src/steel_xai/anomaly/{settings,detect}.py`, `tests/test_anomaly.py`, `tests/test_anomaly_review.py`, `docs/phase_reports/PHASE_5.md`.
- **Sửa:** `configs/anomaly.yaml` (viết lại), `anomaly/__init__.py`, `contracts.py` (`Anomaly`), `normalization/mapping.py` + `configs/normalization.yaml` (xem mục 4), `tests/test_review_round2.py`, `README.md`, `AI_USAGE_LOG.md`.

## 3. Quyết định thiết kế (nhóm nên đọc)

- **Hướng của bất thường là hướng của độ lệch mà luật vượt ngưỡng đo**, không nhất thiết dấu của biến động thô. Ví dụ tồn kho một công ty tăng 60% nhưng các peer tăng 300% → có thể ra bất thường `down` (do luật `inv_growth_vs_peer`) cùng bất thường `up` (do luật YoY), mỗi cái một bản ghi, cờ `mixed_direction`. Nếu nhóm muốn hướng luôn theo dấu biến động của chính công ty, đó là thay đổi nhỏ nhưng cần quyết định.
- **Một bản ghi cho mỗi (công ty, năm, loại, hướng)**; chỉ cần một luật vượt. `severity` = lớn nhất của |giá trị| / |ngưỡng|.
- **Ngày tồn kho** dùng độ dài năm tài chính thực, không mặc định 365.
- **Peer ghép theo năm tài chính `FY` và lọc theo ngày khóa sổ** (xem trên); với HSG (30/9) peer 31/12 cùng nhãn năm bị loại vì kết thúc sau HSG.
- **`compare_order: [yoy]`** (không QoQ); engine yêu cầu đúng giá trị này.

## 4. Sửa ở phase khác

- **Phase 3 (`mapping.py`):** khi chạy 29 tài liệu thật, HSG 2019–2023 mất toàn bộ chỉ tiêu kết quả kinh doanh vì header hai tầng ("Năm kết thúc ngày" + "30/9/2022 VND", chữ "năm" nằm ở ô cha). Đã xét cả đường dẫn header. Review phát hiện bản sửa đầu quá rộng (bất kỳ chữ "năm" nào trong tiêu đề bảng cũng thành cả năm), nên đã thu hẹp còn các **cụm kỳ** `năm kết thúc` / `năm tài chính` / `cho năm`. Có test hồi quy cả hai chiều.
- Kết quả trên dữ liệu thật: 307 fact chính; ba công ty đủ doanh thu, giá vốn, tồn kho cho mọi năm 2015–2023 trừ NKG FY2019 (ô doanh thu dồn số → `UNRESOLVED`, đã biết).

## 5. Tests

`tests/test_anomaly.py` (57) + `tests/test_anomaly_review.py` (21) + 1 test Phase 3. Toàn repo: **529 passed, 0 skipped, 0 failed**; `ruff check` và `ruff format` sạch. Gồm: chỉ báo tính tay (tăng trưởng, DIO năm nhuận, điểm %, z-score mẫu n-1, MAD, trung vị peer), ngưỡng hai phía và biên, hai chiều cùng lúc, `weak_baseline` theo số quan sát, không look-ahead (nền lịch sử và peer, năm ≥ 2024), mẫu số đầy đủ, khóa trùng, đơn vị, cờ, as-of, xác định/độc lập thứ tự, xuất ứng viên, một ví dụ fixture HPG 2022.

Lệnh: `.venv\Scripts\python -m pytest -q`, `ruff check .`, `ruff format --check .`.

## 6. Review độc lập

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 1 | 5 | 5 |

Đã sửa tất cả: HIGH (fact trùng khóa làm kết quả phụ thuộc thứ tự đầu vào); MEDIUM (kỳ hỏng hoàn toàn biến mất khỏi mẫu số; peer dùng số sau kỳ khi năm tài chính lệch; cờ `peer_fiscal_end_differs` sai hai chiều; cờ chất lượng của peer/lịch sử không truyền; `_fiscal` quá rộng); LOW (loại lặp trong config, cờ `days_in_period_missing` sai, đơn vị không kiểm, `asof_dates` không validate, test yếu).

## 7. Known limitations

- **Ngưỡng là đề xuất, tỷ lệ bất thường hiện 56–67%** (xem đầu báo cáo).
- **Mốc as-of chưa có**: nhóm chưa khai ngày công bố BCTC nên `asof_date` đều `null`. Loại peer theo `fiscal_period_end` chỉ là xấp xỉ của as-of thật; ngày công bố của peer vẫn có thể muộn hơn công ty đang xét dù ngày khóa sổ sớm hơn.
- Các cờ `input_*:` từ Phase 4 (đặc biệt `peer_fiscal_end_differs`) xuất hiện trên hầu hết bất thường vì HSG khác ngày khóa sổ; chúng đúng nhưng nhiều nên nhóm nên lọc khi đọc.
- Hầu hết kỳ 2021–2023 có đủ lịch sử (2015–2020) nên `weak_baseline` ít xuất hiện; chuỗi chỉ có 2 năm (chạy trên fixture) thì luôn có cờ.
- Chưa phân biệt báo cáo hợp nhất/riêng lẻ (giới hạn từ Phase 3); nên chỉ nạp `consolidated`.
- Tỷ trọng thành phần tồn kho chỉ là thông tin, không có luật.
- Số 2024 chỉ là đáp án tham chiếu nên bị loại; HSG FY2024 đã trích không đầy đủ nhưng không ảnh hưởng.

## 8. Chưa làm vì ngoài scope

Chọn 18 ca và chia dev/test (người làm, Phase 12a); chỉnh ngưỡng; thư viện giả thuyết (Phase 6); truy xuất RAG và as-of thật (Phase 7); mọi anomaly ngoài hai loại; ML.

## 9. Hard-code công ty

Không. Không có tên công ty, trang, năm trong `src/`. Danh sách công ty và mốc năm đọc từ `configs/dataset.yaml`.

## 10. AI_USAGE_LOG.md

Đã thêm mục "2026-10-23 — Phase 5".

## 11. Khác báo cáo giữa kỳ

Có, đã nêu trong `docs/DRAFT_SPEC_CHANGES.md` (chỉ YoY, dữ liệu năm). Phase 5 không thêm điểm lệch mới; nhóm cần duyệt bản nháp.

## 12. Cập nhật 2026-10-24 (sau review vòng 2)

Thêm sau bản đầu: `anomaly/corpus.py` + `scripts/build_anomaly_candidates.py` (nạp bộ OCR, chạy Phase 2-4-5, xuất `data/processed/anomaly_candidates.csv` và `asof_dates.csv`), `case_selection_window` (2016–2023, NKG từ 2017) trong `configs/dataset.yaml`, hướng bất thường = dấu biến động của chính công ty (`peer_relative` chỉ là thuộc tính phụ), cờ `mixed_direction` / `candidate`, trích ngày ký kiểm toán (`normalization/context.py`). Các điểm lệch spec đã ghi nháp ở `DEVIATIONS_FROM_MIDTERM.md` và `docs/DRAFT_SPEC_CHANGES.md`; **nhóm cần duyệt**.

Review vòng 2: 0 CRITICAL, 2 HIGH, 1 MEDIUM, 1 LOW. Đã sửa:
- **HIGH – rò rỉ qua số so sánh:** kỳ y từng có thể lấy số từ cột so sánh của BCTC năm y+1 (công bố sau as-of; ví dụ NKG FY2022 biên gộp). Nay fact `comparative_only` của năm ≥ kỳ đang xét bị coi là chưa có (kỳ thành "không xét được", vẫn nằm trong mẫu số); số so sánh của năm trước vẫn được dùng.
- **HIGH – peer công bố sau as-of:** peer có as-of muộn hơn công ty đang xét bị loại (cờ `peer_excluded_later_asof`). Nếu thiếu as-of của một bên thì giữ peer (đã có cờ `asof_date_unknown`).
- **MEDIUM – ca chỉ do luật peer:** ví dụ HPG FY2018 tồn kho +20% nhưng gắn "up" chỉ vì peer giảm. Nay gắn cờ `peer_only_hit` và `candidate=false` (vẫn ghi vào bảng).
- **LOW – trích ngày ký lấy nhầm dòng** (HSG FY2015 ra `unresolved`): chưa sửa; fail-safe (không ra ngày sai), ngoài cửa sổ chọn ca.

Tests: toàn repo **566 passed**, `ruff check` và `ruff format --check` sạch.
