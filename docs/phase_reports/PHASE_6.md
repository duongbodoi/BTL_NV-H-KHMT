# Báo cáo Phase 6 — Hypothesis library

- **Ngày:** 2026-10-24
- **Thực hiện:** Claude Code (Claude Sonnet 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 9, 10.1, 18, 20 (Phase 6), 23
- **Trạng thái:** phần việc của Claude (schema, loader, validator) đã xong, qua 1 vòng review độc lập, lỗi đã sửa. **Chưa COMPLETED**: mục tiêu phase gồm thư viện 6–8 giả thuyết mỗi loại mà **nội dung do nhóm soạn** (spec mục 9); hiện thư viện là 15 bản nháp `draft` của nhóm (mục 11), chưa giả thuyết nào `reviewed`. **Cập nhật 2026-10-25:** nhóm duyệt cả 22 giả thuyết (14 tồn kho, 8 biên gộp), `reviewed_by: Nguyễn Tùng Dương`, nguồn ghi đầy đủ tên/ngày/link; thư viện qua validator. Chưa đánh dấu COMPLETED (chờ nhóm quyết).

## ⚠ Việc của nhóm

Soạn lại/sửa và duyệt 6–8 giả thuyết cho mỗi loại (đã có bản nháp, xem mục 11) (`inventory_increase`, `gross_margin_change`) trong `hypotheses/<id>.yaml`, từ báo cáo ngành, tài liệu chuyên môn, trao đổi với người am hiểu. Mỗi giả thuyết điền `sources`, `checks`, rồi đặt `status: reviewed` và `reviewed_by` (họ tên người duyệt). `configs/hypotheses.yaml: min_reviewed_per_type: 6`; `Library.coverage()` báo loại nào còn thiếu bao nhiêu.

## Phase này để làm gì?

Bước 2 của pipeline (spec mục 6): dựng khung để thư viện giả thuyết nghiệp vụ ngành thép được nạp và kiểm tra bằng code, để luật kiểm chứng nằm trong dữ liệu (check có cấu trúc, mục 10.1) chứ không nằm trong code.

## 1. Đã làm được gì

| Thành phần | Hành vi |
|---|---|
| `contracts.Check`, `contracts.Hypothesis` (mục 18) | 9 trường của báo cáo + `sources`, `status`, `reviewed_by`, `checks`; `to_dict`/`from_dict`. `from_dict` **không tự kiểm**; phải qua `validate_hypothesis`. |
| `configs/hypotheses.yaml` | Thư mục thư viện, số giả thuyết tối thiểu mỗi loại, bộ lọc `reviewed_by`, độ dài tối thiểu của nguồn, `known_tools` (để trống tới Phase 8). |
| `hypotheses/library.py` | `validate_hypothesis` (trả danh sách lỗi), `load_hypothesis_file`, `load_library` → `Library` (`reviewed`, `drafts`, `for_type`, `coverage`). |
| Mọi giả thuyết (kể cả draft) | Đủ 9 trường; kiểu đúng; không trường lạ (bắt gõ nhầm tên); `anomaly_type` thuộc `anomaly.yaml`; check có cấu trúc đúng loại (`quant`: tool + params + pass_when; `text`: queries); id check duy nhất; `reviewed_by` nếu có không được là tên AI; khóa YAML trùng bị từ chối. |
| Thêm cho `reviewed` | claim/rationale không rỗng; có bằng chứng kỳ vọng; có phản bác và phần thiếu quan trọng; có `sources` (mỗi mục ≥ 8 ký tự); `reviewed_by` là họ tên người; ≥ 1 check `critical` polarity `support`; ≥ 1 check phản bác; nội dung không nhắc mã công ty. |
| Loader | Tên file = `<id>.yaml`; file `.yml`/`.YAML`/`.json` trong thư mục thư viện là lỗi (không bỏ sót im lặng); id lặp lỗi; một file sai làm hỏng cả lần tải. **Chỉ `reviewed` vào `Library.reviewed`**; `draft` nằm riêng. |

## 2. File

- **Tạo:** `src/steel_xai/hypotheses/library.py`, `configs/hypotheses.yaml`, `hypotheses/README.md`, `tests/test_hypotheses.py`, `docs/phase_reports/PHASE_6.md`.
- **Sửa:** `contracts.py` (`Check`, `Hypothesis`), `config.py` (`hypotheses` vào `CONFIG_NAMES`), `hypotheses/__init__.py`.

## 3. Quyết định thiết kế (nhóm nên đọc)

- **Check critical phải có ít nhất một check `support`**: nếu mọi check critical đều là phản bác thì giả thuyết không thể SUPPORTED (spec mục 10, "mọi check critical đạt").
- **`pass_when` là mapping tự do** (không null); schema điều kiện (`op`, `value`...) sẽ chốt ở Phase 8 cùng registry tool, lúc đó điền `known_tools`.
- **Mã công ty** (từ `dataset.companies`) không được xuất hiện trong nội dung giả thuyết reviewed (trừ `sources`, nơi được nêu tên nguồn). Tên công ty đầy đủ không được kiểm.
- **Bộ lọc "người"**: chuẩn hóa NFKC, bỏ ký tự ẩn, chặn chữ Cyrillic/Hy Lạp, tên mô hình/AI, giá trị giữ chỗ, yêu cầu ≥ 2 từ. Đây là lưới lọc, **không chứng minh** người đã duyệt; kiểm soát cuối là người duyệt/commit.

## 4. Tests

`tests/test_hypotheses.py` (~75 ca): chấp nhận bản hợp lệ; thiếu từng trường trong 9 trường; từng điều kiện `reviewed`; cấu trúc check; reviewer giả mạo/ẩn; khóa YAML trùng; file sai đuôi; id/tên file; chỉ reviewed vào pipeline; một file hỏng làm hỏng cả lần tải; roundtrip; thư viện repo không nhắc công ty. Toàn repo **641 passed**, `ruff check` và `ruff format --check` sạch.

## 5. Review độc lập

| CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|
| 0 | 3 | 4 | 4 |

Đã sửa: HIGH (file `.yml` bị bỏ sót im lặng; khóa YAML trùng che mất giá trị trước, ví dụ `reviewed_by` bị ghi đè; bộ lọc "người" qua mặt được bằng `ClaudeBot`, ký tự zero-width, chữ Cyrillic, `GPT4`, `TBD`...); MEDIUM (giá trị rác `0`/`False` trong check; chỉ có check critical phản bác; không chặn mã công ty; `sources` kiểu `"x"`); LOW (draft mang `reviewed_by: Claude`; tên file không khớp id).
Chưa sửa (ghi nhận): `Hypothesis.from_dict` không tự validate và `params`/`pass_when` là dict mutable trong dataclass frozen — mọi đường vào pipeline phải qua loader.

## 6. Known limitations

- Nội dung thư viện mới là bản nháp chưa duyệt; `Library.reviewed` đang rỗng nên các phase sau chưa có giả thuyết được phép dùng.
- Không kiểm `tool` có tồn tại (chờ registry Phase 8) và không kiểm schema `pass_when`.
- Không kiểm chất lượng nội dung (nguồn có thật, giả thuyết có hợp lý) — việc của người duyệt.
- Mỗi giả thuyết một file; chưa hỗ trợ nhiều giả thuyết trong một file.

## 7. Chưa làm vì ngoài scope

Nội dung thư viện (nhóm); registry tool và thực thi check (Phase 8); truy xuất RAG từ `queries` (Phase 7); xếp hạng (Phase 10).

## 8. Hard-code công ty

Không. Mã công ty đọc từ `configs/dataset.yaml` để *chặn* chúng trong nội dung giả thuyết; không có tên công ty trong `src/`.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-24 — Phase 6".

## 10. Khác báo cáo giữa kỳ

Không thêm điểm lệch mới (thư viện do nhóm soạn).

## 11. Thư viện nháp của nhóm (2026-10-24)

Claude từng sinh 15 bản nháp theo yêu cầu, sau đó nhóm đưa `library_draft.yaml` (15 giả thuyết: INV-1..7, GM-1..8, có nguồn S0–S16) và yêu cầu bỏ bản của Claude. Đã **xóa toàn bộ bản Claude sinh** và dùng file của nhóm (chuyển vào `hypotheses/library_draft.yaml`), nên **không còn lệch spec mục 9**; dòng NHÁP tương ứng trong `DEVIATIONS_FROM_MIDTERM.md` đã gỡ.

Để nạp được file của nhóm, loader được mở rộng: file dạng `hypotheses: [...]`; trường phụ `code`, `testable_with_annual_data` (YAML `no`/`yes` đọc thành bool nên được chuẩn hóa), `source_strength`, `extension_beyond_midterm`; mã nguồn `S<số>` phải có trong `HYPOTHESIS_LIBRARY_DRAFT.md` khi `reviewed`. Claude **không sửa nội dung** file của nhóm.

Claude đã **đề xuất `checks`** cho cả 15 giả thuyết (chèn vào file, không đổi nội dung nhóm viết; có kiểm tự động rằng các trường gốc không đổi). Ngưỡng `pass_when`, danh sách chuỗi giá và mức `critical` là phỏng đoán cần nhóm rà; tên `tool` chốt ở Phase 8; check dùng metric chưa trích sẽ `NOT_EVALUABLE`.

Còn thiếu để có giả thuyết `reviewed` (nhóm làm): rà `checks`, `critical_missing_evidence` cho INV-7 và GM-7, file danh mục nguồn `HYPOTHESIS_LIBRARY_DRAFT.md` (chưa có trong repo), rồi `reviewed_by`.

## 12. Cập nhật 2026-10-24 (thư viện mới của nhóm)

Nhóm thay `library_draft.yaml` bằng bản mới (22 giả thuyết: 15 cũ + DEC-1..7 cho tồn kho giảm, trường `direction`). Claude đã gộp: giữ nguyên nội dung nhóm viết, giữ `checks` đã có của 15 giả thuyết cũ, soạn `checks` đề xuất cho DEC-1..7. Schema nhận thêm `direction` (`up|down|both`, bắt buộc khi `reviewed`). Xem `docs/phase_reports/PHASE_8.md` mục 3b.
