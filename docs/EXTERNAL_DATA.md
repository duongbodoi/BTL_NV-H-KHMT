# Dữ liệu giá bên ngoài (Phase 8)

Các check như `lagged_external_series`, `percentile_vs_history`, `growth_vs_series` cần chuỗi giá THÁNG. Repo **không chứa** dữ liệu này (nhóm thu thập, spec mục 7.1: quặng sắt từ World Bank Pink Sheet; giá thép và các chuỗi khác từ nguồn công khai chốt khi thu thập). Chuỗi thiếu file → check dùng chuỗi đó ra `NOT_EVALUABLE` (không phải FAIL, không bị bỏ qua).

## Chuỗi và đường dẫn

Khai trong `configs/verification.yaml` (`external.series`). Tên chuỗi dùng trong thư viện giả thuyết:

| Chuỗi | File mặc định | Dùng cho |
|---|---|---|
| `iron_ore_price` | `data/external/iron_ore_price.csv` | INV-1, INV-5, GM-2, GM-3 |
| `coking_coal_price` | `data/external/coking_coal_price.csv` | GM-2 |
| `hrc_price` | `data/external/hrc_price.csv` | INV-1, INV-5, GM-1, GM-2, GM-3 |
| `steel_price` | `data/external/steel_price.csv` | GM-1 |
| `usd_vnd` | `data/external/usd_vnd.csv` | GM-8 |

## Định dạng CSV

```csv
date,value
2022-01,150.40
2022-02,148.10
```

- Cột `date,value` đúng thứ tự; `date` là `YYYY-MM` hoặc `YYYY-MM-DD` (chỉ lấy tháng); mỗi tháng đúng một dòng.
- `value` là số dương. Đơn vị ghi trong tên file/nguồn của nhóm; tool chỉ dùng **thay đổi tương đối** và **phân vị**, nên đơn vị không ảnh hưởng, nhưng phải nhất quán trong một chuỗi.
- File sai định dạng (thiếu cột, tháng lặp, số âm...) làm cả lần tải lỗi, không bị đọc dở.

## Chống rò rỉ (spec mục 7.2)

Giá tháng M chỉ được dùng nếu `cuối tháng M + publication_lag_days <= as-of của ca`. `publication_lag_days: 35` là đề xuất của Claude; nhóm chốt theo độ trễ công bố thật của từng nguồn. Nếu chuỗi bạn dùng hiệu chỉnh số liệu về sau (revised), hãy lưu **giá như công bố lúc đó**; dùng giá đã hiệu chỉnh là rò rỉ nhẹ và nên ghi vào data card.

## Độ trễ

`lag_months` trong config là số tháng từ giá đầu vào tới giá vốn; các giá trị hiện tại (3, 3, 1, 1, 0) là **đề xuất khởi điểm của Claude**, không phải quyết định của nhóm. Check đặt `lag: from_config` dùng số này; check đặt số cụ thể (ví dụ `lag: 0`) bỏ qua config.
