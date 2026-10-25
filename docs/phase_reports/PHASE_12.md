# Báo cáo Phase 12 — Baselines + Evaluation (runner, metric, bảng)

- **Ngày:** 2026-10-25
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 11, 13, 14 (14.1, 14.2), 15 (15.1–15.8), 16, 18, 20 (Phase 12), 21.1, 23–25
- **Trạng thái:** code của Claude đã xong, test bằng LLM giả, qua 1 vòng review độc lập, mọi lỗi HIGH/MEDIUM đã sửa (mục 5). **Chưa COMPLETED**: chưa gọi API thật lần nào, chưa có nhãn của người gán thứ hai (Hoàng Minh Đức), chưa đóng băng, chưa chạy tập test.

## ⚠ Điều nhóm cần biết

- **Thứ tự phase.** Spec mục 28.3 yêu cầu phase trước COMPLETED rồi mới làm phase sau. Phase 6–11 và 12a chưa COMPLETED. Nhóm đã quyết làm tiếp Phase 12, giống các phase trước.
- **Chưa gọi API.** `estimate --split dev` trên dữ liệu thật: tối đa **438 lời gọi LLM** cho 4 ca dev × 8 cấu hình × 3 lần, trong đó C khoảng 19–21 lời gọi/lần và no_library khoảng 11. Nhóm chốt provider/model trước khi chạy (hiện là Gemini free tier, nên lỗi 429/503 có thể xảy ra).
- **Nhóm cần chốt trước khi đóng băng** (đều nằm trong `configs/eval.yaml: runner`, là đề xuất của Claude):
  1. `baseline_queries`: truy vấn chung của B theo loại bất thường. Truy vấn sát thư viện quá sẽ làm B mạnh lên một cách không công bằng, và ngược lại.
  2. `other_counts_as_hit: false`: `other` trong đáp án không tính là trúng khi A/B được ánh xạ về `other`. Lý do: hai "other" có thể là hai nguyên nhân khác nhau, và C không sinh được `other`.
  3. `max_hypotheses: 5`, `max_passages: 12`, `pricing` (giá token, để `null` thì chỉ báo token), `success_criteria` (để `null` thì dùng mặc định spec 15.7).
- **Sinh bảng cho người SAU KHI chạy xong mọi cấu hình.** Sinh lại sẽ đổi `item_id`; lệnh từ chối ghi đè key nếu không có `--overwrite`.
- **Chấm tạm trên dev:** `score --split dev --provisional "Nguyễn Tùng Dương"` dùng nhãn của một người. Mọi bảng ghi rõ "ĐÁP ÁN TẠM". Tập test không chấm tạm được.

### Cập nhật 2026-10-25: chốt LLM và ước tính chi phí

- Nhóm chốt `gemini-3.5-flash-lite` **free tier** cho cả đánh giá chính thức: `configs/llm.yaml`, `eval.runner.pricing` = 0, dòng DEVIATIONS đã cập nhật.
- `docs/COST_ESTIMATE.md` (sinh bởi `scripts/estimate_cost.py`, không gọi API): chi phí nếu chạy cùng khối lượng (11 ca × 8 cấu hình × 3 lần) bằng model tính phí. Giá trong `configs/cost/pricing.yaml` (nguồn: trang giá Google ngày 2026-10-22, bảng giá Anthropic ngày 2026-10-06), đặt ngoài `freeze_globs` nên sửa giá không làm lệch freeze. Token vào đo từ prompt dựng trên dữ liệu thật; token ra là giả định (khoảng ×0,5–×2). Sau khi chạy free tier, `--source measured` dùng token đo thật.

## Phase này để làm gì?

Trả lời RQ1–RQ3 bằng số đo được: chạy ba cấu hình A (LLM only), B (LLM + RAG), C (Full System), chạy phụ (A/B nhận danh sách thư viện) và 3 ablation, mỗi ca 3 lần. Sau đó tính metric mục 15.3 kèm CI, xuất bảng/biểu đồ, và sinh bảng cho người (ánh xạ mù, chấm trích dẫn, UNVERIFIED, ca sai).

## 1. Đã làm được gì

