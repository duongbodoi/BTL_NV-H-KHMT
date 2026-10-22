# steel_xai — Giải thích bất thường BCTC ngành thép

Spec chính thức: `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md`. Trạng thái: Phase 0, Phase 1 đã xong; Phase 2 (table/data extraction) chờ review/commit.

## Parse văn bản OCR (Phase 1)

```python
from steel_xai.parsing import parse_file

doc = parse_file("tài_liệu.txt", doc_id="abc_2023")  # mỗi trang mở đầu bằng "===== PAGE N ====="
for block in doc.iter_blocks("table"):
    print(block.block_id, block.page, block.provenance())  # page = số trang PDF, không phải số in
```

## Trích số thô (Phase 2)

```python
from steel_xai.extraction import extract_document, load_structured_csv

for table in extract_document(doc, company="ABC"):  # company do người gọi cung cấp
    for v in table.values:  # mỗi ô của cột giá trị là một RawTableValue
        print(v.row_label, v.period, v.unit, v.raw, v.value, v.status, v.flags)

rows = load_structured_csv("bctc.csv")  # tên cột/layout/định dạng số trong configs/extraction.yaml
```

- Số VN: `1.234.567` → 1234567, `(1.234)` → -1234, `12,5%` → 12.5 (`is_percent`). Giá trị là `Decimal`.
- `-` → `value=None`, `NOT_APPLICABLE` (không bao giờ là 0); ô rỗng → `MISSING`; không parse được → `UNRESOLVED` kèm lý do trong `flags`.
- Chưa map nhãn về metric chuẩn và chưa quy đổi đơn vị (Phase 3).

## Cài đặt

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Cần cài dạng editable (`-e`) để `steel_xai` tìm đúng thư mục `configs/`; có thể ghi đè bằng biến môi trường `STEEL_XAI_CONFIG_DIR`.

API key chỉ đặt qua biến môi trường có tên khai báo ở `configs/llm.yaml` (`api_key_env`, giá trị tạm chờ nhóm chốt provider). Không commit `.env`.

## Test và lint

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Cấu trúc

```text
configs/     anomaly.yaml, retrieval.yaml, llm.yaml, eval.yaml, dataset.yaml, extraction.yaml (giá trị null = nhóm chưa chốt)
data/        raw/ (không commit), processed/, eval/
hypotheses/  thư viện giả thuyết (nội dung do nhóm soạn)
src/steel_xai/  config.py, contracts.py (Provenance, ValueStatus), parsing/ (Phase 1), extraction/ (Phase 2), các subpackage còn rỗng
tests/       test config loader, vệ sinh repo, parsing, extraction; fixtures/ (HPG chỉ là ví dụ)
docs/
AI_USAGE_LOG.md, DEVIATIONS_FROM_MIDTERM.md
```
