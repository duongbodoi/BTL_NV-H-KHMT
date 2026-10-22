# Báo cáo Phase 0 — Foundation

- **Ngày:** 2026-10-22
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 20 — Phase 0
- **Trạng thái:** phần việc của Claude đã xong; **chưa COMPLETED** cho tới khi nhóm review và commit (spec mục 25, 28.2)

---

## Phase này làm gì?

Phase 0 dựng **nền móng kỹ thuật** để các phase sau (parsing → anomaly → giả thuyết → kiểm chứng → báo cáo → đánh giá) có chỗ đặt code và có cách kiểm tra tự động. Phase này **chưa có tính năng nghiệp vụ nào**: chưa đọc BCTC, chưa phát hiện bất thường, chưa gọi LLM.

Cụ thể, Phase 0 bảo đảm:

1. **Project chạy được và kiểm tra được.** Cài đặt bằng một lệnh; `pytest` (test) và `ruff` (lint/format) chạy được ngay trên một bản checkout sạch. Từ đây mọi phase sau đều phải qua cổng test/lint (spec mục 24).
2. **Mọi tham số nằm trong config, không nằm trong code.** Ngưỡng, danh sách công ty, mốc thời gian, model LLM… đều đọc từ `configs/*.yaml` (spec mục 19). Phase 0 làm bộ đọc config báo lỗi rõ ràng khi config thiếu hoặc sai, thay vì âm thầm đoán giá trị (nguyên tắc "Không biết > đoán sai", spec mục 23).
3. **Không lộ secret.** API key chỉ đọc từ biến môi trường. Có test tự động quét repo để bắt key bị lỡ commit, và kiểm `.env`, dữ liệu thô nằm trong `.gitignore`.
4. **Có khung thư mục theo pipeline 4 bước.** Mỗi module tương lai (parsing, extraction, normalization, validation, anomaly, hypotheses, retrieval, verification, llm, report, eval, demo) đã có chỗ, để mỗi thành viên nắm một module (spec mục 21.3).
5. **Minh bạch AI ngay từ đầu.** Tạo `AI_USAGE_LOG.md` (ghi Claude làm gì, người cần xác minh gì) và `DEVIATIONS_FROM_MIDTERM.md` (ghi điểm khác báo cáo giữa kỳ). Hai file này phục vụ tiêu chí chấm "Phần dùng AI và tự làm" (spec mục 30).

Phase 0 **không** quyết định thay nhóm: danh sách công ty, ngưỡng, model, chỉ số thành công đều để trống/`null` vì thuộc phần "chỉ người làm" (spec mục 21.1).

---

## 1. Tóm tắt

- Audit-first: repo `mvp` chưa có commit và chưa có code, nên mọi hạng mục Phase 0 đều tạo mới.
- Theo yêu cầu của nhóm, bỏ qua code legacy bên ngoài repo, nên mục "giữ baseline legacy" không áp dụng.
- Đã dựng: project, test/lint, config loader, cấu trúc thư mục, `AI_USAGE_LOG.md`, `DEVIATIONS_FROM_MIDTERM.md`.

## 2. File tạo

