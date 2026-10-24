# Báo cáo Phase 11 — Grounded report

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 3.4, 4, 11, 13, 16, 20 (Phase 11), 23
- **Trạng thái:** phần việc của Claude đã xong, qua 1 vòng review độc lập, lỗi HIGH/MEDIUM đã sửa. **Chưa COMPLETED**: chưa gọi API thật lần nào; Phase 6–10 chưa COMPLETED (thư viện chưa có giả thuyết `reviewed`, check văn bản chưa chạy thật). Phần còn lại của Phase 10 là việc của nhóm (xác nhận 3 lựa chọn luật, duyệt giả thuyết) và không đổi cấu trúc dữ liệu báo cáo dùng, nên nhóm đồng ý làm tiếp Phase 11 (spec mục 28.3).

## ⚠ Điều nhóm cần biết

- **Chưa gọi LLM thật.** `scripts/run_report.py` mặc định KHÔNG gọi API: chỉ dựng phần do code làm và ghi prompt (`data/processed/reports/*.prompt.json`) để nhóm đọc trước. `--call-api` mới gọi LLM (tốn token). Đã chạy chế độ không gọi API trên cả 11 bất thường thật.
- **LLM không viết tự do.** LLM trả danh sách *khẳng định* có cấu trúc (`section`, `hypothesis_id`, `text`, `sources`). Tiêu đề, bảng trạng thái, kết quả check định lượng, phần thiếu, bằng chứng phản bác và mục Nguồn đều do **code** dựng từ verdict. Vì vậy LLM không thể đổi trạng thái hay bịa nguồn; nếu bị từ chối hết, báo cáo vẫn đủ 8 mục (chỉ thiếu phần diễn giải).
- **Bộ kiểm khá chặt, có thể làm LLM phải sinh lại nhiều lần.** Số phải chép đúng chuỗi `display` do code định dạng (không viết thô hơn, đúng đơn vị `%` / `điểm %`, đúng chiều tăng/giảm, chỉ định dạng Việt). Nên đo trên tập dev xem tỷ lệ chấp nhận ở lần đầu; nếu thấp do từ chối oan thì nới mẫu trong `configs/report.yaml` **trước** khi đóng băng tập test.
- **Bộ lọc nội dung cấm và nhân quả là danh sách regex** (`configs/report.yaml`: `forbidden`, `causal`, `hedges`, `status_phrases`) do Claude đề xuất. Không bao giờ đầy đủ: nhóm nên đọc lại, thử thêm câu thật và bổ sung. Câu có "không/chưa" trong ba từ ngay trước một mẫu cấm thì không tính (ví dụ "không kết luận gian lận").
- **Mọi giá trị trong `configs/report.yaml` là đề xuất** (top_k 3, max_attempts 3, `on_exhausted: skeleton_only`, dung sai 0,0001, đơn vị/nhãn chỉ báo). Chúng nằm trong hash freeze khi Phase 12a làm `eval_freeze.json`; cũng cần thêm `report_prompt_fingerprint()` vào freeze.
- **Metric RQ2 lấy từ đây:** `ReportResult.metrics()` đếm trên **bản nháp đầu** (`first_draft_numeric_mismatches`, `first_draft_unsupported_claims`, các loại vi phạm) và trên bản cuối (`final_numeric_mismatches` luôn 0 theo cấu trúc). "Unsupported claim rate" của spec cần thêm người đánh giá; đây chỉ là phần code đếm được (khẳng định không dẫn nguồn).

## Phase này để làm gì?

