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
| 2026-10-23 | 5 | **[NHÁP – chờ nhóm duyệt]** Cửa sổ chọn ca | giai đoạn gốc 2021–2023 (spec v2 mục 3.3; nhóm đối chiếu với giữa kỳ) | chọn ca trong các năm tài chính 2016–2023 (NKG từ 2017); năm 2024 trở đi vẫn chỉ làm tham chiếu | dữ liệu là BCTC năm nên số kỳ giảm 4 lần so với quý; mở rộng số năm để giữ cỡ mẫu cho 18 ca. Nền lịch sử và peer chỉ dùng thông tin có tại kỳ đang xét. Khoảng năm nằm trong `configs/dataset.yaml` (`case_selection_window`) |
| 2026-10-23 | 5 | **[NHÁP – chờ nhóm duyệt]** Mốc as-of | ngày công bố BCTC của kỳ bất thường (spec v2 mục 7.2) | tạm dùng ngày ký báo cáo kiểm toán độc lập, trích tự động từ từng tài liệu (`asof_source: audit_report_date`); không trích được thì để trống và nhóm tra tay | bộ OCR không có ngày công bố chính thức; ngày ký kiểm toán là mốc gần nhất có trong tài liệu và thường sớm hơn hoặc bằng ngày công bố. Cần thay bằng ngày công bố thật trước khi chạy cấu hình A/B/C |

> Các điểm khác (dữ liệu năm thay quý, 3 công ty, chỉ YoY, giả thuyết mùa vụ không kiểm được) đang ở dạng nháp tại `docs/DRAFT_SPEC_CHANGES.md`; chép vào bảng này sau khi nhóm duyệt.
