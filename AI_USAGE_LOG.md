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

---

## 2026-10-23 — Phase 2 (Table/data extraction)

**Công cụ:** Claude Code (Claude Opus 5.5).

**Việc Claude đã làm:**
- Audit-first: `extraction/` chỉ có `__init__.py` rỗng, nên toàn bộ code đều tạo mới. Khảo sát các dạng ô trong fixture HPG (chỉ dùng làm ví dụ):
  - số VN, số âm trong ngoặc, `-`, `%`;
  - header nhiều tầng có rowspan/colspan;
  - cột `Mã số`/`Thuyết minh`;
  - ô OCR dính nhiều số;
  - header lặp giữa bảng;
  - hai bảng bị OCR gộp làm một.
- Báo kế hoạch trước khi code. Nhóm chốt dấu `-` → `NOT_APPLICABLE`, `value=None`.
- `contracts.py`: `ValueStatus`. `configs/extraction.yaml` cùng `extraction/settings.py`: từ khóa, đơn vị, cấu hình CSV, có validate.
- `extraction/numbers.py`: parse số VN/plain ra `Decimal`. Ô rỗng → `MISSING`; dấu gạch → `NOT_APPLICABLE`; còn lại → `UNRESOLVED` kèm lý do.
- `extraction/tables.py`: bảng → `RawTableValue`, gồm nhãn dòng, mã số, thuyết minh, STT, header, kỳ, đơn vị, trạng thái, cờ, provenance.
- `extraction/structured.py`: adapter CSV (wide/long), xử lý khóa trùng, provenance file/sha256/dòng/cột.
- Test: 196 test case mới, gồm test tích hợp HPG và bất biến "không ô có chữ số nào bị mất", kiểm theo vị trí ô.

**Lỗi/sai sót bắt được khi review:**
- Tự review khi chạy trên HPG (trước review độc lập):
  - Hai bảng bị OCR gộp làm các dòng của bảng sau nhận nhầm kỳ 31/12/2024. Đã thêm xử lý header giữa bảng.
  - Dòng tiêu đề trải hết bảng xóa kỳ/đơn vị.
  - Ô góc trái "Cho năm kết thúc … 2023" bị bỏ mất.
  - Cột STT bị bỏ, không lưu vào đâu.
  - "Giá trị hao mòn lũy kế" bị coi là số lũy kế theo kỳ.
  - Hằng `REASON_MISSING_TOKEN` làm test quét secret báo động giả. Đã đổi tên hằng, không nới lỏng test.
- Review độc lập vòng 1 (subagent): 2 CRITICAL, 4 HIGH, 7 MEDIUM, 7 LOW, đều có input tái hiện. Các lỗi chính:
  - Tiêu đề chứa "Thuyết minh" làm mọi cột thành `note`, mất hết giá trị.
  - Năm/ngày trong thân bảng bị coi là header, làm mất số và gán sai kỳ cho các dòng sau.
  - Ô góc trái áp kỳ cho cột "Năm trước".
  - Ngoại tệ, hoặc "Tr. VND", rơi về VND.
  - "Quý 3" không kèm năm bị bỏ qua.
  - CSV `UNRESOLVED` vẫn giữ `value`.
  - Nhóm nghìn có số 0 đầu (`0.500` → 500).
  - Test bất biến yếu (so chuỗi con).
  Đã sửa toàn bộ CRITICAL/HIGH/MEDIUM và phần lớn LOW.
