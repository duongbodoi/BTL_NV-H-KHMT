# Báo cáo Phase 12a — Bộ ca và đáp án tham chiếu (công cụ)

- **Ngày:** 2026-10-25
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 14.1, 15 (15.1–15.5), 18, 20 (Phase 12a), 21.1, 22–24
- **Trạng thái:** phần việc của Claude (công cụ) đã xong, qua 1 vòng review độc lập, mọi lỗi HIGH/MEDIUM đã sửa. **Chưa COMPLETED**: nội dung (chọn ca, chia dev/test, gán nhãn, giải quyết bất đồng) là việc của nhóm và chưa có (`data/eval/cases.yaml`, `data/eval/labels/` đang trống).

## ⚠ Điều nhóm cần biết

- **Claude không chọn ca, không gán nhãn, không viết đáp án** (spec 21.1). Code chỉ kiểm cấu trúc và tính toán. `eval_tools.py candidates` chỉ *liệt kê* bất thường.
- **Số ca:** `configs/eval.yaml` vẫn là 5 dev + 13 test (18 ca theo spec), nhưng engine hiện chỉ có **11 ứng viên** vượt ngưỡng. Lệnh `freeze` sẽ từ chối cho tới khi số ca khớp config. Nhóm quyết: thêm ca (ví dụ ca không vượt ngưỡng làm đối chứng — validator chỉ cảnh báo), hoặc sửa `split` và ghi vào `DEVIATIONS_FROM_MIDTERM.md`.
- **Freeze băm cả CODE** (`src/**/*.py`, `scripts/*.py`), dữ liệu đầu vào (giá, `rag_chunks.jsonl`, `asof_dates.csv`, `anomaly_candidates.csv`), config, thư viện, file ca/nhãn, prompt và đáp án cuối. Lý do: luật kiểm chứng và bộ kiểm báo cáo nằm trong code. Hệ quả: **viết xong runner Phase 12 và chỉnh xong mọi thứ trên tập dev rồi mới đóng băng**; sửa bất kỳ file nào sau đó thì runner từ chối chạy test, trừ khi có `--unfreeze-reason` (được ghi vào kết quả).
- **Chỉ đóng băng được khi mọi ca đã có đáp án cuối** (hai người gán + người thứ ba giải quyết bất đồng). Spec chỉ yêu cầu băm "config, prompt, ngưỡng, danh sách ca test"; khóa thêm đáp án là để không sửa nhãn sau khi thấy kết quả C.
- **Commit `configs/eval_freeze.json` ngay sau khi đóng băng.** Đóng băng lại (`--overwrite`) giữ lịch sử bản cũ, nhưng chỉ git mới chứng minh được thời điểm.
- **Kiểm "nguồn nhãn độc lập với kho RAG" chỉ bắt được một phần:** LỖI khi nguồn có doc_id/chuỗi doc_id của kho; CẢNH BÁO khi nguồn trông như BCTC của công ty + năm đã nạp vào kho. Bài báo, báo cáo phân tích, link PDF với tên khác: **người duyệt kiểm**.

## Phase này để làm gì?

Chuẩn bị đánh giá (spec 15): danh sách ca + chia dev/test, đáp án tham chiếu do hai người gán độc lập (kappa), người thứ ba giải quyết bất đồng, cơ chế đóng băng trước khi chạy tập test, bảng ánh xạ mù cho giả thuyết tự do của baseline A/B, bảng phân loại ca sai.

## 1. Đã làm được gì

