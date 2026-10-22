# Báo cáo Phase 2 — Table/data extraction

- **Ngày:** 2026-10-23
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` (v2), mục 7.1, 7.3, 16, 17, 18, 20 (Phase 2), 23
- **Trạng thái:** phần việc của Claude đã xong. Đã qua 2 vòng review độc lập và chạy lại regression test. **Chưa COMPLETED** cho tới khi nhóm spot-check và commit (spec mục 25, 28.2).

---

## Phase này để làm gì?

Phase 1 đã tách tài liệu OCR thành các block, trong đó block bảng đã được chia thành ô. Tuy nhiên mỗi ô vẫn chỉ là chuỗi như `"(17.265.822.207)"`, `"-"`, `"31/12/2024 VND"`. Để phát hiện bất thường (Phase 5) và kiểm chứng giả thuyết (Phase 8), hệ thống cần **con số** kèm ngữ cảnh: số đó thuộc dòng nào, kỳ nào, đơn vị gì, và lấy từ ô nào trong tài liệu.

Phase 2 biến **ô bảng thành giá trị số thô**. Mỗi giá trị có nhãn dòng, mã số, thuyết minh, header cột, kỳ, đơn vị, trạng thái và provenance. Phase này cũng thêm **adapter đọc BCTC có cấu trúc từ CSV**, là nguồn chính theo spec mục 7.1.

Nguyên tắc chi phối mọi quyết định là **không biết thì để trống, không đoán** (spec mục 23):

- Dấu `-` không bao giờ thành 0.
- Ô hỏng là `UNRESOLVED`, không bị bỏ qua.
- Kỳ hoặc đơn vị mơ hồ thì để `None` và gắn cờ.

| Phase sau | Dùng kết quả Phase 2 để |
|---|---|
| Phase 3 — Chuẩn hóa metric | map `row_label` / `row_code` về metric chuẩn (tồn kho, doanh thu, giá vốn…), quy đổi đơn vị, áp quy tắc ưu tiên nguồn |
| Phase 4 — Validation | kiểm tổng thành phần, đối chiếu ngẫu nhiên với BCTC gốc qua `value_id` / provenance |
| Phase 5, 8 | dùng giá trị `OK` đã chuẩn hóa; giá trị khác `OK` thành `MISSING` / `NOT_EVALUABLE` |

Phase này **chưa** map nhãn về metric chuẩn, **chưa** quy đổi đơn vị và **chưa** gọi LLM.

---

## 1. Tóm tắt: đã làm được gì

| Đã làm được | Để làm gì |
|---|---|
| Parse số theo định dạng Việt Nam (`numbers.py`): dấu chấm ngăn nghìn (mỗi nhóm đúng 3 chữ số, nhóm đầu không bắt đầu bằng 0), dấu phẩy thập phân, số âm trong ngoặc `(1.234)` hoặc có dấu trừ, `%`. Giá trị lưu bằng `Decimal`. | Lấy đúng con số từ BCTC, không sai số do float. |
| `-` (và các dấu gạch Unicode) → `value=None`, `NOT_APPLICABLE`. Ô rỗng → `MISSING`. Mọi chuỗi khác không khớp trọn vẹn → `UNRESOLVED`, kèm lý do: `multiple_numbers` (OCR dính số), `mixed_text`, `bad_format`, `no_digits`. | Theo yêu cầu của nhóm: dấu gạch không ép về 0, ô không parse được là `UNRESOLVED`. |
| Trích bảng (`tables.py`): trải `rowspan`/`colspan` thành lưới; nhận header nhiều tầng và dòng đánh số cột (TT200); nhận header lặp giữa bảng, trường hợp bảng bị OCR gộp. Mỗi ô của cột giá trị cho ra một `RawTableValue`. | Đọc được bảng thuyết minh thật mà không dựa vào số trang hay số dòng cố định. |
| Phân vai cột: mã số / thuyết minh / STT (khi ô header khớp trọn từ khóa trong config), cột nhãn, cột giá trị, cột chữ. | Nhãn dòng và mã số đi kèm từng giá trị, không bị đọc nhầm thành số liệu. |
| Đọc kỳ từ header: `dd/mm/yyyy`, "ngày … tháng … năm …", năm, "Quý N/yyyy", "Quý N" ghép với năm ở header cha. Đọc đơn vị từ header hoặc dòng "Đơn vị tính:" cùng trang. Kỳ mơ hồ, tương đối, lũy kế, hoặc lấy từ dòng header giữa bảng không sạch → `None` kèm cờ. Đơn vị là ngoại tệ, bậc độ lớn không nhận ra, hoặc mâu thuẫn → `None` kèm cờ. | Không gán nhầm kỳ hoặc bậc độ lớn (sai 10^3–10^9) mà không ai biết. |
| Adapter CSV (`structured.py`): layout wide/long, định dạng số `plain`/`vn`, tên cột trong config. Chỉ `OK` mới có `value`. Khóa trùng mà khác giá trị/đơn vị → mọi bản `UNRESOLVED`. Dòng thiếu/thừa cột, kỳ hoặc công ty không đọc được → `UNRESOLVED`. Provenance gồm file, sha256, dòng, cột. | Có nguồn chính cho anomaly engine. Mâu thuẫn không được tự chọn im lặng (spec mục 7.1). |
| `ValueStatus` (spec mục 18) trong `contracts.py`; config `configs/extraction.yaml`. | Contract dùng chung; mọi từ điển và từ khóa nằm trong config, không nằm trong code. |

Kết quả trên fixture HPG (chỉ là ví dụ):

| Chỉ số | Kết quả |
|---|---|
| Bảng | 74 |
| Giá trị | 1637 |
| `OK` | 1404 |
| `NOT_APPLICABLE` (dấu `-`) | 184 |
| `MISSING` | 31 |
| `UNRESOLVED` | 18 (gồm 17 ô OCR dính nhiều số và 1 ô lẫn chữ) |

Test bất biến xác nhận: trên HPG, không ô nào có chữ số bị mất (kiểm theo vị trí ô).

## 2. File tạo

- `configs/extraction.yaml`
- `src/steel_xai/extraction/numbers.py`
- `src/steel_xai/extraction/settings.py`
- `src/steel_xai/extraction/tables.py`
- `src/steel_xai/extraction/structured.py`
- `tests/test_extraction_numbers.py`
- `tests/test_extraction_tables.py`
- `tests/test_extraction_structured.py`
- `docs/phase_reports/PHASE_2.md`

## 3. File sửa

- `src/steel_xai/contracts.py`: thêm `ValueStatus`.
- `src/steel_xai/config.py`: thêm `"extraction"` vào `CONFIG_NAMES`.
- `src/steel_xai/extraction/__init__.py`: export API.
- `README.md`
- `AI_USAGE_LOG.md`

## 4. Quyết định thiết kế

- **Dấu `-` → `NOT_APPLICABLE`, `value=None`.** Nhóm đã chốt trước khi code. Phase 3 quyết định có coi là 0 khi cộng tổng hay không, và phải ghi rõ khi làm vậy.
- **Quy ước VN cố định cho bảng OCR:** `1.751` là 1751, `1,234` là 1,234. Trường hợp `1,234` (đúng 3 chữ số thập phân, không có nhóm nghìn) có ghi chú `ambiguous_separator`.
- **Bất biến `value is not None` ⇔ `status == OK`** cho cả bảng lẫn CSV. Số của bản `UNRESOLVED` vẫn đọc được ở `raw`.
- **Header nhận theo nội dung ô, không theo số dòng:**
  - Năm/ngày trần (không kèm chữ) chỉ là header khi dòng không có nhãn, hoặc khi khối header phía trên chưa có kỳ nào.
  - Ô số hỏng không bao giờ được coi là header, để không bị nuốt im lặng vào tiêu đề cột.
- **Header giữa bảng:**
  - Dòng chỉ có tiêu đề mục → giữ header cột.
  - Dòng mang kỳ hoặc đơn vị → thay hẳn header.
  - Dòng mang kỳ nhưng còn chữ khác ngoài từ chỉ kỳ (ví dụ "Đến năm 2030") → các dòng sau ra `period_ambiguous`.
- **Ô góc trái** (header cột nhãn, ví dụ "Cho năm kết thúc ngày …") áp cho mọi cột, kèm cờ `period_from_corner`. Ngoại lệ: cột có cụm kỳ tương đối ("Năm trước", "Số đầu năm"…) → `period_relative`.
- **Không quy đổi đơn vị**, chỉ gắn mã (`VND`, `million_VND`…). Đơn vị không nhận ra không bao giờ rơi về VND.
- **`company` do người gọi truyền vào**, không suy ra từ nội dung tài liệu.
- **Không thêm vnstock**: cần mạng và nhóm chưa chốt nguồn. Adapter đọc CSV xuất từ nguồn bất kỳ. Không thêm dependency mới.

## 5. Tests

196 test case mới, tính cả parametrize:

- `test_extraction_numbers.py` (67): số VN hợp lệ, dấu gạch, ô rỗng, mọi dạng `UNRESOLVED`, nhóm nghìn có số 0 đầu, chữ số không phải ASCII, định dạng plain.
- `test_extraction_tables.py` (92):
  - lưới rowspan/colspan, header nhiều tầng;
  - mã số / thuyết minh / STT;
  - kỳ (ngày, năm, quý, quý không năm, mơ hồ, tương đối, lũy kế);
  - đơn vị (header, dòng đơn vị của trang, ngoại tệ, bậc độ lớn lạ);
  - header giữa bảng; dòng đánh số cột;
  - các ca "không mất im lặng";
  - roundtrip JSON, tính deterministic;
  - test hồi quy cho mọi phát hiện review có input tái hiện;
  - test tích hợp HPG, gồm bất biến "mọi ô có chữ số đều được ghi nhận", kiểm theo vị trí ô.
- `test_extraction_structured.py` (37): layout wide/long, trạng thái, kỳ sai, dòng ragged, ô thừa, khóa trùng (phân biệt hoa thường, đơn vị), `ignore_columns`, header NFC, validate config.

## 6. Commands đã chạy

```bash
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