- Review độc lập vòng 2: 1 CRITICAL, 3 HIGH (trong đó 1 hồi quy), 3 MEDIUM, 5 LOW. Các lỗi chính:
  - Năm trong thân bảng khi nhãn nằm ở cột 1 vẫn bị nuốt.
  - Dòng chữ "Đến năm 2030" đặt kỳ 2030 cho các dòng sau.
  - Header năm trần đứng sau dòng tiêu đề bị coi là số liệu (hồi quy do bản sửa vòng 1).
  - Cụm kỳ tương đối ngoài danh sách từ khóa.
  - Dòng đánh số "A | B | C | 1 | 2".
  Đã sửa và thêm test hồi quy. Trong lúc sửa còn tự bắt thêm hai lỗi trong chính config mới:
  - từ khóa "trước" khớp nhầm "chi phí trả trước";
  - "đến" trong `period_words` khiến "Đến năm 2030" bị coi là header sạch.
  LOW còn mở: số La Mã ở cột 0 khi không có header; "Năm nay" dưới tiêu đề có ngày ra `period_relative` (bảo thủ). Cả hai đều có cờ.

**Phần người cần tự xác minh:**
- **Spot-check số (spec mục 25.5):** mở trang PDF 40 của BCTC hợp nhất HPG 2024 gốc, bảng "10. Hàng tồn kho", dòng "Nguyên vật liệu", cột 31/12/2024 "Giá gốc". Extractor ra `20.324.528.389.776` → `Decimal('20324528389776')` VND, và dự phòng `(17.265.822.207)` → `-17265822207`. Xác nhận với bản PDF, không chỉ với file OCR.
- **Quy ước VN:** `1.751` được hiểu là 1751. `1,234` được hiểu là 1,234 và có cờ `ambiguous_separator`. Tài liệu dùng định dạng Anh phải chuyển đổi trước.
- **Ý nghĩa của `NOT_APPLICABLE`** (dấu `-`). Nhóm cần chốt quy tắc ở Phase 3: khi cộng tổng có được coi là 0 không, và ghi ở đâu.
- **Ý nghĩa các cờ:**
  - Cờ bảng: `no_header`, `subheader_in_body`, `column_numbering_row`.
  - Cờ kỳ: `period_missing`, `period_ambiguous`, `period_relative`, `period_cumulative`, `period_from_corner`, `period_incomplete`.
  - Cờ đơn vị: `unit_missing`, `unit_conflict`, `unit_unrecognized`.
  - Cờ khác: `after_subheader`, `label_from_rowspan`.
  - Phase 3 phải lọc theo cờ, không chỉ theo `status`.
- **Rà `configs/extraction.yaml`:** danh sách đơn vị, ngoại tệ, từ kỳ tương đối/lũy kế, `period_words`. Đây là quy ước BCTC VN do Claude soạn; nhóm nên bổ sung theo tài liệu thật của các công ty đã chọn.
- **Khi chốt nguồn dữ liệu có cấu trúc** (vnstock hoặc nguồn khác): khai tên cột, layout, `number_format`, `unit`, `ignore_columns` trong `structured`.
- **Hiểu vì sao trên HPG có 493 giá trị `period_missing`.** Đó là các bảng biến động "Số dư đầu năm/cuối năm", kỳ nằm ở nhãn dòng. Đây là việc của Phase 3.

**Cách nhóm đã kiểm tra (nhóm điền):**
-


---

## 2026-10-23 — Phase 3 (Financial metric normalization)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Việc Claude đã làm:**
- Audit-first: `normalization/` chỉ có `__init__.py` rỗng; Phase 0-2 còn nguyên (295 test pass). Khảo sát nhãn, mã số, cột, kỳ của các dòng tồn kho, doanh thu, giá vốn, lợi nhuận gộp, XDCB dở dang, capex trên fixture HPG (chỉ dùng làm ví dụ).
- `configs/normalization.yaml`: từ điển 9 metric, quy đổi đơn vị, cờ chặn, dung sai, metric dẫn xuất.
- `normalization/{settings,mapping,resolve,pipeline}.py`: khớp nhãn trọn, kiểm mã/cột/ngữ cảnh bảng, khóa kỳ, quy đổi VND, ưu tiên nguồn, mâu thuẫn thành `UNRESOLVED`, `gross_margin` do code tính.
- `contracts.py`: `FinancialFact`. `config.py`: thêm `normalization`.
- Test: 71 test case mới trong `tests/test_normalization.py`, gồm bất biến "mỗi giá trị khớp nhãn thành đúng một candidate hoặc rejection" và test tích hợp HPG.