Bước 4 của pipeline (spec mục 6, 13): biến kết quả kiểm chứng đã xếp hạng (Phase 10) thành báo cáo tiếng Việt 8 mục. LLM chỉ diễn đạt lại; code kiểm mọi con số, mọi nguồn dẫn, nội dung cấm, rồi từ chối và sinh lại khi lệch.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `report/facts.py` — `build_report_input` | Chỉ dữ liệu đã xử lý (không BCTC gốc): mã `N` = số do code tính (chỉ báo bất thường, giá trị check, đầu vào của check), mã `E` = trích dẫn đã qua kiểm nguyên văn, mã `M` = check thiếu dữ liệu + lý do, mã `H` = verdict (trạng thái, hạng, critical đạt/tổng). Top-k theo `rank` (lấy cả nhóm hòa). None không thành 0. LLM chỉ thấy chuỗi `display` do code định dạng, không thấy giá trị thô. |
| `report/numbers.py` | Định dạng Việt (`1.234,5`, `12,3%`, `-15,6 điểm %`, `45,3 ngày`, `42.134,5 tỷ đồng`); đọc số kiểu Việt (chấm ngăn nghìn, phẩy thập phân, ngoặc = âm, hậu tố `%`/`điểm %`/`ngày`/`tỷ`/`triệu`/`lần`). Kiểu Anh (`12.3`, `1,234.5`) bị coi là sai. Khớp khi cùng loại đơn vị, lệch ≤ nửa đơn vị chữ số cuối, không thô hơn chuỗi hiển thị, đúng dấu khi có dấu trừ/ngoặc. |
| `report/checks.py` — `check_draft` | Mỗi khẳng định: có nguồn (không có → `unsupported`, bị loại, không chặn); mã nguồn có thật; mã E/M/H và số N của bằng chứng phải thuộc đúng giả thuyết; mỗi số khớp một N đã dẫn (kèm chiều "tăng/giảm" ngay trước), hoặc số viết trong E/M đã dẫn (cùng đơn vị), hoặc hạng của H đã dẫn; tỷ số `a/b` phải đúng critical đạt/tổng; trạng thái nhắc tới (SUPPORTED... và cụm tiếng Việt) phải đúng trạng thái do code xác định; nội dung cấm (khuyến nghị đầu tư, gian lận, chắc chắn); nhân quả không rào đón khi giả thuyết chưa SUPPORTED; kết luận phải có điều kiện; độ phủ (mỗi giả thuyết top-k được nhắc; PARTIAL/UNVERIFIED có phần thiếu phải được giải thích ở mục missing). |
| `report/generate.py` — `generate_report` | LLM viết → kiểm → nếu có vi phạm chặn thì sinh lại kèm danh sách lỗi (và số thứ tự lần viết, để không trúng cache bản lỗi), tối đa `max_attempts`. Hết lượt: `skeleton_only` (mặc định) hoặc `drop_failing`. Lỗi LLM (từ chối, sai schema) → báo cáo chỉ phần code dựng, trạng thái `llm_error`. Bất biến: không khẳng định nào có vi phạm lọt vào bản cuối. |
| `report/render.py` | 8 mục theo spec mục 13; tuyên bố miễn trừ ("không phải tư vấn đầu tư"), cảnh báo khi dùng giả thuyết draft; khoảng trắng trong khẳng định được gộp (không chèn được tiêu đề Markdown). |
| `report/prompts.py` | Prompt + schema trong code; `report_prompt_fingerprint()` cho freeze. |
| `configs/report.yaml`, `scripts/run_report.py` | Mọi ngưỡng/mẫu/đơn vị trong config; script chạy trên dữ liệu thật, mặc định không gọi API. |

## 2. File