## 7. Kết quả

| Passed | Skipped | Failed |
|---|---|---|
| 295 | 0 | 0 |

Ruff check và ruff format: sạch.

## 8. Known limitations

- **Nhận diện header và vai trò cột là heuristic.** Bảng lạ có thể bị đọc chưa đúng. Khi không chắc, extractor gắn cờ (`no_header`, `no_label_column`, `subheader_in_body`, `after_subheader`, `period_*`, `unit_*`) chứ không đoán. **Phase 3 phải lọc theo cờ**, không chỉ theo `status`.
- **Kỳ nằm ở nhãn dòng chưa được đọc.** Ví dụ bảng biến động TSCĐ: "Số dư đầu năm", "Số dư cuối năm". Các giá trị này ra `period_missing`; trên HPG có 493 giá trị như vậy. Bảng biến động bị OCR gộp cũng chưa được tách.
- **Từ khóa kỳ tương đối là danh sách** (`relative_period_keywords`). Cụm ngoài danh sách sẽ nhận kỳ của ô góc trái, nhưng vẫn có cờ `period_from_corner` để kiểm. "Năm nay" dưới tiêu đề có ngày cụ thể ra `period_relative` (bảo thủ).
- **Cột 0 là số La Mã/số thứ tự mà không có header** "STT" thì cột nhãn bị đọc sai (có cờ `no_label_column`).
- **CSV:**
  - Cột có trong file mà không khai báo sẽ bị coi là chỉ tiêu. Phải liệt kê cột bỏ qua ở `ignore_columns`.
  - Chưa hỗ trợ Parquet (cần pandas/pyarrow).
  - Chưa có adapter gọi vnstock trực tiếp.