**Lỗi/sai sót bắt được:**
- Khi tự chạy trên HPG: bảng thuyết minh tồn kho không được nhận vì `requires_table_labels` dùng nhãn "nguyên liệu, vật liệu" trong khi bảng ghi "Nguyên vật liệu". Đã đổi sang điều kiện chỉ cần dòng "Thành phẩm".
- Khi tự chạy test: header có ngày trần hoặc "Cho quý kết thúc…" bị nhận thành số cả năm; đã thêm `annual_keywords`.
- Review độc lập (subagent): 0 CRITICAL, 1 HIGH, 5 MEDIUM, 5 LOW, đều có input tái hiện:
  - HIGH: cờ `ragged_rows` không chặn, nên giá trị ở hàng thiếu ô có thể bị gán sang sai kỳ.
  - MEDIUM: header "…ngày 31 tháng 12 năm 2023" bị loại vì từ khóa "tháng"; `-` ở một nguồn còn nguồn khác có số không thành mâu thuẫn; `relative_tolerance` không có cận trên; config thiếu kiểm (chuỗi rỗng trong bộ lọc cột, cờ chặn gõ sai, `VND` khác 1, tử/mẫu dẫn xuất khác loại); capex dương từ CSV luôn `UNRESOLVED` do kiểm dấu theo quy ước báo cáo.
  - LOW: assertion luôn đúng trong test (`or True`), `cogs` thiếu kiểm dấu, `quarter_end_months` nhận tháng lạ. Hai mục còn mở được ghi trong báo cáo phase (CSV chỉ có năm luôn gán `Q4`; nhãn có chú thích cuối vẫn khớp).
  - Đã sửa HIGH, MEDIUM và 3 LOW, thêm test hồi quy cho từng mục.

**Phần người cần tự xác minh:**
- **Spot-check số (spec mục 25.5):** mở PDF BCTC hợp nhất HPG 2024 gốc, đối chiếu trang 40 (Nguyên vật liệu, cột 31/12/2024 "Giá gốc" = `20.324.528.389.776`), trang 7 (Hàng tồn kho mã 140 = `46.091.222.189.472`) và trang 11 (doanh thu thuần mã 10 = `138.855.112.131.387`, lợi nhuận gộp mã 20 = `18.497.549.127.684`). Tự bấm máy: 18.497.549.127.684 / 138.855.112.131.387 ≈ 0,1332.
- **Từ điển nhãn trong `configs/normalization.yaml` do Claude soạn** theo mẫu TT200: rà theo BCTC thật của các công ty đã chọn và bổ sung alias. Khi chốt nguồn CSV (vnstock hoặc nguồn khác), điền `structured_aliases` cho từng metric.
- **Nhóm cần quyết định các quy tắc đề tài:**
  - Số bị công ty trình bày lại ở báo cáo sau hiện thành `UNRESOLVED` (`ocr_conflict`); nhóm chọn cách xử lý.
  - `relative_tolerance` hiện bằng 0 (phải bằng nhau tuyệt đối).
  - Thành phần tồn kho lấy cột Giá gốc, còn `inventory_total` là số thuần sau dự phòng.
  - Công ty có năm tài chính lệch lịch: khóa kỳ hiện theo quý lịch.
- **Hiểu các cờ và lý do trong `rejections`:** `date_not_quarter_end`, `flow_duration_unknown`, `code_mismatch`, `column_mismatch`, `table_context_mismatch`, `ragged_rows`; và cờ trong fact: `source_conflict`, `ocr_conflict`, `not_applicable_vs_value`, `sign_flipped`, `sign_not_checked`, `unit_unknown`.
- **Hiểu vì sao `-` không thành 0:** `NOT_APPLICABLE` không có giá trị; Phase 4/5 phải quyết định có coi là 0 khi cộng tổng hay không, và ghi rõ.

**Cách nhóm đã kiểm tra (nhóm điền):**
-
