# AI Usage Log

Mỗi phase Claude thêm một mục (spec mục 26.1): ngày, phase, việc Claude đã làm, lỗi/sai sót bắt được khi review, **phần người cần tự xác minh**. Nhóm bổ sung cách kiểm tra đã thực hiện.

---

## 2026-10-22 — Phase 0 (Foundation)

**Công cụ:** Claude Code (Claude Opus 5.5).

**Việc Claude đã làm:**
- Audit-first: repo chưa có commit, chưa có code. Theo yêu cầu của người dùng, chỉ làm trong repo này và không dùng code legacy bên ngoài, nên mục "giữ baseline legacy" không áp dụng.
- Tạo `pyproject.toml` (package `steel_xai`, src layout, dep `pyyaml`, dev `pytest` + `ruff`), `.gitignore`, `.env.example` (không có giá trị), `README.md`.
- Tạo `configs/{anomaly,retrieval,llm,eval,dataset}.yaml` dạng khung:
  - chỉ ghi các giá trị có nguồn trong spec: ngành thép, 2 loại bất thường hai chiều, giai đoạn 2021–2023, mốc 2024, BM25, 18 ca = 5 dev + 13 test, 3 lần/ca;
  - danh sách công ty, ngưỡng, model và chỉ số thành công để trống/`null` cho nhóm chốt.
- `src/steel_xai/config.py`:
  - `load_config`, `load_all_configs` báo `ConfigError` khi thiếu file, YAML hỏng, gốc không phải mapping hoặc tên file không hợp lệ;
  - ghi đè thư mục config bằng `STEEL_XAI_CONFIG_DIR`;
  - `get_api_key` chỉ đọc từ biến môi trường.
- 12 subpackage rỗng theo pipeline; các thư mục `data/`, `hypotheses/`, `docs/`, `tests/fixtures/` (chỉ có `.gitkeep`).
- Test:
  - `tests/test_config.py`: config loader;
  - `tests/test_repo_hygiene.py`: import subpackage, quét secret trên các file sẽ commit, kiểm gitignore.
- `DEVIATIONS_FROM_MIDTERM.md`: chép 4 điểm đã chốt từ spec mục 26.2.
- Không tạo nội dung thuộc spec mục 21.1. Chưa tạo `eval_freeze.json` (thuộc Phase 12a).

**Lỗi/sai sót bắt được khi review** (review độc lập bằng subagent, không có CRITICAL):
- HIGH, đã sửa:
  - test quét secret quét cả file bị gitignore, nên `.env` cục bộ hợp lệ làm test fail. Đã chuyển sang quét `git ls-files --cached --others --exclude-standard`;
  - regex secret bỏ sót `ANTHROPIC_API_KEY=...`, `sk-proj-...` và `password: ...`. Đã sửa pattern và thêm test cho từng trường hợp.
- MEDIUM, đã sửa:
  - pytest không import được package khi chưa cài: thêm `pythonpath = ["src"]`;
  - `PROJECT_ROOT` sai khi cài non-editable: thêm override qua biến môi trường và ghi chú trong README;
  - `anomaly.yaml` đặt tên khóa ngưỡng trước Phase 5: rút về `thresholds: {}`;
  - `llm.yaml` ngầm chọn provider: ghi chú là giá trị tạm, bỏ test khóa cứng tên biến.
- LOW, đã sửa:
  - kiểm tra tên config (chống `../`);
  - dùng `is None` thay cho `or`;
  - bổ sung `.gitignore`;
  - test gitignore dùng `git check-ignore`;
  - thêm chú thích cho tên ablation/anomaly.
- Khi chạy lại, test quét secret bắt trúng chuỗi mẫu `password: ...` trong chính file test, chứng tỏ pattern hoạt động. Đã nối chuỗi để tránh tự bắt.

**Phần người cần tự xác minh:**
- Tên các khóa trong config: `inventory_increase` (xét hai chiều), `gross_margin_change`, tên ablation `no_rag/no_verification/no_library`.
- Biến môi trường API key (`ANTHROPIC_API_KEY`) chỉ là giá trị tạm. Cần chốt provider/model trong `configs/llm.yaml`.
- Hiểu cách `load_config` báo lỗi và cách `STEEL_XAI_CONFIG_DIR` ghi đè thư mục config.
- Chạy lại trên máy mình: `python -m pip install -e ".[dev]"`, `python -m pytest`, `python -m ruff check .`.
- Phase 0 không có phép tính hay số trích từ BCTC, nên chưa có gì để spot-check số (spec mục 25.5).

**Cách nhóm đã kiểm tra (nhóm điền):**
-