| Thành phần | Mô tả |
|---|---|
| `contracts.CaseRecord` (spec 18) | `case_id`, `split`, `anomaly_id`, `reference_set`, `reference_sources`, `reference_statuses`, `note`; `to_dict`/`from_dict`. |
| `eval/cases.py` | Đọc/kiểm `data/eval/cases.yaml`: id duy nhất, bất thường có trong `anomaly_candidates.csv` (không vượt ngưỡng → cảnh báo), công ty/kỳ trong phạm vi, năm ≥ 2024 bị từ chối (chỉ làm nguồn đáp án), một bất thường chỉ một ca, số ca dev/test khớp config (bắt buộc khi đóng băng). |
| `eval/labels.py` | File nhãn mỗi người: `reference_set` chỉ gồm giả thuyết `reviewed` cùng loại hoặc `other` (có `other` thì bắt buộc note), `statuses` tùy chọn, nguồn độc lập với kho RAG, người gán là người (dùng chung luật `reviewed_by`, chặn cả tên giữ chỗ `<Họ và tên>`). Cohen's kappa cho đáp án và trạng thái; đồng thuận theo ca (khớp hẳn, Jaccard). Bất đồng → `adjudication.yaml` (người thứ ba khác hai người gán, bắt buộc lý do) → đáp án cuối. |
| `eval/freeze.py` | `build_manifest` / `write_freeze` / `diff_manifest` / `require_frozen`. Hash chuẩn hóa CRLF. `manifest_sha256` để runner ghi vào kết quả. |
| `eval/worksheets.py` | Ánh xạ mù (salt ngẫu nhiên bí mật, `item_id` không lộ cấu hình, key để riêng), kiểm ánh xạ, kappa ánh xạ, `unblind`; liệt kê ca sai, kiểm nguyên nhân (5 loại spec 15.5), bảng tổng hợp kèm câu "khảo sát khả thi". |
| `scripts/eval_tools.py` | `candidates`, `check`, `freeze --by "Họ Tên" [--overwrite]`, `check-freeze`. Không gọi API. |
| `data/eval/templates/` | Mẫu `cases.yaml`, `labels.yaml`, `adjudication.yaml`, `blind_mapping.csv`, `failure_analysis.csv`, `README.md` (quy trình). |

## 2. File

- **Tạo:** `src/steel_xai/eval/{settings,cases,labels,freeze,worksheets}.py`, `scripts/eval_tools.py`, `tests/test_eval.py`, `data/eval/templates/*`, `data/eval/labels/.gitkeep`, file này.
- **Sửa:** `src/steel_xai/eval/__init__.py`, `src/steel_xai/contracts.py` (`CaseRecord`, `SPLITS`), `configs/eval.yaml` (paths, annotators, other_id, failure_causes, freeze_globs, min_unfreeze_reason_chars), `src/steel_xai/hypotheses/library.py` (`person_name_problem`; chặn tên chứa `<>[]{}`; **sửa lỗi cũ Phase 6**: regex danh mục nguồn chứa ký tự backspace thay vì `\b` nên danh mục S0.. không bao giờ khớp), `configs/hypotheses.yaml` (thêm "họ tên", "họ và tên" vào giá trị giữ chỗ), `src/steel_xai/llm/prompts.py` (dấu vân tay prompt băm cả user prompt).

## 3. Quyết định thiết kế (nhóm nên đọc)

- **Kappa đáp án** tính trên quyết định nhị phân "(ca, giả thuyết) có trong đáp án không", với giả thuyết **áp dụng được cho hướng của ca** (`both` hoặc cùng hướng) + `other` + giả thuyết ngược hướng mà ít nhất một người đã chọn. Bỏ giả thuyết ngược hướng không ai chọn để kappa không bị thổi phồng theo cỡ thư viện. `other` là một mục: hai người cùng chọn `other` nhưng mô tả khác nhau vẫn tính là đồng ý → đọc kèm note. Báo thêm tỷ lệ ca khớp hẳn và Jaccard trung bình.
- **Trạng thái chuẩn** (`statuses`) là tùy chọn, dùng cho verification accuracy (spec 15.3, chỉ trên top-3 của C ở tập test — chưa biết trước khi chạy C, nên nhóm có thể bổ sung sau; mọi bổ sung phải xong TRƯỚC khi đóng băng).
- **Đáp án cuối:** ca hai người khớp → dùng luôn; bất đồng (đáp án hoặc trạng thái, kể cả một bên có trạng thái một bên không) → bắt buộc quyết định của người thứ ba; quyết định có lỗi bị loại (không lọt vào đáp án).
- **Freeze:** chưa đóng băng → luôn từ chối chạy test (lý do không cứu được). Lệch → từ chối trừ khi `unfreeze_reason` ≥ 15 ký tự và ≥ 3 từ khác nhau; `FreezeCheck.to_dict()` (khác biệt, hash cũ/mới, lý do) phải được runner ghi vào kết quả. `STEEL_XAI_CONFIG_DIR` trỏ ra ngoài `configs/` → từ chối (config khi chạy khác config được băm).
- **Ánh xạ mù:** `item_id = sha256(salt|ca|cấu hình|lần chạy|hạng|văn bản)` với salt ngẫu nhiên 128 bit, chỉ lưu trong file key. Biết salt thì vét cạn được cấu hình (người gán đã thấy ca và văn bản), nên **không đưa file key cho người ánh xạ**.

