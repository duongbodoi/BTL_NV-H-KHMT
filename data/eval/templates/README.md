# Mẫu cho Phase 12a (bộ ca và đáp án tham chiếu)

Claude chỉ dựng công cụ. **Nhóm** chọn ca, gán nhãn, giải quyết bất đồng, ánh xạ mù, gán nguyên nhân ca sai (spec mục 21.1).

| File mẫu | Chép thành | Ai điền |
|---|---|---|
| `cases.yaml` | `data/eval/cases.yaml` | nhóm: chọn ca, chia dev/test |
| `labels.yaml` | `data/eval/labels/<tên>.yaml` (2 file) | hai người gán, độc lập |
| `adjudication.yaml` | `data/eval/adjudication.yaml` | người thứ ba |
| `blind_mapping.csv` | (Phase 12 sinh, có `item_id` mù) | hai người ánh xạ mù giả thuyết của A/B |
| `failure_analysis.csv` | (Phase 12 sinh danh sách ca sai) | người gán cột `cause` |

`cause` thuộc: `retrieval_miss`, `rule_wrong`, `llm_classification_wrong`, `ambiguous_reference`, `data_missing`.

Quy trình:
1. `python scripts/eval_tools.py candidates` — xem bất thường.
2. Điền `cases.yaml`; sửa `configs/eval.yaml: split` nếu đổi số ca (ghi vào `DEVIATIONS_FROM_MIDTERM.md`).
3. Hai người điền nhãn độc lập → `python scripts/eval_tools.py check` (kappa, bất đồng).
4. Người thứ ba điền `adjudication.yaml` → `check` lại tới khi mọi ca có đáp án cuối.
5. Viết xong runner (Phase 12), chốt config/prompt/ngưỡng/code trên tập dev, mọi ca có đáp án cuối →
   `python scripts/eval_tools.py freeze --by "Họ Tên"` → **commit `configs/eval_freeze.json` ngay**.
   Freeze băm cả code (`src/`, `scripts/`), dữ liệu đầu vào, config, thư viện, file ca/nhãn và đáp án.
6. Trước khi chạy tập test: `python scripts/eval_tools.py check-freeze` phải khớp. Sửa bất kỳ file nào
   sau khi đóng băng → runner từ chối, trừ khi có `--unfreeze-reason` (được ghi vào kết quả).

Ánh xạ mù: **không đưa file key cho người ánh xạ** (key chứa salt; biết salt thì đoán được cấu hình).