- **Tạo:** `src/steel_xai/report/{settings,numbers,facts,prompts,checks,render,generate}.py`, `configs/report.yaml`, `scripts/run_report.py`, `tests/test_report.py`, `docs/phase_reports/PHASE_11.md`.
- **Sửa:** `src/steel_xai/report/__init__.py` (export), `src/steel_xai/config.py` (thêm `report` vào `CONFIG_NAMES`), `AI_USAGE_LOG.md`, `README.md`, `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (mục 28: trạng thái Phase 11).

## 3. Tests

`tests/test_report.py` (120 test), **toàn bộ dùng LLM giả**: định dạng và đọc số Việt (kể cả mơ hồ `1.250`/`1,250`, kiểu Anh, ngoặc âm, hậu tố dính liền); thuộc tính "chuỗi display luôn khớp chính giá trị của nó" trên 3.000 giá trị ngẫu nhiên mọi đơn vị; từng loại vi phạm (số sai, sai đơn vị `%`/`điểm %`, thô hơn display, sai chiều, năm lạ, ngày lạ, tỷ số critical sai, hạng sai, trạng thái sai, mã giả, sai giả thuyết, nội dung cấm, nhân quả, kết luận không điều kiện, độ phủ, giới hạn định dạng); câu vô hại không bị bắt nhầm (phủ định, "cổ phiếu quỹ", "sản lượng bán ra", "cơ hội đầu tư mở rộng công suất"); vòng sinh lại (đếm lệch ở bản nháp đầu, feedback và số lần viết trong prompt lần sau), hết lượt (`skeleton_only`, `drop_failing` + `max_claims`), lỗi LLM; bất biến "không khẳng định lỗi nào lọt bản cuối" trên 150 bản nháp ngẫu nhiên; fingerprint prompt.

Lệnh: `pytest -q` → **1048 passed**, 0 failed, 0 skipped; `ruff check .` và `ruff format --check .` sạch.

## 4. Review độc lập

Một agent không thấy quá trình viết code review toàn bộ Phase 11 (đọc code, chạy pytest, chạy script trên dữ liệu thật, tự viết script thử). Không có CRITICAL. Đã sửa:

- **HIGH:** số kiểu Anh `12.345 tỷ` qua sai 1000 lần (nay chỉ nhận định dạng Việt); trạng thái và tỷ số critical sai vẫn qua (nay đối chiếu với verdict); bộ lọc nhân quả bỏ sót "do/vì/khiến/kéo theo" và từ rào đón "có thể thấy" (nay thêm mẫu, loại "có thể thấy").
- **MEDIUM:** viết thô hơn display vẫn qua ("1 nghìn tỷ" cho 1.400 tỷ); không kiểm dấu/chiều; lẫn `%` với `điểm %`; số trong văn bản nguồn khớp bất kể đơn vị; số N của giả thuyết khác; thiếu mẫu cấm phổ biến ("nên cân nhắc mua", "thổi phồng", "làm đẹp BCTC"); bắt nhầm câu phủ định/vô hại; báo cáo skeleton mất kết quả check định lượng (nay code liệt kê ở mục 4); đơn vị fact đầu vào mặc định VND (nay theo metric trong config, khoản < 1 triệu in đồng); lỗ hổng trích số (mã/ngày được miễn bất kể có thật, `12,3%,15%`).
- **LOW:** sinh lại có thể trúng cache bản lỗi (thêm số lần viết); `drop_failing` vượt `max_claims`; xuống dòng trong khẳng định chèn được Markdown; prompt thiếu mã giả thuyết; `run_report.py --anomaly` ghi đè log.

Không sửa (ghi nhận là giới hạn): một lỗi LLM dừng hẳn vòng sinh lại (client đã tự thử lại khi sai schema; từ chối thì không nên gọi lại).

## 5. Known limitations

- Chưa đo trên LLM thật: chưa biết tỷ lệ chấp nhận ở lần đầu, số lần sinh lại, token/độ trễ mỗi báo cáo.
- Chiều tăng/giảm chỉ kiểm khi từ "tăng"/"giảm" đứng trong khoảng 30 ký tự ngay trước số; cách nói khác ("đi xuống", "thu hẹp") không được kiểm chiều.
- Bộ lọc cấm/nhân quả/trạng thái là regex: có thể bỏ sót cách diễn đạt mới hoặc bắt nhầm. Kiểm "có điều kiện" chỉ dựa trên từ rào đón có mặt trong câu.
- Câu chữ của LLM chỉ được kiểm phần số, nguồn, trạng thái, từ cấm; **ý nghĩa** câu (ví dụ diễn giải sai một trích dẫn) vẫn cần người đánh giá ("citation support rate" do hai người chấm, spec mục 15.3).
- Số dính liền phía sau chữ cái (`N99`, `abc12`) không được đọc là số (để không nhầm với mã); số viết bằng chữ ("mười hai phần trăm") không được kiểm.
- Năm 4 chữ số thuộc dữ liệu đầu vào được bỏ qua khi không có đơn vị theo sau; một số 4 chữ số trùng năm mà không có đơn vị vẫn có thể lọt.

## 6. Chưa làm vì ngoài scope

- Baseline A/B (LLM only, LLM + RAG) và runner đánh giá: Phase 12. Phase 11 chỉ cung cấp `metrics()` để Phase 12 tổng hợp.
- Giao diện hiển thị báo cáo: Phase 13.

## 7. Hard-code công ty

Không. Không có tên công ty, năm, trang cố định trong code; tên chỉ báo/đơn vị/nhãn nằm trong `configs/report.yaml`. Test dùng công ty giả `AAA`.

## 8. AI_USAGE_LOG.md

Đã thêm mục "2026-10-24 — Phase 11 (Grounded report)" với việc đã làm, lỗi review bắt được và phần người cần xác minh.

## 9. Khác báo cáo giữa kỳ

Không có điểm mới khác giữa kỳ: LLM vẫn chỉ viết lại kết quả đã xác thực ở bước 4. Việc LLM trả khẳng định có cấu trúc (thay vì văn bản tự do) là chi tiết triển khai của ràng buộc spec mục 13, không đổi phạm vi.
