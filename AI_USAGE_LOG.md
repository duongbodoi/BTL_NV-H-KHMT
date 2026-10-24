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


---

## 2026-10-23 — Phase 3 (cập nhật theo dữ liệu năm) và Phase 4 (Validation)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Bối cảnh:** nhóm đã commit Phase 3 bản đầu (`4d2b8fa`, khóa kỳ theo quý) và chốt: dữ liệu là BCTC năm OCR ViFinQA, công ty HPG/HSG/NKG (HSG kết thúc năm tài chính 30/9), `-` khi cộng tổng coi là 0 kèm cờ `nil_as_zero`, dung sai 0,1%, mẫu đối chiếu N = 20, số điều chỉnh ở năm sau lưu riêng và gắn `restated_later`. Việc 8 và 9 của danh sách (bộ ca, thư viện giả thuyết) do nhóm làm, Claude không làm.

**Việc Claude đã làm:**
- Khôi phục 4 fixture về working tree (đã `git restore`; hiện là file chưa theo dõi, nhóm `git add` khi commit).
- Phase 3: đổi khóa kỳ sang `FY<năm>` + `fiscal_period_end`; `normalization/context.py` (ngày kết thúc năm tài chính và đơn vị đọc từ văn bản tài liệu); giải header tương đối "Năm nay / Số đầu năm"; tách nhãn dùng chung 140/141 bằng mã số; thêm metric `inventory_gross`, `inventory_provision`; giá vốn lưu độ lớn dương; số so sánh lưu riêng và cờ `restated_later` / `comparative_only`; `FinancialFact.fiscal_period_end`.
- Phase 2 (sửa nhỏ): ô header hai tầng "Tại ngày 31 tháng 12 năm" được nhận là header (NKG 2023 trước đó mất toàn bộ header).
- Phase 4: `configs/validation.yaml`; `validation/{settings,checks,reconciliation}.py` (quan hệ kế toán, `nil_as_zero`, năm tài chính, bốc mẫu đối chiếu, báo cáo đối chiếu).
- Config: `dataset.yaml` (HPG/HSG/NKG, annual, N = 20), `anomaly.yaml` (`compare_order: [yoy]`).
- Bản nháp thay đổi spec mục 3.3, 7.1, 8.3 và `DEVIATIONS_FROM_MIDTERM.md`: `docs/DRAFT_SPEC_CHANGES.md`. **Chưa sửa spec và DEVIATIONS.**
- Test: 448 passed (đầu phase: 295 + 71 của bản Phase 3 đầu); fixture test cho NKG 2019, HSG 2017, NKG 2023, HPG 2022.

**Lỗi/sai sót bắt được:**
- Khi chạy 4 fixture mới: NKG 2023 không ra fact nào (header hai tầng bị coi là dữ liệu); HSG 2017 không ra fact nào (header "Năm nay/Năm trước" và bảng không ghi đơn vị). Là lỗ hổng của Phase 2-3 mà fixture HPG không lộ ra.
- Chọn ngày kết thúc năm tài chính: thử "kết thúc ngày" cho NKG ra năm trước; đổi sang ngày cuối tháng xuất hiện nhiều nhất (đúng ở cả 5 fixture).
- Review độc lập vòng 2 (subagent): 0 CRITICAL, 2 HIGH, 6 MEDIUM, 8 LOW, đều có input tái hiện:
  - HIGH: đơn vị lạ ("Đơn vị: 1.000.000 đồng") bị đơn vị văn bản (VND) ghi đè, sai độ lớn nhưng vẫn `OK`; ngày ký báo cáo ngang số phiếu với ngày khóa sổ có thể lệch cả năm tài chính.
  - MEDIUM: `restated_later` bỏ sót khi một bên là `-`; quan hệ kế toán vẫn PASS khi đầu vào khác ngày khóa sổ; năm nhuận báo thay đổi ngày khóa sổ; đối chiếu đọc sai dấu số trong ngoặc, `1,000` và phần trăm; dòng tham chiếu trùng làm tăng cỡ mẫu; config gõ sai tên metric không bị bắt.
  - LOW đã sửa: chặn trên bỏ sót khi thiếu một thành phần; `non_negative` vô hiệu cho giá vốn/capex; fact `UNRESOLVED` có tham chiếu làm thấp tỷ lệ lệch; NFC; cờ nguồn không truyền sang số dẫn xuất; test tautology.
  - LOW còn mở: khóa fact chưa phân biệt báo cáo hợp nhất/riêng lẻ; `fiscal_end_consistent` tính theo số fact; đẳng thức `0 = 0` cả ba vế `-`.