| Thành phần | Mô tả |
|---|---|
| `contracts.RunRecord` (spec 18) | ca, split, cấu hình, lần chạy, `outputs` (ranking/report/verdicts), token, độ trễ thật, thông tin LLM, lỗi, `freeze`. |
| `eval/baselines.py` | Prompt + schema cho A/B (sinh tự do + báo cáo), A_lib/B_lib/no_verification (xếp hạng id thư viện), no_library (sinh giả thuyết + truy vấn). Kiểm khẳng định của A/B bằng **cùng** bộ kiểm số và kiểm nguyên văn với C. `baseline_prompt_fingerprint` được băm vào freeze. |
| `eval/runner.py` | `EvalContext` (dựng từ dữ liệu thật bằng `build_context`), `run_one` (lỗi → record `error`, không dừng cả lượt), `run_eval` (bỏ qua bản đã có, chạy lại bản lỗi, `force`), `estimate_calls`. Tập test bắt buộc có `FreezeCheck`, ghi vào mọi record. |
| `eval/metrics.py` | Hit@k, MRR (kỳ vọng khi phá hòa ngẫu nhiên + cận lạc quan/bi quan), bootstrap CI theo ca, độ lệch giữa các lần chạy, metric báo cáo, verification accuracy/false-SUPPORTED, RQ3, chi phí, ca sai. |
| `eval/export.py` | Bảng cho người (mù), gộp hai người + người thứ ba, kappa, `results.json`, `summary.md`, `per_case.{csv,md}`, `rq3.csv`, `hit_chart.svg`, `status_heatmap.svg`, ca minh họa (Markdown + SVG chuỗi giá). Không cần thư viện vẽ mới. |
| `eval/references.py` | Nạp đáp án cuối (dùng chung với `eval_tools.py`) và đáp án tạm một người (chỉ dev). |
| `scripts/run_eval.py` | `estimate` (không gọi API), `run`, `sheets`, `score`. |

### Các cấu hình

| Cấu hình | Đầu vào LLM | LLM làm | Kiểm chứng | Báo cáo (RQ2) |
|---|---|---|---|---|
| A | bất thường + bảng chỉ số (mã N) | sinh ≤5 giả thuyết, xếp hạng, viết khẳng định | không | có (một bản) |
| B | như A + ≤12 đoạn truy xuất (mã P, lọc as-of, truy vấn chung theo loại) | như A, trích nguyên văn | không | có (một bản) |
| C | 4 bước | diễn đạt truy vấn, phân loại đoạn, viết báo cáo | check định lượng + văn bản + luật | có (nháp đầu + bản cuối) |
| A_lib / B_lib | như A / B + danh sách giả thuyết reviewed áp dụng | xếp hạng id | không | không |
| no_rag | — | không gọi LLM | chỉ định lượng | không |
| no_verification | thư viện + đoạn truy xuất theo truy vấn của thư viện | xếp hạng id | không | không |
| no_library | như A | sinh giả thuyết + truy vấn | mỗi giả thuyết 1 check văn bản critical, cùng luật | không |

## 2. File

- **Tạo:** `src/steel_xai/eval/{baselines,runner,metrics,export,references}.py`, `scripts/run_eval.py`, `tests/test_eval_runner.py`, file này.
- **Sửa:** `src/steel_xai/contracts.py` (`RunRecord`), `src/steel_xai/eval/settings.py` (`RunnerSettings`), `src/steel_xai/eval/freeze.py` (băm prompt baseline), `configs/eval.yaml` (`secondary_configs`, mục `runner`), `scripts/eval_tools.py` (dùng `references.py`), `pyproject.toml` (miễn E501 cho prompt baseline, giống `llm/prompts.py`), `tests/test_eval.py` (2 kỳ vọng: split 4/7 theo quyết định nhóm; thêm fingerprint `baselines`).

## 3. Quyết định thiết kế (nhóm nên đọc)