## 4. Tests

`tests/test_eval.py`: 78 test (config, contract, danh sách ca, kappa với giá trị chuẩn sách giáo khoa, file nhãn, nguồn độc lập RAG, người gán, bất đồng/adjudication, freeze: đổi file/xuống dòng/prompt/ca/đáp án/env var/lý do/lịch sử, ánh xạ mù, ca sai). Toàn bộ repo: **1144 passed**, ruff sạch.

## 5. Review độc lập

1 vòng (agent riêng, không thấy quá trình viết code): 0 CRITICAL, 3 HIGH, 8 MEDIUM, 6 LOW. Đã sửa:

| Mức | Phát hiện | Sửa |
|---|---|---|
| HIGH | Freeze không băm code (luật verdict, bộ kiểm báo cáo) và dữ liệu đầu vào | `freeze_globs` gồm `src/**/*.py`, `scripts/*.py`, `data/external/*`, kho RAG, as-of, ứng viên, `pyproject.toml` |
| HIGH | `STEEL_XAI_CONFIG_DIR` lách freeze | từ chối khi trỏ ra ngoài `configs/` |
| HIGH | Freeze không khóa nhãn, đóng băng được khi chưa có nhãn | băm đáp án cuối + file ca/nhãn; `write_freeze` đòi mọi ca có đáp án |
| MEDIUM | Dấu vân tay prompt thiếu user prompt | băm `rephrase_user`, `classify_user` (cả hai chiều) |
| MEDIUM | Chạy được test khi chưa đóng băng; lý do "aaaa…" qua | chưa đóng băng → luôn từ chối; lý do cần ≥ 3 từ khác nhau |
| MEDIUM | `--overwrite` không để lại lịch sử | `history` + `manifest_sha256` |
| MEDIUM | Kappa phồng theo cỡ thư viện | chỉ giả thuyết áp dụng được; thêm khớp hẳn/Jaccard |
| MEDIUM | Quyết định adjudication lỗi vẫn vào đáp án | quyết định lỗi bị loại; người giải quyết không hợp lệ → bỏ mọi quyết định |
| MEDIUM | Kiểm nguồn độc lập RAG dễ lách | thêm cảnh báo BCTC công ty+năm trong kho; sửa comment config cho đúng code |
| MEDIUM | Tên giữ chỗ `<Họ và tên>` qua kiểm người | chặn `<>[]{}` và "họ tên"/"họ và tên" |
| MEDIUM | Biết seed thì đoán được cấu hình trong ánh xạ mù | salt ngẫu nhiên bí mật, lưu trong key |
| LOW | Ca test thiếu kết quả C bị bỏ qua im lặng; thiếu dòng trong file ánh xạ/ca sai không bị phát hiện; so tên người không chuẩn hóa dấu; doc_id không phải chuỗi làm crash, date không kiểm; file `.yml` bị bỏ qua; danh mục nguồn không được băm | đã sửa |

Chưa sửa (LOW): adjudicator ghi `reference_set` cho ca chỉ bất đồng trạng thái, hoặc quyết định cho ca không bất đồng → bị bỏ qua im lặng (không ảnh hưởng đáp án).

## 6. Known limitations

- Kiểm nguồn độc lập RAG không đầy đủ (xem trên). OCR thô (`data/raw`) không băm trong freeze (lớn); nó đi vào pipeline qua các file đã băm.
- Freeze không chứng minh thời điểm; cần commit.
- `failure_rows` nhận một top-3 mỗi ca; cách gộp 3 lần chạy do runner Phase 12 quyết.
- Ánh xạ mù chỉ mù khi văn bản giả thuyết của A/B không tự lộ nguồn (ví dụ trích đoạn RAG nguyên văn ở B).

## 7. Chưa làm vì ngoài scope

Runner A/B/C và ablation, metric (Hit@k, MRR, citation…), CI/bootstrap, biểu đồ (Phase 12). Nội dung ca/nhãn/ánh xạ/nguyên nhân ca sai (nhóm).

## 8. Hard-code công ty

Không. Công ty, loại bất thường, cửa sổ chọn ca, năm tham chiếu đọc từ config; test dùng mã giả AAA/BBB.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-25 — Phase 12a".

## 10. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới ở phần công cụ. Nếu nhóm giảm số ca (hiện 11 ứng viên so với 18 ca của spec), cần ghi vào `DEVIATIONS_FROM_MIDTERM.md`.