**Phần người cần tự xác minh:**
- **Spot-check số** trên file txt HPG 2024 (đã chốt hoãn PDF gốc): tồn kho thuần `46.091.222.189.472` (trang 7), doanh thu thuần `138.855.112.131.387` (trang 11), nguyên vật liệu `20.324.528.389.776` và thành phẩm `12.155.686.194.056` (trang 40). Thêm: HSG 2017, giá vốn `21.730.791.206.018` (báo cáo ghi trong ngoặc) và kỳ kết thúc `2017-09-30`; NKG 2023, tồn kho thuần `5.718.699.385.156`.
- **Điền mẫu đối chiếu 20 chỉ tiêu** từ BCTC gốc: chạy `draw_sample` (seed trong `configs/validation.yaml`), `write_sample_template`, điền `reference_value` (theo VND, định dạng VN), rồi `reconciliation_report`. Tỷ lệ lệch là số liệu cho `docs/DATA_CARD.md`; hiện chưa có.
- **Duyệt `docs/DRAFT_SPEC_CHANGES.md`**, đối chiếu với báo cáo giữa kỳ thật (giữa kỳ viết quý hay năm?), rồi chép vào spec và `DEVIATIONS_FROM_MIDTERM.md`.
- **Hiểu các quyết định:** `-` coi là 0 chỉ khi cộng tổng (có cờ `nil_as_zero`), ở fact vẫn là `NOT_APPLICABLE`; dung sai 0,1% áp cho cả ba quan hệ; thành phần tồn kho chỉ kiểm chặn trên (chưa liệt kê hết); HSG so với HPG/NKG cùng `FY` nhưng khác thời điểm khóa sổ → cờ `peer_fiscal_end_differs`.
- **Quyết định còn treo:** báo cáo hợp nhất và riêng lẻ (thư mục ViFinQA có cả hai): chỉ nạp `consolidated` hay thêm chiều vào khóa; quy tắc cho số trình bày lại ngoài cờ.
- Kiểm tra `fiscal_period_end` của từng tài liệu thật (suy từ văn bản bằng heuristic).

**Cách nhóm đã kiểm tra (nhóm điền):**
-


---

## 2026-10-23 — Phase 5 (Anomaly engine)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Bối cảnh:** nhóm yêu cầu làm tiếp Phase 5 khi Phase 3-4 chưa được đánh dấu COMPLETED (chưa spot-check PDF, chưa điền mẫu đối chiếu). Claude đã nêu điều này trong báo cáo. Ngưỡng và chọn ca là việc của người (spec mục 21.1).

**Việc Claude đã làm:**
- Audit-first: `anomaly/` chỉ có `__init__.py` rỗng; `anomaly.yaml` chỉ có khung (`thresholds: {}`); Phase 0-4 đã commit (`9582154`). Chạy toàn bộ 29 tài liệu `consolidated` của ViFinQA qua Phase 2-4 (trong thư mục scratchpad, không commit) để xem dữ liệu thật trước khi thiết kế.
- `contracts.Anomaly`; `anomaly/{settings,detect}.py`; `configs/anomaly.yaml` viết lại; `tests/test_anomaly.py`, `tests/test_anomaly_review.py`.
- Sửa Phase 3 `mapping.py` + `normalization.yaml` (header hai tầng có chữ "năm" ở ô cha) sau khi dữ liệu thật cho thấy HSG 2019–2023 mất toàn bộ chỉ tiêu kết quả kinh doanh.