- **Bảng OCR luôn dùng định dạng số VN.** Tài liệu dùng định dạng Anh sẽ ra `UNRESOLVED`/`ambiguous_separator`, không bị đọc sai im lặng với số lớn.

## 9. Chưa làm vì ngoài scope

- Map nhãn về metric chuẩn, quy đổi đơn vị, quy tắc ưu tiên nguồn, mâu thuẫn giữa nguồn (Phase 3).
- Kiểm tổng thành phần, quan hệ kế toán, báo cáo đối chiếu (Phase 4).
- Nối bảng qua trang.

## 10. Hard-code công ty

Không có. Code không phụ thuộc tên công ty, số trang, số dòng hay năm. Từ khóa (mã số, thuyết minh, đơn vị, kỳ tương đối…) là quy ước BCTC Việt Nam và nằm trong `configs/extraction.yaml`. Chỉ test tích hợp mới chứa kỳ vọng riêng của fixture HPG (một ô ví dụ ở trang 40), với vai trò ví dụ.

## 11. AI_USAGE_LOG.md

Đã thêm mục "2026-10-23 — Phase 2". Review độc lập 2 vòng:

| Vòng | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| 1 | 2 | 4 | 7 (+1 test) | 7 |
| 2 | 1 | 3 | 3 | 5 |

Tất cả CRITICAL/HIGH/MEDIUM đã sửa và có test hồi quy. Các LOW còn mở được ghi ở mục 8.

Phần người cần xác minh: xem mục tương ứng trong `AI_USAGE_LOG.md`.

## 12. Khác báo cáo giữa kỳ

Không có điểm khác mới. Không sửa `DEVIATIONS_FROM_MIDTERM.md`.