- **Hòa hạng.** C chia chung hạng (spec 12.2), nên nhiều giả thuyết UNVERIFIED có thể cùng hạng. Hit@k/MRR lấy **kỳ vọng khi phá hòa ngẫu nhiên** (công thức tổ hợp, có test so với vét cạn), kèm cận bi quan (giả thuyết đúng đứng cuối nhóm hòa) và lạc quan. Nếu đếm "hạng ≤ k là trúng" thì một nhóm hòa lớn sẽ thổi phồng Hit@k.
- **Unsupported claim** = khẳng định không có nguồn hợp lệ nào. Định nghĩa này giống nhau cho A/B/C. Với B, trích dẫn chỉ thành nguồn khi qua kiểm nguyên văn. So sánh chính dùng **bản nháp đầu** của C (C còn sinh lại, A/B thì không), bản cuối của C báo riêng.
- **Numeric mismatch** dùng cùng bộ kiểm của bước 4. Số trong khẳng định không dẫn nguồn N/E nào bị tính là lệch.
- **Citation support rate** = (trích dẫn qua kiểm nguyên văn VÀ được người chấm là `supports`) / mọi trích dẫn. `partial` không tính là hỗ trợ. Hai người chấm mù cấu hình, bất đồng thì người thứ ba quyết.
- **Verification accuracy / false-SUPPORTED**: cặp (ca, giả thuyết) thuộc top-3 của C **ở cùng lần chạy**, có trạng thái chuẩn. no_rag được chấm trên cùng tập cặp đó. no_verification không có trạng thái; no_library sinh giả thuyết tự do nên không có trạng thái chuẩn để so.
- **Ca sai (15.5):** ca test mà giả thuyết đúng không nằm trong top-3 của C ở **quá nửa** số lần chạy (một lần chạy tính là trượt khi Hit@3 kỳ vọng < 0,5).
- **CI:** bootstrap phân vị theo ca, 2000 mẫu, seed cố định. Với 7 ca test, CI sẽ rất rộng; đây là lý do mọi bảng có câu "khảo sát khả thi".
- **Ablation no_library** không có check định lượng vì công cụ định lượng gắn với từng giả thuyết của thư viện. Trạng thái của nó chỉ dựa trên văn bản. Đây là giới hạn cần nêu khi báo cáo.
- **Baseline không có vòng kiểm/sinh lại.** Bộ kiểm chỉ dùng để đo, không sửa đầu ra của A/B.

## 4. Tests

`tests/test_eval_runner.py`: 49 test, gồm:
- từng cấu hình với LLM giả định tuyến theo schema;
- chống rò rỉ as-of ở B; trích dẫn bịa/không khớp/không trích đều không được tính;
- resume, force, bản lỗi được chạy lại, test split bị chặn khi thiếu freeze, `run_id` nằm trong khóa cache;
- Hit@k/MRR với hòa hạng (so với vét cạn hoán vị); bootstrap tái lập được;
- verification accuracy trên top-3 của C; metric báo cáo + chấm người; chi phí; RQ3; ca sai;
- bảng mù không lộ cấu hình; gộp hai người + người thứ ba; mọi bảng có câu "khảo sát khả thi"; định dạng số Việt.

Toàn repo: **1193 passed**, ruff sạch.

## 5. Review độc lập

1 vòng (agent riêng, không thấy quá trình viết code): 0 CRITICAL, 4 HIGH, 10 MEDIUM, 6 LOW. Reviewer xác nhận đúng: công thức Hit@k/MRR khi hòa, bootstrap, luật ca sai, `run_id` trong khóa cache, freeze bắt buộc cho tập test, lọc as-of ở B, code không tự điền nội dung mục 21.1.