**Lỗi/sai sót bắt được:**
- Khi chạy dữ liệu thật: HSG 2019–2023 không có doanh thu/giá vốn/lợi nhuận gộp (header "Năm kết thúc ngày" + "30/9/2022"). Đã sửa; sau đó chạy lại: đủ dữ liệu trừ NKG FY2019.
- Khi viết test: nhiều lỗi dữ liệu tổng hợp của chính test (giá vốn = 1 làm số ngày tồn kho phình ra; doanh thu không đổi làm luật tồn kho − doanh thu cùng bị vượt); sửa test, không sửa ngưỡng.
- Review độc lập (subagent): 0 CRITICAL, 1 HIGH, 5 MEDIUM, 5 LOW, đều có input tái hiện:
  - HIGH: fact trùng khóa (cùng công ty/năm/metric) bị ghi đè theo thứ tự đầu vào → kết quả phụ thuộc thứ tự.
  - MEDIUM: kỳ có dữ liệu hỏng hoàn toàn biến mất khỏi mẫu số; peer có năm tài chính kết thúc muộn hơn vẫn được dùng (dùng số "sau" mốc khóa sổ); cờ `peer_fiscal_end_differs` bỏ sót khi thiếu ngày và gắn thừa khi công ty không đủ tư cách peer; cờ chất lượng của peer và nền lịch sử không truyền; bản sửa `_fiscal` của Phase 3 quá rộng (chỉ cần chữ "năm" trong tiêu đề bảng là coi báo cáo 6 tháng là cả năm).
  - LOW: `anomaly_types` lặp nhân đôi bản ghi; cờ `days_in_period_missing` gắn sai lý do; đơn vị không được kiểm; `asof_dates` không validate; test yếu (một test lấy nhầm evaluation).
  - Đã sửa toàn bộ, kèm test hồi quy.

**Phần người cần tự xác minh:**
- **Ngưỡng:** đọc `configs/anomaly.yaml`. Mọi con số là đề xuất khởi điểm của Claude, **không phải quyết định của nhóm**; tỷ lệ bất thường hiện 56–67% (quá cao). Chốt ngưỡng và chọn luật giữ lại trên 5 ca dev, rồi đặt `provisional_thresholds: false`.
- **Hướng bất thường** (mục 3 của `PHASE_5.md`): hướng là hướng của độ lệch theo luật vượt ngưỡng, có thể khác dấu biến động thô; quyết định có giữ không.
- **Khai `asof_dates`** (ngày công bố BCTC theo công ty và năm) trong `anomaly.yaml`; Phase 7 cần.
- **Spot-check tay một chỉ báo:** ví dụ HPG FY2022 biên gộp 11,85% so 27,46% năm 2021, thay đổi ≈ -15,61 điểm % (số này lấy từ fixture HPG 2022; kiểm với BCTC gốc).
- **Danh sách ứng viên:** chạy `candidate_rows` / `write_candidates_csv` để chọn ca; Claude không chọn ca.
- Hiểu `peer_excluded_later_fiscal_end`: HSG (30/9) không dùng được peer 31/12 cùng nhãn năm, vì số của họ chưa có khi HSG khóa sổ.

**Cách nhóm đã kiểm tra (nhóm điền):**
-

**Cập nhật 2026-10-24 (đóng Phase 5):**
- Claude nâng ngưỡng `anomaly.yaml` (tỷ lệ gắn cờ ~70% → tồn kho 30%, biên gộp 20%) và ghim ngưỡng test vào `tests/fixtures/anomaly_config/`. Ngưỡng vẫn là đề xuất, nhóm chưa chốt.
- Review độc lập vòng 2 (subagent) trên phần thêm sau bản đầu: 2 HIGH (số so sánh từ BCTC năm sau; peer công bố sau as-of), 1 MEDIUM (ca chỉ do luật peer), 1 LOW (trích ngày ký, chưa sửa, fail-safe). Đã sửa các lỗi HIGH/MEDIUM kèm 4 test hồi quy.
- Nhóm không có ngày công bố, chỉ có năm: dùng tạm ngày ký báo cáo kiểm toán trích tự động.
- Người cần tự xác minh: chốt ngưỡng trên tập dev; duyệt các mục NHÁP ở `DEVIATIONS_FROM_MIDTERM.md`; chọn 18 ca từ `anomaly_candidates.csv` (Claude không chọn ca).

## 2026-10-24 — Phase 6 (Hypothesis library)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Bối cảnh:** nhóm yêu cầu bắt đầu Phase 6 sau khi Phase 5 đánh dấu COMPLETED. Theo spec mục 9, nội dung thư viện giả thuyết do nhóm soạn; Claude chỉ dựng schema, loader, validator và bản nháp placeholder.

**Việc Claude đã làm:**
- Audit-first: `hypotheses/` chỉ có `__init__.py` rỗng; không có `Hypothesis` trong `contracts.py`.
- `contracts.Check`/`Hypothesis`; `configs/hypotheses.yaml`; `hypotheses/library.py`; `tests/test_hypotheses.py`; một bản nháp `hypotheses/inventory_capacity_expansion.yaml` (chép từ ví dụ spec 9.1, `status: draft`, không có nội dung do Claude nghĩ ra).

