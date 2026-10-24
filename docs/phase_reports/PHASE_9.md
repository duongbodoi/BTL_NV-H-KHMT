# Báo cáo Phase 9 — LLM reasoning integration

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.2, 10.3, 10.4, 11, 16, 18, 20 (Phase 9), 23
- **Trạng thái:** phần việc của Claude đã xong, qua 1 vòng review độc lập, lỗi HIGH/MEDIUM đã sửa. **Chưa COMPLETED**: Phase 6 chưa có giả thuyết `reviewed`, Phase 7–8 còn điểm tạm, và **chưa từng gọi API Claude thật** (xem mục đầu). Phase 9 xây trên đầu vào chưa xác minh hoàn toàn (spec mục 28.3); nhóm đã quyết định đi tiếp.

## ⚠ Điều nhóm cần biết

- **Chưa thử với API thật.** Gói `anthropic` không cài trong môi trường này và không có API key. `AnthropicTransport` đã được viết theo tài liệu API hiện hành và được test bằng module `anthropic` giả (kiểm đúng hình dạng request), nhưng **lần chạy thật đầu tiên có thể lộ lỗi** (ví dụ tên tham số, hành vi `output_config` kết hợp `effort`). Chạy thử một vài ca nhỏ trước (`scripts/run_text_evidence.py --limit 1`), và đo chi phí bằng `--dry-run` rồi nhật ký `data/processed/llm_calls.jsonl`.
- **Model, effort, `max_tokens` trong `configs/llm.yaml` là đề xuất của Claude** (`claude-opus-5-5`, effort `medium`, 8000 token); nhóm chốt trước khi đánh giá, vì đổi chúng làm hash freeze lệch.
- **Không gửi `temperature`/`top_p`/`top_k`**: API từ phiên bản 2026-10-21 trả 400 nếu có, và các mô hình Claude 5.x chỉ nhận giá trị mặc định. Loader từ chối nếu ai đó đặt chúng trong config. Độ biến động của 3 lần chạy (spec mục 14) vì vậy đến từ lấy mẫu mặc định, không chỉnh được.
- **Từ chối (refusal) hiện chỉ được xử lý, không được "cứu".** Mô hình có thể từ chối bằng bộ phân loại an toàn; client ghi `refusal` và check đó thành `UNVERIFIED`. Chưa bật tính năng fallback phía server của API (không thử được khi chưa có API thật). Nhóm có thể bật sau nếu thấy tỷ lệ từ chối cao.
- **Chi phí:** với 11 bất thường ứng viên và thư viện nháp, `--dry-run` ước tối đa 200 lời gọi LLM mỗi lần chạy; × 3 lần chạy × số ca. Chưa có số tiền thật.

## Phase này để làm gì?