- Gốc repo: `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, `AI_USAGE_LOG.md`, `DEVIATIONS_FROM_MIDTERM.md`
- `configs/`: `anomaly.yaml`, `retrieval.yaml`, `llm.yaml`, `eval.yaml`, `dataset.yaml`
- `src/steel_xai/`: `__init__.py`, `config.py`, cùng 12 subpackage rỗng (parsing … demo)
- `tests/`: `test_config.py`, `test_repo_hygiene.py`
- `.gitkeep` trong `data/raw`, `data/processed`, `data/eval`, `hypotheses/`, `docs/`, `tests/fixtures/`
- `docs/phase_reports/PHASE_0.md` (file này)

## 3. File sửa

Không có. `CLAUDE.md` và spec giữ nguyên.

## 4. Quyết định thiết kế

- Package đặt tên `steel_xai`, theo src layout.
- Config chia thành 5 file YAML. Loader báo `ConfigError` rõ ràng thay vì đoán giá trị.
- Thư mục config có thể ghi đè bằng biến `STEEL_XAI_CONFIG_DIR`.
- API key chỉ đọc từ biến môi trường (tên biến khai báo ở `configs/llm.yaml`, khóa `api_key_env`).
- Trong config chỉ ghi giá trị có nguồn trong spec: ngành thép, 2 loại bất thường xét hai chiều, giai đoạn 2021–2023, mốc 2024, BM25, 18 ca = 5 dev + 13 test, 3 lần/ca.
- Các giá trị thuộc mục 21.1 để trống hoặc `null`: danh sách công ty, ngưỡng, model, chỉ số thành công.

## 5. Tests

- **Config loader** (`tests/test_config.py`): load đủ 5 config; báo lỗi khi thiếu file / YAML hỏng / gốc không phải mapping / tên file không hợp lệ; ghi đè bằng biến môi trường; đọc UTF-8; lấy API key.
- **Vệ sinh repo** (`tests/test_repo_hygiene.py`): import được 12 subpackage; quét secret trên các file sẽ commit (có test riêng cho trường hợp phải bắt được và trường hợp không được báo nhầm); `.env` và dữ liệu thô bị gitignore, `.env.example` thì không.

## 6. Commands đã chạy

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

- Hai lần mô phỏng checkout sạch trong thư mục tạm: chỉ copy các file git sẽ track, rồi chạy cả hai cách là chỉ cài dependency và cài `-e`.
- Thử tạo một file `.env` chứa key giả để xác nhận test không báo nhầm, sau đó đã xóa file này.

## 7. Kết quả

| Môi trường | Passed | Skipped | Failed |
|---|---|---|---|
| Repo | 46 | 0 | 0 |
| Checkout sạch (không có `.git`) | 41 | 5 | 0 |

- 5 test bị skip ở checkout sạch vì các test kiểm gitignore cần git.
- `ruff check` và `ruff format --check`: sạch.
- Trong quá trình làm có một lần fail: test quét secret bắt trúng chuỗi mẫu `password: ...` nằm trong chính file test. Đã sửa bằng cách nối chuỗi, không nới lỏng assertion.

## 8. Known limitations

- Đường dẫn mặc định tới `configs/` chỉ đúng khi cài editable. Nếu cài cách khác thì phải đặt `STEEL_XAI_CONFIG_DIR`.
- Quét secret chỉ dựa trên regex, nên không bảo đảm bắt hết mọi loại key.

## 9. Chưa làm vì ngoài scope

- Mọi feature từ Phase 1 trở đi.
- `configs/eval_freeze.json` (thuộc Phase 12a).
- Nội dung thư viện giả thuyết, danh sách công ty, ngưỡng, ca đánh giá (spec mục 21.1).

## 10. Hard-code công ty

Không có. Code không chứa mã công ty hay năm; năm chỉ nằm trong `configs/dataset.yaml`.

## 11. AI_USAGE_LOG.md

- Đã thêm mục "2026-10-22 — Phase 0", gồm việc đã làm và các lỗi review đã sửa.
- Review độc lập bằng subagent: không có lỗi CRITICAL. Đã sửa 2 lỗi HIGH, 4 lỗi MEDIUM và các lỗi LOW nhỏ.

**Phần người cần tự xác minh:**

- Tên khóa `inventory_increase` (xét hai chiều), `gross_margin_change` và tên các ablation `no_rag`, `no_verification`, `no_library`.
- Biến `ANTHROPIC_API_KEY` mới là giá trị tạm; cần chốt provider và model trong `configs/llm.yaml`.
- Hiểu cách `load_config` báo lỗi và cách `STEEL_XAI_CONFIG_DIR` ghi đè thư mục config.
- Tự chạy lại pytest và ruff trên máy mình.
- Phase 0 chưa có số liệu BCTC nên chưa có gì để spot-check (spec mục 25.5).

## 12. Khác báo cáo giữa kỳ

Không phát sinh điểm mới. `DEVIATIONS_FROM_MIDTERM.md` hiện chỉ chứa 4 điểm đã chốt trong spec mục 26.2.

---

## Việc còn lại của nhóm

- Review thay đổi rồi commit (spec mục 25, bước 6). Sau đó Phase 0 mới đủ điều kiện **COMPLETED**.
- Thư mục `.venv/` đã nằm trong `.gitignore`, không cần commit.