**Lỗi/sai sót bắt được:** review độc lập (subagent), 0 CRITICAL, 3 HIGH, 4 MEDIUM, 4 LOW, mỗi lỗi có input tái hiện:
- HIGH: file `.yml` bị bỏ sót im lặng; khóa YAML trùng khiến giá trị cuối thắng (che `reviewed_by: Claude` bằng một dòng sau); bộ lọc "reviewed_by là người" qua mặt được bằng `ClaudeBot`, zero-width, Cyrillic, `GPT4`, `TBD`...
- MEDIUM: giá trị rác `0`/`False` lọt qua kiểm tra truthy; chỉ có check critical phản bác; không chặn mã công ty; `sources: ["x"]`.
- LOW: draft mang `reviewed_by: Claude`; tên file không khớp id; `from_dict` không validate (chưa sửa, ghi nhận).
- Đã sửa trừ mục cuối, kèm test hồi quy.

**Phần người cần tự xác minh:**
- **Soạn nội dung thư viện** (6–8 giả thuyết mỗi loại), điền nguồn thật, checks, rồi đặt `status: reviewed` và `reviewed_by`. Bộ lọc "người" chỉ là lưới lọc; người duyệt/commit là kiểm soát cuối.
- Xem lại các quyết định ở mục 3 của `docs/phase_reports/PHASE_6.md` (critical phải có check support, chặn mã công ty).

**Cách nhóm đã kiểm tra (nhóm điền):**
-

**Cập nhật (cùng ngày):** nhóm yêu cầu Claude sinh giả thuyết nháp, Claude đã sinh 15 bản (`status: draft`); sau đó nhóm đưa `library_draft.yaml` của chính họ và yêu cầu bỏ bản của Claude. Đã xóa toàn bộ bản Claude sinh, dùng file của nhóm; loader mở rộng để nạp định dạng `hypotheses: [...]` và các trường phụ. Claude không sửa nội dung giả thuyết của nhóm. Người cần: thêm `checks`, điền `critical_missing_evidence` còn trống (INV-7, GM-7), tạo `HYPOTHESIS_LIBRARY_DRAFT.md` (danh mục nguồn S0–S16), rồi duyệt.

**Cập nhật cuối:** theo yêu cầu của nhóm, Claude đề xuất `checks` cho cả 15 giả thuyết trong `hypotheses/library_draft.yaml` (chèn khối `checks:`, giữ nguyên các trường nhóm viết; có bản sao lưu và so sánh tự động). Ngưỡng `pass_when`, tên `tool`, danh sách chuỗi giá và mức `critical` là phỏng đoán của Claude, nhóm cần rà trước khi duyệt.

## 2026-10-24 — Phase 7 (RAG evidence retrieval)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Bối cảnh:** nhóm yêu cầu làm Phase 7 khi Phase 6 chưa COMPLETED (chưa có giả thuyết `reviewed`) và Phase 3–5 còn điểm tạm. Claude đã nêu điều này trong báo cáo.

**Việc Claude đã làm:**
- Audit-first: `retrieval/` rỗng; `retrieval.yaml` chỉ có khung (`top_k: null`); kho dữ liệu là OCR BCTC (thuyết minh), không có báo cáo thường niên.
- `retrieval/{settings,chunking,retriever,build}.py`, `configs/retrieval.yaml`, `scripts/build_rag_index.py`, `tests/test_retrieval.py`; dựng kho thật 6.698 chunk.

**Lỗi/sai sót bắt được:**
- Review độc lập (subagent): 0 CRITICAL, 0 HIGH, 2 MEDIUM, 3 LOW. Reviewer tấn công lọc as-of (132 mốc × truy vấn top-50, so điểm với kho chỉ chứa chunk đủ điều kiện): không có rò rỉ.
  - MEDIUM: mảnh chunk gộp từ nhiều block mang sai trang/`block_ids` (74 chunk); bật thêm báo cáo `separate` làm cả hai loại mất ngày công bố vì hai ngày ký khác nhau.
  - LOW: `company` bị bỏ qua khi truyền `companies`; crash khi `doc_published` sai kiểu; cache khóa theo ngày; as-of tùy chỉnh sai định dạng không bị từ chối.
  - Đã sửa toàn bộ, kèm 5 test hồi quy; xác minh lại trên kho thật.
