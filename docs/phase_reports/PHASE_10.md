# Báo cáo Phase 10 — Hypothesis verification / ranking

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 10.1–10.4, 12.1, 12.2, 18, 20 (Phase 10), 23
- **Trạng thái:** phần việc của Claude đã xong, qua 1 vòng review độc lập, lỗi/rủi ro đã xử lý. **Chưa COMPLETED**: các phase đầu vào (6, 8, 9) chưa COMPLETED và **thư viện chưa có giả thuyết `reviewed`**, nên chưa có kết quả nào dùng được để đo (xem mục đầu). Phase 10 xây trên đầu vào chưa xác minh hoàn toàn (spec mục 28.3); nhóm đã quyết định đi tiếp.

## ⚠ Điều nhóm cần biết

- **Chưa có kết quả đo được.** Mọi giả thuyết hiện là `draft`; `analyze_anomaly` từ chối chúng (trừ khi bật `allow_draft=True` để chạy thử). `data/processed/verdicts.jsonl` do `scripts/run_verdicts.py` sinh ra chỉ để **xem thử**, có cờ `used_drafts: true`, **không được dùng để tính metric**.
- **"SUPPORTED" trong lần chạy thử nghĩa là "mọi check critical đều PASS"**, mà các check và ngưỡng đó phần lớn do Claude đề xuất trong `library_draft.yaml`, chưa được nhóm duyệt. Lần chạy thử trên 11 bất thường thật (chỉ phần định lượng, không gọi API) ra: 7 SUPPORTED, 18 PARTIAL, 29 UNVERIFIED, 29 CONTRADICTED (sau khi thêm giá HRC/than cốc, 2026-10-24: 9 SUPPORTED, 15 PARTIAL, 29 UNVERIFIED, 30 CONTRADICTED; xem `PHASE_8.md` mục 3c). **Cả 7 SUPPORTED đều không có bằng chứng văn bản nào** (không chạy RAG/LLM; `HypothesisVerdict.has_verified_text` = false). Báo cáo (Phase 11) phải hiển thị điều này, và phân tích RQ3 nên tách cột "có bằng chứng văn bản đã kiểm" khỏi cột trạng thái.
- **Ba lựa chọn mà spec để ngỏ, tôi đã chọn và đặt vào config/ghi chú; nhóm nên xác nhận vì chúng ảnh hưởng Hit@k, MRR, false-SUPPORTED:**
  1. **Check critical-support bị FAIL** (số liệu đã tính ra mà kỳ vọng quan trọng không xảy ra) có tính là bằng chứng chống lại không? Config `critical_support_fail_policy`: `never` (chỉ check `contradict` hoặc đoạn văn bị gán `contradict` mới làm CONTRADICTED), `if_none_pass` (**mặc định**: CONTRADICTED chỉ khi không còn check critical-support nào đạt, còn đạt một phần thì PARTIAL) hoặc `always`. Lúc đầu tôi chọn `always`; review chỉ ra rằng nó làm 30/83 verdict thật thành CONTRADICTED vì ngưỡng check chưa được duyệt, và đẩy giả thuyết đúng-nhưng-ngưỡng-chặt xuống dưới cả UNVERIFIED, nên đổi mặc định.
  2. **Check văn bản critical + `contradict` mà không tìm thấy bằng chứng** có chặn SUPPORTED không? Mặc định **không** (`text_critical_contradict_unknown_blocks: false`): văn bản không chứng minh được sự vắng mặt, nếu chặn thì loại check này không bao giờ SUPPORTED. Check **định lượng** critical + `contradict` chưa kiểm được thì luôn chặn. Nên chạy ablation true/false khi đo.
  3. **Hòa hạng** theo kiểu cạnh tranh (1, 1, 3). Nhóm hòa hiển thị theo `hypothesis_id`, nhưng **hạng là chung**; Phase 12 phải dùng `rank` (không dùng vị trí trong danh sách) khi tính Hit@k và MRR (MRR cần chốt dùng hạng tốt nhất của nhóm hòa hay trung bình).
