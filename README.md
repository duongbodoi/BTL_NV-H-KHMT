# steel_xai — Giải thích bất thường BCTC ngành thép

Spec chính thức: `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md`. Trạng thái (2026-10-25): Phase 0–12 đã có code và test (chi tiết từng phase ở `docs/phase_reports/`). Đang làm: gán nhãn bộ ca (Phase 12a), chạy đánh giá trên dev (Phase 12; đã chạy thật C01 thành công). Demo (Phase 13): xem mục 6 bên dưới. Chưa làm: gói nộp (Phase 14).

## Hướng dẫn chạy thử (cho thành viên nhóm)

LLM dùng **Gemini `gemini-3.5-flash-lite` free tier** (nhóm chốt 2026-10-25): không tốn tiền, nhưng có giới hạn số lời gọi/phút.

### 1. Cài đặt (một lần)

Cần Python ≥ 3.11 và git.

```bash
git clone <địa chỉ repo> && cd mvp
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1   |   Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q          # phải thấy "... passed", không có failed
```

### 2. Dữ liệu (một lần)

- **OCR BCTC không nằm trong git** (dữ liệu lớn/bản quyền). Lấy thư mục `vifinqa` từ nhóm (Nguyễn Tùng Dương) và đặt đúng chỗ: `data/raw/vifinqa/<mã>/<năm>/<tên tài liệu>/..._extracted.txt`.
- Giá quặng/thép (`data/external/`), danh sách ca và nhãn (`data/eval/`) đã có trong git.
- Dựng các file trung gian (không gọi API, mất vài phút):

```bash
python scripts/build_anomaly_candidates.py   # → data/processed/anomaly_candidates.csv, asof_dates.csv
python scripts/build_rag_index.py            # → data/processed/rag_chunks.jsonl
python scripts/eval_tools.py candidates      # kiểm tra: phải thấy 11 dòng có dấu *
```

### 3. API key Gemini (một lần)