Bước 3 phần văn bản của pipeline (spec mục 6, 10.3, 10.4, 11): dùng LLM đúng hai việc mà spec cho phép ở bước này — diễn đạt lại truy vấn và phân loại đoạn truy xuất — còn mọi quyết định (check nào chạy, đoạn nào xét, trích dẫn nào giữ, trạng thái check) do **code** làm.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `llm/client.py` — `LLMClient` | Lớp bọc mỏng, không agent framework. **Structured output bắt buộc**: đầu ra phải là JSON qua schema (kiểm lại trong code; sai → gọi lại tối đa `max_retries_invalid_output`, rồi `LLMOutputError`). **Cache đĩa theo hash** của (model, effort, max_tokens, tác vụ, `run_id`, system, user, schema, phiên bản): 3 lần chạy khác `run_id` không trùng cache; cache hỏng hoặc không còn khớp schema bị bỏ. **Log mỗi lần gọi** (JSONL): token vào/ra tính tiền, độ trễ, cache hit, số lần thử, trạng thái, request id. Refusal, cắt do `max_tokens`, `stop_reason` lạ, JSON trùng khóa: lỗi rõ ràng, không thử lại mù. Lỗi ghi cache/log chỉ thành cảnh báo, không làm mất kết quả đã trả tiền. |
| `AnthropicTransport` | Gọi Messages API với `output_config.format = json_schema` + `effort`; không `tool_choice` ép buộc (Claude 5.5 trả 400), không sampling params; lấy block `text` đầu tiên (bỏ qua block `thinking`). Key chỉ từ biến môi trường. |
| `llm/schema.py` | Kiểm JSON theo tập con nhỏ của JSON Schema (type, properties, required, additionalProperties=false, items, enum), không thêm phụ thuộc. |
| `llm/quotes.py` — `verify_quote` | Trích dẫn phải là **chuỗi con nguyên văn** của ĐÚNG đoạn được gán, sau chuẩn hóa khoảng trắng (kể cả NBSP), Unicode NFC, bỏ ký tự vô hình. Không bỏ hoa/thường, dấu câu hay dấu tiếng Việt; không chấp nhận "…" nối mẩu. Chốt chất lượng: độ dài 20–600 ký tự, ≥ 40% ký tự là chữ (chặn quote chỉ gồm `\|` hoặc số), không bắt đầu/kết thúc giữa một từ. |
| `llm/evidence.py` | `rephrase_queries`: LLM chỉ **thêm** cách diễn đạt; check_id lạ bị bỏ, truy vấn có chữ số hoặc mã công ty bị bỏ, trùng sau chuẩn hóa bị bỏ, tối đa 3 mỗi check; truy vấn gốc luôn được giữ, lỗi LLM → dùng truy vấn gốc. `classify_passages`: LLM gán `support/contradict/irrelevant` + trích dẫn; code kiểm từng trích dẫn → thành `Evidence` (kèm provenance đầy đủ) hoặc bị loại. `gather_text_evidence`: điều phối cho một giả thuyết × một bất thường. |
| Trạng thái check văn bản | `FOUND` (có bằng chứng đã kiểm đúng chiều của check) · `MISSING` (LLM nêu bằng chứng đúng chiều nhưng trích dẫn không khớp → bị loại; spec 10.4) · `UNVERIFIED` (không truy xuất được đoạn nào, chỉ có đoạn không liên quan/ngược chiều, hoặc LLM lỗi; spec 10.3). Chunk LLM bịa, trích dẫn rỗng, judgement trùng **không** tạo MISSING. |
| Chống rò rỉ | Đoạn gửi cho LLM chỉ là đoạn `BM25Retriever` trả về (đã lọc as-of và công ty); chốt chặn thứ hai trong `gather_text_evidence` kiểm lại ngày công bố và công ty (test cho một retriever hỏng giả lập). Thiếu as-of trên bất thường → `UNVERIFIED`, không gọi gì. |
| Chống prompt injection | Mỗi đoạn nằm giữa dòng mở `<<<passage-KEY …>>>` và dòng đóng `<<<end-passage-KEY>>>`; KEY được chọn sao cho **không xuất hiện trong bất kỳ văn bản nào** (xác định theo nội dung nên cache ổn định). Văn bản đoạn **không bị sửa** (nên trích dẫn nguyên văn vẫn khớp); `section`, `chunk_id` mã hóa JSON trên một dòng. |
| `contracts.Evidence`, `TextCheckStatus` | Hợp đồng chung (spec mục 18). |
| `prompts.prompt_fingerprint()` | Băm toàn bộ prompt và schema để `eval_freeze.json` (Phase 12) phát hiện thay đổi. |
| `scripts/run_text_evidence.py` | Chạy trên bất thường thật, ghi `text_evidence.jsonl`; `--dry-run` đếm lời gọi mà không gọi API. |

## 2. File

- **Tạo:** `src/steel_xai/llm/{settings,schema,client,quotes,prompts,evidence}.py`, `scripts/run_text_evidence.py`, `tests/test_llm.py`, `docs/phase_reports/PHASE_9.md`.
- **Sửa:** `configs/llm.yaml` (mở rộng, bỏ `temperature`), `contracts.py` (`Evidence`, `TextCheckStatus`), `llm/__init__.py`, `pyproject.toml` (extra `llm = ["anthropic>=1.0"]`; ruff bỏ E501 cho `prompts.py`).

## 3. Quyết định thiết kế (nhóm nên đọc)

- **Ranh giới LLM/code theo đúng mục 11 của spec.** LLM không quyết định giả thuyết đúng hay sai, không tính số, không thêm/bớt check. Phase 10 sẽ dùng `TextCheckOutcome` + `CheckResult` (Phase 8) để gộp trạng thái SUPPORTED/PARTIAL/CONTRADICTED/UNVERIFIED bằng luật trong code.
- **Mỗi đoạn tối đa một bằng chứng:** nếu LLM trả nhiều judgement cho cùng đoạn, lấy judgement đầu tiên có trích dẫn qua kiểm; các judgement còn lại ghi `duplicate`.
- **Bằng chứng ngược chiều với check vẫn được giữ** (ví dụ check tìm ủng hộ nhưng LLM thấy đoạn chống lại): check ra `UNVERIFIED` nhưng `evidence` chứa đoạn đó với `polarity=contradict`, để Phase 10 hiển thị cả hai.
- **Trích dẫn so khớp theo dạng chuẩn hóa NFC + khoảng trắng** (`Evidence.quote` là chuỗi con của dạng chuẩn hóa đó), không phải byte-by-byte; ký tự vô hình trong văn bản gốc bị bỏ ở cả hai phía.
- **`run_id` bắt buộc trong khóa cache** nhưng không có tham số lấy mẫu: ba lần chạy cùng ca khác nhau chỉ nhờ lấy mẫu mặc định của mô hình; chạy lại cùng `run_id` cho kết quả y hệt từ cache (tái lập, spec mục 16).