- **Một đoạn văn bản bị LLM gán nhãn `contradict` (dù đã qua kiểm trích dẫn nguyên văn) là đủ để kéo giả thuyết xuống CONTRADICTED.** Nhãn sai sẽ làm giả thuyết tụt hạng (không gây false-SUPPORTED). Chưa yêu cầu từ hai nguồn trở lên; ghi nhận là giới hạn.

## Phase này để làm gì?

Bước 3 phần cuối của pipeline (spec mục 6, 12): gộp kết quả các check (định lượng của Phase 8, văn bản của Phase 9) thành **trạng thái** SUPPORTED / PARTIAL / CONTRADICTED / UNVERIFIED bằng **hàm xác định trong code**, rồi **xếp hạng** các giả thuyết của một bất thường. LLM không tham gia.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `contracts.VerdictStatus`, `CheckFinding`, `HypothesisVerdict` | Hợp đồng (spec mục 18) có `evidence`, `contradictions`, `missing`, thêm `failed` (kỳ vọng đã kiểm mà không xảy ra), các số đếm cho xếp hạng và `has_verified_text`. `to_dict/from_dict` không mất thông tin. |
| `verification/verdict.py` — quy về một thang | quant: PASS → satisfied, FAIL → not_satisfied, NOT_EVALUABLE → unknown. text: FOUND (và **có bằng chứng đã kiểm đúng chiều**) → satisfied; UNVERIFIED/MISSING → unknown. **Văn bản không bao giờ not_satisfied.** Với check `support`, satisfied = ủng hộ; với `contradict`, satisfied = quan sát được bằng chứng chống lại. |
| Luật trạng thái (mục 12.1) | CONTRADICTED nếu có bằng chứng rõ chống lại (xem trên); SUPPORTED nếu có ≥ 1 check critical-support, mọi check critical-support satisfied, không contradiction, và không có check critical-contradict định lượng chưa kiểm được; PARTIAL nếu chưa SUPPORTED nhưng có bằng chứng ủng hộ; UNVERIFIED còn lại. Mỗi check của thư viện đều có finding (LLM/bên ngoài không thêm/bớt check); kết quả cho check không tồn tại, sai loại hay gán nhầm `check_id` là lỗi lớn tiếng. |
| Xếp hạng (mục 12.2) | SUPPORTED > PARTIAL > UNVERIFIED > CONTRADICTED; cùng trạng thái: tỷ lệ check critical-support đạt (số hữu tỉ chính xác, `Fraction`; SUPPORTED luôn bằng 1), rồi số check supporting đạt; hòa chia chung hạng (1, 1, 3); độc lập thứ tự đầu vào. |
| `verification/analyze.py` — `analyze_anomaly` | Lọc giả thuyết theo loại và hướng; **chỉ giả thuyết `reviewed` được vào** (guard chỉ áp dụng cho giả thuyết thật sự được phân tích; `allow_draft=True` để chạy thử và đặt `used_drafts`). Tắt `quant_ctx` hoặc `text_fn` để làm ablation (bỏ RAG, bỏ kiểm chứng): thiếu dữ liệu chỉ làm trạng thái đi xuống. |
| `scripts/run_verdicts.py` | Chạy trên bất thường thật; mặc định chỉ định lượng, không gọi API; `--with-text` thêm LLM (tốn token). In UTF-8 an toàn trên Windows. |

## 2. File

- **Tạo:** `src/steel_xai/verification/{verdict,analyze}.py`, `scripts/run_verdicts.py`, `tests/test_verdict.py`, `docs/phase_reports/PHASE_10.md`.
- **Sửa:** `contracts.py` (`VerdictStatus`, `CheckFinding`, `HypothesisVerdict`), `configs/verification.yaml` (khối `verdict`), `verification/__init__.py`, `scripts/run_{text_evidence,quant_checks}.py` (mã hóa UTF-8).

## 3. Tests

