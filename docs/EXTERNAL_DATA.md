# Dữ liệu giá bên ngoài (Phase 8)

Các check như `lagged_external_series`, `percentile_vs_history`, `growth_vs_series` cần chuỗi giá THÁNG. Nhóm thu thập (đã có `iron_ore_price` từ 2026-10-24; spec mục 7.1: quặng sắt từ World Bank Pink Sheet; giá thép và các chuỗi khác từ nguồn công khai chốt khi thu thập). Chuỗi thiếu file → check dùng chuỗi đó ra `NOT_EVALUABLE` (không phải FAIL, không bị bỏ qua).

## Chuỗi và đường dẫn

Khai trong `configs/verification.yaml` (`external.series`). Tên chuỗi dùng trong thư viện giả thuyết:

| Chuỗi | File mặc định | Dùng cho |
|---|---|---|
| `iron_ore_price` | `data/external/iron_ore_price.csv` | INV-1, INV-5, GM-2, GM-3 |
| `coking_coal_price` | `data/external/coking_coal_price.csv` | GM-2 |
| `hrc_price` | `data/external/hrc_price.csv` | INV-1, INV-5, GM-1, GM-2, GM-3 |
| `steel_price` | `data/external/steel_price.csv` | GM-1 |
| `usd_vnd` | `data/external/usd_vnd.csv` | GM-8 |

## Nhập từ Excel

```
python scripts/import_price_xlsx.py data/external/iron_ore_price.xlsx iron_ore_price
```

Cột A: tháng (`2015M01` kiểu World Bank, `2015-01`, `2015-01-31` hoặc ngày Excel), cột B: giá; dòng đầu là tiêu đề cũng được. Ghi ra CSV tại đường dẫn khai trong `configs/verification.yaml`. Dòng nào sai (tháng lặp, ngày lạ, giá ≤ 0) làm cả lần nhập lỗi và nêu số dòng.

Bảng nhiều cột tổng hợp tay (VSA): chọn cột bằng `--col`; `--midpoint-ranges` nhận ô `458-462` (lấy **trung bình hai đầu**, nhóm chốt 2026-10-24) và hậu tố `USD/T`; `--trailing-empty-ok` bỏ các dòng chỉ có ngày ở cuối bảng (dòng trống xen giữa vẫn lỗi). Hai tùy chọn mặc định tắt.

```
python scripts/import_price_xlsx.py data/external/vsa_prices.xlsx hrc_price --col C --midpoint-ranges --trailing-empty-ok
python scripts/import_price_xlsx.py data/external/vsa_prices.xlsx coking_coal_price --col E --midpoint-ranges --trailing-empty-ok
```

Chuỗi hiện có: `iron_ore_price` (World Bank, $/dmtu, 2015-01..2024-12; **nên thêm 2014 nếu dùng FY2015–2016**, vì cửa sổ so sánh 12 tháng cần dữ liệu trước năm đang xét). Dữ liệu World Bank dùng giấy phép mở (CC BY 4.0): nhớ ghi nguồn trong `docs/DATA_CARD.md`.

`hrc_price` và `coking_coal_price` (thêm 2026-10-24): từ `data/external/vsa_prices.xlsx` do nhóm tổng hợp tay từ tin thị trường của Hiệp hội Thép Việt Nam (https://vsa.com.vn/category/tin-tuc/thi-truong-trong-nuoc/), giá **đầu tháng**, USD/tấn, 48 tháng 2019-01..2022-12. Cột "thép cuộn" được nhóm xác nhận là HRC (thép cuộn cán nóng). Giới hạn:
- Chỉ phủ 2019–2022: các ca FY2016–FY2018 vẫn `NOT_EVALUABLE` cho hai chuỗi này; `percentile_vs_history` (cần ≥ 36 tháng lịch sử) chưa chạy được cho FY2021. Các dòng 2023–2024 trong file chưa có giá.
- Nguồn chỉ ghi ở mức chuyên mục, chưa có link bài cho từng tháng; ô khoảng giá đã lấy trung bình (thông tin gốc là khoảng).
- Cột "phôi thép" (chỉ 2019-01..06) và "thép phế liệu" chưa dùng: chưa có chuỗi/giả thuyết nào trong thư viện cần chúng.

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