- Trong lúc làm: bộ quét bí mật của repo (`test_no_secrets_in_repo`) báo nhầm tên hằng `_TOKEN` (regex tìm "token ="); đổi tên thành `_WORD`.

**Phần người cần tự xác minh:**
- Thay mốc as-of tạm (ngày ký kiểm toán) bằng ngày công bố thật trước khi chạy A/B/C.
- Xác nhận cách hiểu "năm 2024 trở đi" theo năm tài chính (tài liệu FY2023 ký 3/2024 vẫn trong kho).
- Chốt `top_k`, tham số BM25 và so BM25/dense/hybrid trên tập dev; hiện là giá trị khởi điểm của Claude và **chất lượng truy xuất chưa được đo**.
- Đọc thử kết quả truy xuất cho vài truy vấn của giả thuyết trong `hypotheses/library_draft.yaml`.

**Cách nhóm đã kiểm tra (nhóm điền):**
-

## 2026-10-24 — Phase 8 (Quantitative verification tools)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Bối cảnh:** nhóm yêu cầu làm Phase 8 khi Phase 6 chưa COMPLETED (chưa có giả thuyết `reviewed`), chưa có dữ liệu giá bên ngoài, Phase 3–5 còn điểm tạm. Claude đã nêu điều này trong báo cáo.

**Việc Claude đã làm:**
- Audit-first: `verification/` rỗng; không có dữ liệu giá trong repo; thư viện nháp đã nêu 11 tool tên tạm trong `checks`.
- `contracts.CheckResult/CheckInput/CheckStatus`; `verification/{settings,data,context,tools,evaluate}.py` (registry 13 tool); `configs/verification.yaml`; `scripts/run_quant_checks.py`; `docs/EXTERNAL_DATA.md`; `tests/test_verification*.py`.
- Đối chiếu độc lập với Phase 5: hai chỉ báo (`inv_minus_rev_growth`, `dio_change_days`) tính ở hai nơi cho cùng số trên 7/7 ca thật.

**Lỗi/sai sót bắt được:**
- Review độc lập (subagent): 0 CRITICAL, 2 HIGH, 7 MEDIUM, 9 LOW, mỗi lỗi có input tái hiện; không có rò rỉ as-of.
  - HIGH: dự phòng hàng tồn kho lưu số âm nên hai check không bao giờ chạy được; giả thuyết "tồn kho tăng" chạy trên ca tồn kho giảm có thể ra PASS ủng hộ vì cờ có dấu đảo hướng.
  - MEDIUM: `ratio` dùng cùng kỳ cho tử và mẫu; check "tất cả chuỗi giá" PASS khi mới có một nửa dữ liệu; GM-3 dùng `abs` lẫn với GM-2; `ratio_of_changes` không đo đúng ý GM-4; `validate_check` văng lỗi thô; typo tên metric lẫn với thiếu dữ liệu; số ngày DIO không có trong inputs.
  - Đã sửa toàn bộ HIGH/MEDIUM và hầu hết LOW, kèm ~60 test hồi quy; còn 1 LOW ghi nhận (phân vị luôn > 0).
- Một số check do chính Claude đề xuất ở Phase 6 có ngữ nghĩa sai (GM-3, GM-4, GM-7); đã chỉnh trong khối `checks` của `library_draft.yaml`, không đụng nội dung nhóm viết.

**Phần người cần tự xác minh:**
- Thu thập chuỗi giá (`docs/EXTERNAL_DATA.md`), chốt `lag_months`, `publication_lag_days`.
- Rà các `checks` và ngưỡng `pass_when` trong `hypotheses/library_draft.yaml` (đều là phỏng đoán của Claude).
- Quyết định có soạn giả thuyết cho tồn kho GIẢM hay không (hiện không chạy giả thuyết tồn kho trên ca giảm).
- Quyết định có trích thêm các metric dấu `*` ở Phase 3 hay chấp nhận NOT_EVALUABLE.

**Cách nhóm đã kiểm tra (nhóm điền):**
-