`tests/test_verdict.py` (~75 ca) + toàn repo **918 passed**; `ruff check`/`ruff format --check` sạch. Gồm **golden test cho từng trạng thái** (bảng 15 tổ hợp định lượng, văn bản FOUND/MISSING/UNVERIFIED, bằng chứng ngược chiều, hỗn hợp), **ba chính sách FAIL critical**, quy tắc ưu tiên của mục 12.1 (CONTRADICTED giữ cả bằng chứng ủng hộ), **thiếu dữ liệu không bao giờ thành SUPPORTED** (không có kết quả nào → UNVERIFIED cho cả 22 giả thuyết thư viện; kiểm bất biến trên 1.500 ca ngẫu nhiên: SUPPORTED chỉ khi mọi critical-support satisfied và không contradiction; đổi một kết quả "đạt" của check ủng hộ thành "thiếu" không bao giờ làm trạng thái tốt lên), **xếp hạng và hòa** (thứ tự trạng thái, tỷ lệ critical, số supporting, hòa chính xác 1/2 = 2/4, độc lập thứ tự đầu vào, không sửa đầu vào, id trùng), ablation, guard giả thuyết draft, chạy thư viện nháp thật end-to-end. Reviewer chạy thêm fuzz 60.000 ca và 8.000 bộ xếp hạng ngẫu nhiên: không vi phạm bất biến nào.

Lệnh: `.venv\Scripts\python -m pytest -q`; thử trên dữ liệu thật: `python scripts/run_verdicts.py`.

## 4. Review độc lập

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 0 | 5 | 6 |

Đã xử lý:
- **MEDIUM:** tỷ lệ critical tính cả check critical-contradict nên giả thuyết SUPPORTED có thể có tỷ lệ < 1 và xếp dưới giả thuyết không có check đó → nay tỷ lệ chỉ tính trên critical-support (SUPPORTED luôn bằng 1); FAIL của check critical-support luôn là CONTRADICTED → thành chính sách trong config, mặc định `if_none_pass`, kèm danh sách `failed` để báo cáo vẫn thấy kỳ vọng không xảy ra.
- **LOW:** check văn bản FOUND mà không có bằng chứng vẫn cho SUPPORTED → nay coi là unknown; `check_id` của kết quả/bằng chứng không đối chiếu với check → nay lỗi lớn tiếng; guard draft chặn cả giả thuyết khác loại/hướng → nay chỉ chặn giả thuyết được phân tích; script lỗi mã hóa trên Windows; kết quả chạy thử không được đánh dấu → nay `used_drafts`.
- **Ghi nhận, không đổi luật:** một nhãn `contradict` đơn lẻ kéo giả thuyết xuống CONTRADICTED; hạng hòa vs vị trí danh sách (nhắc Phase 12); check critical-contradict `not_satisfied` không tính là bằng chứng ủng hộ (giả thuyết chỉ có loại check này ra UNVERIFIED, bảo thủ).

## 5. Known limitations

- Chưa chạy được với giả thuyết `reviewed` (chưa có); chưa có số liệu đo (Hit@k, MRR, false-SUPPORTED, UNVERIFIED rate) — thuộc Phase 12.
- Trạng thái chỉ đúng chừng nào các check và ngưỡng đúng: chất lượng kết quả bị chi phối bởi `library_draft.yaml`, trong đó `checks` và `pass_when` là đề xuất của Claude.
- Chưa có luật cho giả thuyết không có check critical-support nào (không bao giờ SUPPORTED; tối đa PARTIAL).
- Một check văn bản chỉ cho phép một bằng chứng mỗi đoạn; không có cân nhắc độ mạnh của bằng chứng (số nguồn, độ dài trích dẫn).

## 6. Chưa làm vì ngoài scope

Viết báo cáo từ verdict (Phase 11); đo Hit@k/MRR/false-SUPPORTED, freeze, baseline A/B (Phase 12); chọn ca và nhãn chuẩn (nhóm, Phase 12a).

## 7. Hard-code công ty

Không.

## 8. AI_USAGE_LOG.md

Đã thêm mục "2026-10-24 — Phase 10".

## 9. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới; các lựa chọn ở mục đầu là diễn giải của bảng spec 12.1 (nên được nhóm xác nhận và ghi vào báo cáo cuối).