Mỗi người tự tạo key miễn phí tại Google AI Studio (https://aistudio.google.com/apikey). **Không gửi key qua chat, không commit key.** Code chỉ đọc biến môi trường `GEMINI_API_KEY` (không tự đọc file `.env`). Thay `<KEY>` bằng key của bạn:

```powershell
# Windows PowerShell: đặt cho phiên hiện tại
$env:GEMINI_API_KEY = "<KEY>"
# hoặc lưu lâu dài (mở terminal mới sau lệnh này)
setx GEMINI_API_KEY "<KEY>"
```

```bash
# Linux/macOS
export GEMINI_API_KEY="<KEY>"
```

### 4. Chạy thử không gọi API (kiểm tra cài đặt)

```bash
python scripts/run_eval.py estimate --split dev   # đếm số lời gọi LLM tối đa cho 4 ca dev
python scripts/run_report.py --anomaly "NKG|FY2021|inventory_increase|up"   # báo cáo khung (chưa có LLM)
```

Báo cáo ra ở `data/processed/reports/`.

### 5. Chạy thử với API thật (free tier)

**Bắt đầu nhỏ: một ca, một lần chạy** (khoảng 40 lời gọi LLM, vài phút):

```bash
python scripts/run_eval.py run --split dev --cases C01 --runs 1
```

- Mỗi dòng in ra là một cấu hình (A, B, C, A_lib, B_lib, no_rag, no_verification, no_library) với `ok` hoặc `error (lý do)`.
- Gặp lỗi 429/503 (hết hạn mức free tier): đợi vài phút rồi **chạy lại đúng lệnh đó**. Lần chạy đã `ok` được giữ nguyên, chỉ lần lỗi được chạy lại.
- Kết quả: `outputs/eval/dev/runs/<cấu hình>/<ca>/<lần>.json`. Thư mục `outputs/` và cache LLM (`cache/`) nằm máy ai người nấy, không commit.

Chấm tạm (chỉ trên dev, khi người gán thứ hai chưa xong):

```bash
python scripts/run_eval.py score --split dev --provisional "Nguyễn Tùng Dương"
```

Xem `outputs/eval/dev/results/summary.md` (bảng metric), `case_C01.md` (ca minh họa), `hit_chart.svg`. Ở giai đoạn này A/B hiện "–" ở Hit@k vì còn chờ ánh xạ mù: đó là bình thường.

Chạy đủ tập dev (4 ca × 8 cấu hình × 3 lần, tối đa ~440 lời gọi; free tier có thể phải chạy nhiều đợt):

```bash
python scripts/run_eval.py run --split dev
```

Một báo cáo đầy đủ cho một bất thường (bước 4 có LLM viết):

```bash
python scripts/run_report.py --call-api --anomaly "NKG|FY2021|inventory_increase|up"
```

### 6. Demo (trình bày kết quả, không gọi API)

Demo đọc kết quả đã chạy ở bước 5 (cấu hình C, kèm A/B để so sánh), nên lúc trình bày không cần mạng.

```bash
python scripts/demo.py --list               # các ca đã có kết quả
python scripts/demo.py --case C01           # xem trên terminal

python -m pip install -e ".[demo]"          # một lần: cài Streamlit
streamlit run src/steel_xai/demo/app.py     # mở giao diện web (trình duyệt tự bật)
```

**Ca trình diễn** (nhóm chọn, `configs/demo/showcase.yaml`): **C06** (chính: biên gộp HSG 2016), **C01** (ví dụ ca sai: tồn kho NKG 2021), **C05** (dự phòng). Demo đưa các ca này lên đầu, đánh dấu ★.

**Bản dự phòng không cần Python/mạng** (phòng khi máy trình bày lỗi Streamlit):

```bash
python scripts/demo.py --html outputs/demo   # mỗi ca trình diễn một file HTML + index.html
```

Chép cả thư mục `outputs/demo/` vào USB, mở `index.html` bằng trình duyệt bất kỳ. Thư mục `outputs/` không nằm trong git: xuất lại sau mỗi lần chạy lại các ca.

Trang hiển thị: bất thường và chỉ báo → giả thuyết + trạng thái → bằng chứng, trích dẫn (tài liệu, trang) và phần còn thiếu → giải thích cuối → so sánh A/B. Muốn có thêm ca thì chạy `run_eval.py run` cho ca đó trước.

### 7. Quy tắc khi chạy

- **KHÔNG chạy `--split test`.** Tập test chỉ chạy một lần, sau khi cả nhóm đã gán nhãn xong và đóng băng (`eval_tools.py freeze`). Lệnh sẽ từ chối nếu chưa đóng băng; đừng dùng `--unfreeze-reason` để lách.
- **Không sửa tay** file trong `outputs/`, `data/processed/`, `configs/eval_freeze.json`.
- Sửa code/config/prompt thì kết quả cũ bị coi là phiên bản khác và được chạy lại ở lần `run` sau (tốn thêm lời gọi).
- Gặp lỗi lạ: gửi nhóm dòng `error (...)` và file JSON của lần chạy đó, **không gửi kèm API key**.
- Người gán nhãn thứ hai (Hoàng Minh Đức): gán **trong phiên riêng, không mở** `data/eval/labels/nguyen_tung_duong.yaml`. Hướng dẫn: `data/eval/templates/README.md`.

### Lệnh hay dùng

| Việc | Lệnh |
|---|---|
| Kiểm danh sách ca, nhãn, kappa | `python scripts/eval_tools.py check` |
| Ước tính số lời gọi LLM (không gọi API) | `python scripts/run_eval.py estimate --split dev` |
| Chạy đánh giá | `python scripts/run_eval.py run --split dev [--cases C01,C02] [--configs A,B,C] [--runs 1]` |
| Sinh bảng cho người (ánh xạ mù, chấm trích dẫn) | `python scripts/run_eval.py sheets --split dev` (sau khi chạy xong mọi cấu hình) |
| Chấm điểm | `python scripts/run_eval.py score --split dev --provisional "<họ tên>"` |
| Ước tính chi phí nếu dùng model tính phí | `python scripts/estimate_cost.py` → `docs/COST_ESTIMATE.md` |

## Parse văn bản OCR (Phase 1)

```python
from steel_xai.parsing import parse_file

doc = parse_file("tài_liệu.txt", doc_id="abc_2023")  # mỗi trang mở đầu bằng "===== PAGE N ====="
for block in doc.iter_blocks("table"):
    print(block.block_id, block.page, block.provenance())  # page = số trang PDF, không phải số in
```

## Trích số thô (Phase 2)

```python
from steel_xai.extraction import extract_document, load_structured_csv

for table in extract_document(doc, company="ABC"):  # company do người gọi cung cấp
    for v in table.values:  # mỗi ô của cột giá trị là một RawTableValue
        print(v.row_label, v.period, v.unit, v.raw, v.value, v.status, v.flags)

rows = load_structured_csv("bctc.csv")  # tên cột/layout/định dạng số trong configs/extraction.yaml
```

- Số VN: `1.234.567` → 1234567, `(1.234)` → -1234, `12,5%` → 12.5 (`is_percent`). Giá trị là `Decimal`.
- `-` → `value=None`, `NOT_APPLICABLE` (không bao giờ là 0); ô rỗng → `MISSING`; không parse được → `UNRESOLVED` kèm lý do trong `flags`.
- Chưa map nhãn về metric chuẩn và chưa quy đổi đơn vị: đó là việc của Phase 3.

## Chuẩn hóa metric (Phase 3)

Dữ liệu là BCTC **năm** (OCR ViFinQA, không có nguồn có cấu trúc); kỳ là năm tài chính `FY2023` kèm `fiscal_period_end` (HSG kết thúc 30/9).

```python
from steel_xai.normalization import build_context, normalize

ctx = build_context(doc, settings)  # ngày kết thúc năm tài chính + đơn vị đọc từ văn bản tài liệu
result = normalize(extract_document(doc, company="ABC"), contexts=[ctx])
f = result.fact(
    "ABC", "FY2023", "inventory_total"
)  # FinancialFact: value (Decimal, VND), status, provenance
print(result.rejections, result.conflicts, result.comparatives)
```

- Từ điển nhãn, mã số, cột, đơn vị, cờ chặn nằm ở `configs/normalization.yaml`.
- Giá trị chính của năm N lấy từ báo cáo năm N; số so sánh trong báo cáo N+1 lưu riêng (`result.comparatives`), khác thì fact chính có cờ `restated_later`.
- Hai nguồn/tài liệu cùng năm báo cáo lệch nhau thì `UNRESOLVED`, không tự chọn. Thiếu/`-` không bao giờ thành 0 ở bước này.
- `gross_margin = gross_profit / net_revenue` do code tính (`source = "derived"`).

## Validation (Phase 4)

```python
from steel_xai.validation import load_validation_settings, validate_facts, draw_sample

v = validate_facts(result.facts, load_validation_settings())  # fact đã gắn cờ + danh sách check
print(v.summary())  # PASS / FAIL / NOT_EVALUABLE theo từng quan hệ
rows = draw_sample(
    result.facts, n=20, seed=...
)  # mẫu đối chiếu BCTC gốc; người điền reference_value
```

- Quan hệ kế toán (lợi nhuận gộp = doanh thu thuần - giá vốn; hàng tồn kho thuần = giá gốc + dự phòng; thành phần ≤ tổng) nằm ở `configs/validation.yaml`, dung sai 0,1%.
- `-` khi cộng tổng coi là 0 và gắn cờ `nil_as_zero` (cấu hình được); thiếu/UNRESOLVED thì check ra `NOT_EVALUABLE`, không bao giờ thành 0.
- Check FAIL chỉ gắn cờ vào fact (`validation_failed:<quan_hệ>`), không đổi giá trị hay trạng thái.
- Bản nháp thay đổi spec/DEVIATIONS theo quyết định dữ liệu năm: `docs/DRAFT_SPEC_CHANGES.md` (chờ nhóm duyệt).

## Phát hiện bất thường (Phase 5)

```python
from steel_xai.anomaly import detect_anomalies, candidate_rows, write_candidates_csv

result = detect_anomalies(validated.facts)  # giai đoạn gốc và mốc năm đọc từ configs/dataset.yaml
print(result.summary())  # kỳ gắn cờ / kỳ xét được, theo loại (spec mục 8.4)
write_candidates_csv(result, "candidates.csv")  # danh sách ứng viên để NGƯỜI chọn ca
```

- Hai loại (tồn kho, biên gộp), hai chiều, chỉ YoY theo năm tài chính, không LLM. Luật và ngưỡng ở `configs/anomaly.yaml`.
- **Ngưỡng hiện là đề xuất khởi điểm, chưa phải quyết định của nhóm** (`provisional_thresholds: true`, mọi bất thường mang cờ này).
- Nền lịch sử và peer chỉ dùng thông tin có tại kỳ đang xét; năm từ `reference_only_from_year` trở đi không bao giờ được dùng. Không đủ lịch sử thì cờ `weak_baseline`, thiếu peer thì cờ `peer_missing`.

## Cài đặt

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Cần cài dạng editable (`-e`) để `steel_xai` tìm đúng thư mục `configs/`; có thể ghi đè bằng biến môi trường `STEEL_XAI_CONFIG_DIR`.

API key chỉ đặt qua biến môi trường có tên khai báo ở `configs/llm.yaml` (`api_key_env`, hiện là `GEMINI_API_KEY`). Không commit `.env`.

## Test và lint

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Cấu trúc

```text
configs/     anomaly.yaml, retrieval.yaml, llm.yaml, eval.yaml, dataset.yaml, extraction.yaml (giá trị null = nhóm chưa chốt)
data/        raw/ (không commit), processed/, eval/
hypotheses/  thư viện giả thuyết (nội dung do nhóm soạn)
src/steel_xai/  config.py, contracts.py (Provenance, ValueStatus), parsing/ (Phase 1), extraction/ (Phase 2), các subpackage còn rỗng
tests/       test config loader, vệ sinh repo, parsing, extraction; fixtures/ (HPG chỉ là ví dụ)
docs/
AI_USAGE_LOG.md, DEVIATIONS_FROM_MIDTERM.md
```