## 4. Tests

`tests/test_llm.py` (~75 ca) + toàn repo **861 passed**; `ruff check`/`ruff format --check` sạch. **Không có test nào gọi mạng hoặc tiêu token** (transport giả; `AnthropicTransport` test bằng module `anthropic` giả). Gồm: config (cấm sampling params); schema; client (structured output, token/độ trễ, cache hit không tốn token, khóa cache gồm `run_id` và các tham số, cache hỏng, retry cộng dồn token, refusal/cắt cụt không retry, lỗi transport, lỗi ghi đĩa không mất kết quả, JSON trùng khóa, ký tự đại diện lẻ); hình dạng request của `AnthropicTransport`; trích dẫn (khoảng trắng, NBSP, NFC/NFD, hoa/thường, dấu tiếng Việt, nối mẩu, quá ngắn/dài, chỉ gồm `|` hoặc số, cắt giữa từ, ký tự vô hình); phân loại (quote khớp → `Evidence` có provenance đầy đủ; từng kiểu quote sai → bị loại + MISSING; quote đúng nhưng thuộc đoạn khác; chunk bịa; judgement trùng; chỉ irrelevant; ngược chiều; LLM lỗi; không có đoạn; thiếu as-of); chống rò rỉ (đoạn sau as-of không bao giờ tới prompt, công ty khác không tới prompt, retriever hỏng bị chốt chặn bắt); prompt injection (KEY, section thù địch, thẻ viết hoa/thường); diễn đạt lại (check lạ, trùng, chữ số, mã công ty, giới hạn, lỗi → truy vấn gốc); băm prompt.

## 5. Review độc lập

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 1 | 4 | 5 |

Đã sửa:
- **HIGH:** lỗi ghi cache/log (đĩa đầy, đường dẫn sai) làm văng `OSError`, mất kết quả đã trả tiền và sập cả pipeline → nay chỉ cảnh báo, token vẫn được ghi.
- **MEDIUM:** `section` chứa dấu nháy/xuống dòng giả được thuộc tính của prompt, và `_escape` phân biệt hoa/thường (đồng thời làm trích dẫn đúng bị loại oan vì văn bản bị sửa) → thay bằng ranh giới KEY không giả được, thuộc tính mã hóa JSON, không sửa văn bản; MISSING bị lạm dụng (chunk bịa, quote rỗng, judgement trùng) → chỉ khi trích dẫn thật sự không khớp; judgement đầu hỏng làm judgement sau đúng bị bỏ → nay thử lần lượt; truy vấn diễn đạt lại không kiểm số liệu/mã công ty và không chuẩn hóa Unicode khi loại trùng.
- **LOW:** chốt chất lượng trích dẫn (quote chỉ gồm `|`/số, cắt giữa từ); polarity lạ → lỗi rõ ràng; đoạn LLM bỏ sót được báo; `pause_turn`, JSON trùng khóa, ký tự đại diện lẻ, file tạm cache đua ghi; `timeout_s` NaN, `max_tokens` quá lớn.
- **Chưa sửa (ghi nhận):** `call_log` chưa xoay vòng (JSONL tăng dần); trích dẫn quá dài vẫn có thể gần như cả đoạn (≤ 600 ký tự); tên đầy đủ của công ty (không phải mã) trong truy vấn diễn đạt lại chưa bị chặn bằng code (chỉ prompt cấm).

## 6. Known limitations

- Chưa gọi API thật; `AnthropicTransport` chưa được xác nhận end-to-end.
- Chất lượng phân loại của LLM chưa đo (cần tập dev có nhãn, Phase 12); trích dẫn đúng nguyên văn **không** chứng minh phân loại đúng ngữ nghĩa.
- Kho RAG chỉ có thuyết minh BCTC (xem Phase 7), nên nhiều check văn bản sẽ `UNVERIFIED`.
- Chưa có prompt cho baseline A/B (sinh giả thuyết tự do) và cho bước viết báo cáo (bước 4) — thuộc Phase 11–12.
- Chưa dùng prompt caching / batch của API (có thể giảm chi phí khi chạy 3 lần × 18 ca).

## 7. Chưa làm vì ngoài scope

Gộp trạng thái giả thuyết và xếp hạng (Phase 10); viết báo cáo + kiểm số trong báo cáo (Phase 11); baseline A/B và freeze/eval (Phase 12); bật fallback khi bị từ chối; đo chi phí thật.

## 8. Hard-code công ty

Không. Mã công ty đọc từ `configs/dataset.yaml` (chỉ để **chặn** chúng trong truy vấn diễn đạt lại); prompt không nêu tên công ty.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-24 — Phase 9".

## 10. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới (LLM phân loại bằng chứng ở bước 3 đã nằm trong các điểm đã chốt, spec mục 26.2).