**Cập nhật cuối (cùng ngày), theo yêu cầu của nhóm:**
- Nhóm thêm `iron_ore_price.xlsx` (World Bank, 2015–2024) và `library_draft_new.yaml` (22 giả thuyết, có `direction`, 7 giả thuyết giảm tồn kho) và giao Claude tự quyết cách xử lý chỉ tiêu chưa trích.
- Claude: viết importer xlsx → CSV (`scripts/import_price_xlsx.py`), chuyển file giá vào `data/external/`, gộp thư viện mới (giữ nguyên nội dung nhóm, soạn `checks` đề xuất cho DEC-1..7), thêm trường `direction` và tool `metric_level`, thêm 4 chỉ tiêu vào `configs/normalization.yaml` (`accounts_payable`, `goods_in_transit`, `depreciation`, `inventory_change_cf`) sau khi so sánh trước/sau (fact cũ giữ nguyên) và đối chiếu giá trị với OCR gốc.
- Claude tự quyết (nhóm giao): trích thêm chỉ tiêu bằng cấu hình thay vì để NOT_EVALUABLE; không trích chỉ tiêu cần báo cáo thường niên hoặc khi OCR gộp dòng; hướng giả thuyết lấy từ chính giả thuyết (bỏ cấu hình riêng).
- **Chưa review độc lập** cho phần cập nhật này; test tự động 786 passed. Người cần: rà `checks` DEC-1..7, đọc lại ngưỡng, xem NKG/HSG còn thiếu chỉ tiêu nào, bổ sung giá 2014 và các chuỗi giá còn lại.

## 2026-10-24 — Phase 9 (LLM reasoning integration)

**Công cụ:** Claude Code (Claude Sonnet 5.5).

**Bối cảnh:** nhóm yêu cầu làm Phase 9 ("nếu không có gì quá nghiêm trọng") khi Phase 6–8 chưa COMPLETED và phần cập nhật cuối của Phase 8 chưa qua review độc lập. Claude đã nêu điều này.

**Việc Claude đã làm:**
- Audit-first: `llm/` rỗng; `configs/llm.yaml` chỉ có khung (model, temperature null). Đọc tài liệu API hiện hành (skill claude-api) trước khi viết client: phát hiện model hiện tại từ chối `tool_choice` ép buộc nên dùng structured output (`output_config.format`), và API từ phiên bản 2026-10-21 từ chối `temperature/top_p/top_k` nên không gửi (loader cấm đặt chúng).
- `llm/{settings,schema,client,quotes,prompts,evidence}.py`, `contracts.Evidence/TextCheckStatus`, `scripts/run_text_evidence.py`, `tests/test_llm.py`, mở rộng `configs/llm.yaml` (model/effort/max_tokens là ĐỀ XUẤT của Claude).
- **Chưa gọi API thật** (không có gói `anthropic` và API key trong môi trường làm việc); mọi test dùng transport giả; request của `AnthropicTransport` chỉ được đối chiếu với tài liệu và với module `anthropic` giả.

**Lỗi/sai sót bắt được:**
- Review độc lập (subagent): 0 CRITICAL, 1 HIGH, 4 MEDIUM, 5 LOW, mỗi lỗi có input tái hiện; không có rò rỉ as-of.
  - HIGH: lỗi ghi cache/log làm mất kết quả đã trả tiền và sập pipeline.
  - MEDIUM: prompt injection qua thuộc tính `section` và qua thẻ viết hoa/thường (cách escape cũ còn làm trích dẫn đúng bị loại oan); MISSING bị lạm dụng (chunk LLM bịa, quote rỗng); judgement đầu hỏng làm judgement sau đúng bị bỏ; truy vấn diễn đạt lại không kiểm số liệu/mã công ty.
  - Đã sửa toàn bộ kèm ~25 test hồi quy; còn ghi nhận: `call_log` chưa xoay vòng.
- Trong lúc làm: nhiều lệnh shell dùng heredoc bị hỏng ở môi trường này (dấu nháy/xuống dòng), nên chuyển sang ghi script bằng công cụ ghi file; bộ quét bí mật của repo từng báo nhầm một dòng gán khóa API trong client (viết lại thành hai dòng).

**Phần người cần tự xác minh:**
- **Chạy thử API thật với 1–2 ca** (`scripts/run_text_evidence.py --limit 1`) trước khi chạy hàng loạt; kiểm nhật ký token/độ trễ và chi phí.
- Chốt `model`, `effort`, `max_tokens` trong `configs/llm.yaml` (đang là đề xuất); chúng nằm trong hash freeze.
- Đọc thử vài kết quả phân loại: trích dẫn đúng nguyên văn **không** có nghĩa là nhãn support/contradict đúng ngữ nghĩa.
- Quyết định có bật fallback khi mô hình từ chối (hiện chỉ ghi nhận và coi check là UNVERIFIED).

**Cách nhóm đã kiểm tra (nhóm điền):**
-