| Mức | Phát hiện | Sửa |
|---|---|---|
| HIGH | Ánh xạ mù khóa theo hạng: giả thuyết hòa hạng ghi đè nhau | khóa theo VỊ TRÍ trong danh sách; test hòa hạng |
| HIGH | Lỗi LLM trong C bị nuốt thành UNVERIFIED/báo cáo rỗng, record vẫn `ok` | lần chạy có lời gọi LLM thất bại = `error` (chạy lại) |
| HIGH | Resume giữ kết quả cũ dù code/prompt/config đã đổi | `RunRecord.fingerprint` (code, config, dữ liệu, prompt, tham số runner; không gồm nhãn); khác băm → chạy lại; `score --split test` từ chối khi trộn phiên bản/manifest hoặc còn lần chạy lỗi |
| HIGH | Lần chạy lấy từ cache ghi token 0, độ trễ ~0 vào chi phí | loại khỏi số đo chi phí, đếm `cached_runs_excluded` |
| MEDIUM | Báo cáo bị từ chối tính là "0 lệch số ở bản cuối" | bản cuối = None khi bị từ chối; thêm tỷ lệ `report_rejected` |
| MEDIUM | So A/B/C trên tập ca khác nhau khi có lỗi/chưa ánh xạ | thêm bảng trên tập ca chung; tiêu chí thành công dùng tập chung |
| MEDIUM | Citation của C đo trên bản cuối, verbatim luôn 100% | ghi rõ trong tiêu đề cột |
| MEDIUM | Adjudication ánh xạ không kiểm id | chỉ nhận id cùng loại bất thường hoặc `other` |
| MEDIUM | Chất lượng UNVERIFIED tính cả khi bảng lỗi/thiếu/đếm đôi | một file, đối chiếu khung; có lỗi → không tính |
| MEDIUM | CSV kết quả thiếu câu "khảo sát khả thi" | dòng cuối mọi CSV kết quả |
| MEDIUM | Không sinh ca minh họa, không render bảng ca sai | `case_<id>.md` + SVG cho mỗi ca; bảng nguyên nhân ca sai trong summary |
| MEDIUM | B chạy không có đoạn truy xuất mà vẫn `ok` | lỗi rõ khi thiếu retriever/as-of/truy vấn; lớp kiểm as-of thứ hai |
| MEDIUM | Văn bản cho người chấm mù lộ mã N/E/H/P và trạng thái | thay bằng `[mã]`, `[trạng thái]` |
| MEDIUM | Lần chạy chưa chấm trích dẫn biến mất im lặng | đếm `citation_unrated_runs` |
| LOW | Ca chỉ có đáp án `other` | báo trong đầu bảng |
| LOW | CLI văng traceback (provisional sai tên, bảng ca sai), `--runs` bị cắt im lặng | bắt lỗi, in cảnh báo |

Chưa sửa (LOW, ghi nhận):
- no_library không có verification accuracy (giả thuyết tự do không có trạng thái chuẩn);
- A/B trả 0 khẳng định thì ca bị bỏ khỏi unsupported rate;
- adjudication thừa / item lạ trong bảng chấm bị bỏ qua im lặng;
- A/B chỉ có tối đa 5 giả thuyết nên MRR của chúng có cận thấp hơn C;
- chưa gộp văn bản trùng nhau trong bảng ánh xạ mù (3 lần chạy giống hệt nhau có thể thành cụm dễ nhận).

Claude tự bắt khi sửa: lỗi heredoc biến `\b` thành ký tự backspace trong regex của `blind_text` (đã sửa, đã quét toàn repo).

## 6. Known limitations

- Chưa gọi API thật: chưa biết tỷ lệ lỗi JSON/429 của model chạy thử, chưa đo độ trễ/chi phí thật.
- Bảng chấm trích dẫn chỉ mù một phần: câu khẳng định của C thường nhắc trạng thái (UNVERIFIED…), người chấm có thể đoán được đó là C.
- Ca có đáp án chỉ gồm `other` thì không cấu hình nào trúng được (khi `other_counts_as_hit: false`). Ca này vẫn được tính, phản ánh giới hạn độ phủ của thư viện.
- Độ trễ đo theo thời gian thực của lần chạy. Lần chạy lại lấy từ cache LLM sẽ nhanh hơn thật, nhưng runner giữ bản đã có trừ khi `--force`.
- 7 ca test cho CI rất rộng.

## 7. Chưa làm vì ngoài scope

Demo (Phase 13). Nội dung bảng cho người (ánh xạ mù, chấm trích dẫn, nguyên nhân UNVERIFIED, nguyên nhân ca sai): việc của nhóm (spec 21.1). Chốt chỉ số thành công và ngưỡng: việc của nhóm.

## 8. Hard-code công ty

Không. Truy vấn baseline theo loại bất thường, cấu hình đọc từ config; test dùng mã giả AAA.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-25 — Phase 12".

## 10. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới so với giữa kỳ. Số ca 11 (4 dev + 7 test) đã ghi ở DEVIATIONS (Phase 12a).
