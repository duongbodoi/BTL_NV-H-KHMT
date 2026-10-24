# Thư viện giả thuyết (ngành thép)

Nội dung do **nhóm soạn** (`library_draft.yaml`, 7 giả thuyết tồn kho + 8 biên gộp, tất cả `status: draft`). Schema và luật kiểm:
`src/steel_xai/hypotheses/library.py`, cấu hình: `configs/hypotheses.yaml`.

Kiểm nhanh: `python -c "from steel_xai.hypotheses import load_library; print(load_library().coverage())"`

## Hướng (`direction`)

Mỗi giả thuyết khai `direction`: `up` (giải thích bất thường tăng), `down` (giảm) hoặc `both`. Kiểm chứng chỉ chạy giả thuyết trên bất thường cùng hướng. Bắt buộc khi `reviewed`. Thư viện hiện có 14 giả thuyết tồn kho (7 giải thích tăng, 7 giải thích giảm: DEC-1..7; INV-4 và INV-6 là `both`) và 8 giả thuyết biên gộp.

## Định dạng file

- Một file có thể chứa nhiều giả thuyết: gốc là `hypotheses: [...]` (như `library_draft.yaml`), hoặc một giả thuyết/file với tên `<id>.yaml`.
- Trường bắt buộc: 9 trường của báo cáo (spec mục 9.1). Trường phụ: `sources`, `status`, `reviewed_by`, `checks`, `code`, `testable_with_annual_data` (yes | partial | no), `source_strength`, `extension_beyond_midterm`.
- Chỉ đuôi `.yaml` chữ thường; `.yml`/`.json` trong thư mục này là lỗi (không bỏ sót im lặng). Khóa YAML trùng là lỗi.

## Điều kiện để một giả thuyết thành `reviewed` (vào pipeline)

`status: reviewed`, `reviewed_by` là họ tên người, `sources` không rỗng, claim/rationale/phản bác/`critical_missing_evidence` không rỗng,
**có `checks`** gồm ≥ 1 check `critical` polarity `support` và ≥ 1 check phản bác, nội dung không nhắc mã công ty.
Mã nguồn (`S0`..) phải có trong `HYPOTHESIS_LIBRARY_DRAFT.md` (`sources_catalog` trong config); file đó hiện chưa có trong repo.

## Còn thiếu so với `reviewed`

- `checks` đã được Claude **đề xuất** cho cả 15 giả thuyết (ngày 2026-10-24, dựa trên `expected_*_evidence` của nhóm). Nhóm cần rà: ngưỡng trong `pass_when` (phỏng đoán), danh sách `series`, và quyết định check nào là `critical`. Tên `tool` (`growth_yoy`, `growth_gap`, `share_of_change`, `ratio`, `ratio_change`, `ratio_of_changes`, `share_shift`, `days_inventory_change`, `percentile_vs_history`, `growth_vs_series`, `lagged_external_series`) chốt cùng registry ở Phase 8. Cờ trong `pass_when`: `signed_by_anomaly_direction`, `signed_opposite_to_anomaly_direction`, `abs`, `any`.
- Check dùng metric chưa trích (`goods_in_transit`, `accounts_payable`, `inventory_provision_charge`, `segment_revenue`, `export_revenue_share`, `production_volume`, `depreciation`, `cip_transfer_to_fixed_assets`) sẽ `NOT_EVALUABLE` tới khi Phase 3 trích thêm.
- `critical_missing_evidence` rỗng ở INV-7 và GM-7.
- Metric có dấu `*` trong `expected_quantitative_evidence` là chỉ tiêu Phase 3 chưa trích (check định lượng tương ứng sẽ `NOT_EVALUABLE`).
