# Các điểm khác báo cáo giữa kỳ

Ghi mọi điểm khác báo cáo giữa kỳ kèm lý do (spec mục 26.2). Mọi điểm phát sinh trong quá trình làm phải được thêm vào đây trước khi nộp.

## Các điểm đã chốt (từ spec v2, mục 26.2)

| Điểm | Giữa kỳ | Bản này | Lý do |
|---|---|---|---|
| LLM phân loại bằng chứng ở bước 3 | LLM chỉ viết lại ở bước 4 | thêm phân loại bằng chứng, quote kiểm bằng code | cần đọc hiểu thuyết minh dài; rủi ro giới hạn bằng structured output + kiểm nguyên văn |
| Năm 2024 trở đi | giai đoạn 2021–2023 | chỉ làm nguồn đáp án tham chiếu | diễn biến kỳ sau của ca cuối 2023 nằm sau 2023; không đưa vào RAG nên không rò rỉ |
| Đo lường | Hit@k, citation, số lệch | thêm MRR, unsupported claim, false-SUPPORTED, dev/test, CI, kappa, ablation | cỡ mẫu nhỏ cần kiểm chứng tách biệt và nêu độ tin cậy |
| So khớp giả thuyết giữa cấu hình | chưa nêu | chạy chính (ánh xạ mù) + chạy phụ (cùng danh sách) | baseline sinh tự do, C dùng thư viện cố định |

## Các điểm phát sinh trong quá trình làm

| Ngày | Phase | Điểm | Giữa kỳ | Bản này | Lý do |
|---|---|---|---|---|---|
| 2026-10-23 | 5 | Cửa sổ chọn ca | giai đoạn gốc 2021–2023 (spec v2 mục 3.3; nhóm đối chiếu với giữa kỳ) | chọn ca trong các năm tài chính 2016–2023 (NKG từ 2017); năm 2024 trở đi vẫn chỉ làm tham chiếu | dữ liệu là BCTC năm nên số kỳ giảm 4 lần so với quý; mở rộng số năm để giữ cỡ mẫu cho 18 ca. Nền lịch sử và peer chỉ dùng thông tin có tại kỳ đang xét. Khoảng năm nằm trong `configs/dataset.yaml` (`case_selection_window`) |
| 2026-10-23 | 5 | Mốc as-of | ngày công bố BCTC của kỳ bất thường (spec v2 mục 7.2) | ngày ký báo cáo kiểm toán độc lập, trích tự động từ từng tài liệu (`asof_source: audit_report_date`), có ghi dòng gốc làm bằng chứng trong `data/processed/asof_dates.csv` | nguồn dữ liệu chỉ có năm công bố, không có ngày công bố chính thức. BCTC kiểm toán chỉ công bố được sau khi ký báo cáo kiểm toán, nên ngày ký luôn sớm hơn hoặc bằng ngày công bố: dùng mốc này là thận trọng (không rò rỉ thông tin sau công bố), đổi lại có thể bỏ sót tài liệu phát hành giữa ngày ký và ngày công bố. Mọi kỳ trong cửa sổ chọn ca đều trích được ngày (nhóm duyệt 2026-10-25) |
| 2026-10-24 | 9 | Nhà cung cấp LLM | (nhóm đối chiếu với giữa kỳ) | Gemini `gemini-3.5-flash-lite` free tier cho cả chạy thử và đánh giá chính thức (nhóm chốt 2026-10-25); A/B/C, chạy phụ và ablation dùng cùng model/thiết lập (`configs/llm.yaml`, nằm trong freeze) | chi phí: không tốn tiền. Đổi lại: model nhỏ hơn các model tính phí, hạn mức lời gọi/phút (có thể 429/503, runner chạy lại lần lỗi). Báo cáo cuối ghi rõ model; ước tính chi phí nếu dùng model tính phí ở `docs/COST_ESTIMATE.md` |
| 2026-10-25 | 12a | Số ca đánh giá | 15–20 ca (spec v2: 18 ca, 5 dev + 13 test) | 11 ca, 4 dev + 7 test | engine chỉ phát hiện 11 bất thường vượt ngưỡng trong cửa sổ chọn ca; nhóm quyết lấy toàn bộ 11 ca (không lọc theo phán đoán) thay vì thêm ca không vượt ngưỡng. Chia dev/test ngẫu nhiên phân tầng theo loại bất thường, seed cố định (`data/eval/cases.yaml`). Cỡ mẫu nhỏ hơn làm CI rộng hơn; kết quả vẫn là khảo sát khả thi |

> Các điểm khác (dữ liệu năm thay quý, 3 công ty, chỉ YoY, giả thuyết mùa vụ không kiểm được) đang ở dạng nháp tại `docs/DRAFT_SPEC_CHANGES.md`; chép vào bảng này sau khi nhóm duyệt.
