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

---

## 2026-10-22 — Phase 1 (Document parsing)

**Công cụ:** Claude Code (Claude Opus 5.5).

**Việc Claude đã làm:**
- Audit-first: `parsing/` rỗng, chưa có code Phase 1, nên toàn bộ đều tạo mới. Inspect fixture HPG để biết các dạng đầu vào (chỉ dùng làm ví dụ):
  - marker `===== PAGE N =====`;
  - bảng HTML trên một dòng có `rowspan`/`colspan`, entity, LaTeX;
  - số trang in lệch hoặc không có;
  - rác OCR từ con dấu/chữ ký;
  - header lặp có lỗi OCR.
- `src/steel_xai/contracts.py`: `Provenance` (spec mục 18).
- `src/steel_xai/parsing/document.py`: `Document/Page/Block(text|table)/TableCell`, `parse_text`, `parse_file`, `to_dict/from_dict`.
  - Trang chỉ lấy từ marker (số trang PDF).
  - Block id deterministic `<doc_id>:pNNNN:bNNN`; giữ số dòng nguồn và `content_sha256`.
  - Văn bản giữ nguyên, không sửa OCR, không bỏ dòng nào.
  - Bảng chỉ tách cấu trúc ô; chưa parse số (Phase 2).
  - Marker thiếu/trùng/giảm/méo → `ParseError`. Bảng hỏng → giữ nguyên văn và gắn `flags`.
- Test: `tests/test_parsing_document.py` và fixture tổng hợp `tests/fixtures/synthetic_ocr_document.txt` (công ty giả định). Có thêm một test tích hợp trên fixture HPG, với kỳ vọng tính từ chính file; test này tự skip nếu thiếu file.
- README: thêm ví dụ dùng parser.

**Lỗi/sai sót bắt được khi review** (review độc lập bằng subagent, không có CRITICAL):
- HIGH, đã sửa: marker gần đúng bị gộp im lặng vào trang trước, làm trích dẫn sai trang. Các dạng gặp lỗi: `\f===== PAGE 2 =====`, `===== PAGE 2 ===== (tiếp)`, `PAGE 2 / 80`. Cách sửa:
  - chấp nhận `\f`/`\v` quanh marker;
  - dòng giống marker mà sai định dạng → `ParseError`;
  - chỉ nhận chữ số ASCII.
- MEDIUM, đã sửa:
  - `</table>` nằm trong comment HTML cắt bảng sai: giờ che comment/script/style trước khi dò bảng và gắn cờ `unexpected_html`;
  - `<p>1.000</p><p>2.000</p>` trong ô bị dính thành `1.0002.000`: thẻ khối giờ chèn khoảng trắng, thẻ lạ thì gắn cờ.
- LOW, đã sửa:
  - `from_dict` nhận JSON có trang/doc_id/block_id không khớp: giờ báo `ParseError`;
  - đổi `sha256` → `content_sha256`, ghi rõ đây là hash của văn bản đã chuẩn hóa;
  - test tách dòng theo `\n` giống parser;
  - siết test phủ nội dung: so từng ký tự không trắng của mỗi trang và kiểm block không chồng nhau.
- Tự review: tự sửa một test viết sai (encode cp1258) và một test vô nghĩa (tautology) trên fixture HPG.

**Phần người cần tự xác minh:**
- Mở `tests/fixtures/HPG_financial_statements_2024_consolidated_extracted.txt`, chọn một block bất kỳ (ví dụ `...:p0040:b008`, tiêu đề "10. Hàng tồn kho") và đối chiếu với trang 40 của **file PDF gốc**: số in trên trang là 39, nhưng trích dẫn phải ghi trang PDF 40.
- Quy ước nhập liệu: mỗi trang phải có marker `===== PAGE N =====` với N là số trang PDF. Nếu công cụ OCR của nhóm xuất định dạng khác thì phải chuyển đổi trước, vì parser sẽ báo lỗi chứ không đoán.
- Hiểu các cờ `unclosed_table`, `nested_table`, `empty_table`, `invalid_span`, `text_outside_cell`, `unexpected_html`, `stray_table_tag` và vì sao block có cờ vẫn được giữ.
- Fixture HPG là BCTC năm 2024, chỉ dùng để test parser. Theo spec mục 3.3 và 7.2, **không được** đưa file này vào kho RAG ở Phase 7.
- Nhóm cần quyết định có commit fixture HPG hay không (bản quyền/dung lượng). Nếu không commit, test tích hợp sẽ tự skip.
- Phase 1 chưa trích số nào từ BCTC. Spot-check số (spec mục 25.5) bắt đầu từ Phase 2; ở phase này nên spot-check trang/vị trí như mục đầu tiên.

**Cách nhóm đã kiểm tra (nhóm điền):**
-
