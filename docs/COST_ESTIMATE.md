# Ước tính chi phí chạy đánh giá bằng model tính phí

_Sinh bởi `scripts/estimate_cost.py` ngày 2026-10-25. Không gọi API. Giá trong `configs/cost/pricing.yaml` (nguồn và ngày tra ghi trong file). Đây là ước tính để cân nhắc, không phải hóa đơn._

**Đánh giá chính thức dùng `gemini-3.5-flash-lite` free tier: chi phí thật = 0 đồng** (giới hạn số lời gọi/phút, có thể gặp lỗi 429/503 và phải chạy lại). Bảng dưới trả lời câu hỏi: nếu chạy CÙNG khối lượng bằng model tính phí thì tốn bao nhiêu.

## Khối lượng

| Tập | Số ca | Lần chạy/ca | Cấu hình | Token vào (thấp–giữa–cao) | Token ra (thấp–giữa–cao) |
|---|---|---|---|---|---|
| dev | 4 | 3 | 8 | 574,390 – 765,853 – 919,024 | 417,120 – 834,240 – 1,668,480 |
| test | 7 | 3 | 8 | 1,057,275 – 1,409,700 – 1,691,641 | 741,840 – 1,483,680 – 2,967,360 |

Token theo cấu hình (mức giữa, cả hai tập):

| Cấu hình | Token vào | Token ra |
|---|---|---|
| A | 29,942 | 108,900 |
| B | 135,951 | 108,900 |
| C | 1,194,484 | 1,453,980 |
| A_lib | 32,986 | 21,780 |
| B_lib | 138,632 | 21,780 |
| no_rag | 0 | 0 |
| no_verification | 150,117 | 21,780 |
| no_library | 493,441 | 580,800 |

## Chi phí ước tính (USD, mức giữa; khoảng thấp–cao trong ngoặc)

| Model | Giá vào / ra ($/1M) | Tập dev | Tập test | Tổng | Ghi chú |
|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 0.30 / 2.50 | $2.32 | $4.13 | **$6.45** ($3.39–$12.37) | model hiện tại; free tier = 0 đồng (giới hạn lời gọi/phút) |
| `gemini-3.1-flash-lite` | 0.25 / 1.50 | $1.44 | $2.58 | **$4.02** ($2.15–$7.61) | Có free tier. |
| `gemini-3.8-flash` | 0.75 / 3.75 | $3.70 | $6.62 | **$10.32** ($5.57–$19.34) | giá giới thiệu; tăng lên 1,50 / 7,50 từ 2027-01-01 Có free tier. |
| `gemini-3.1-pro-preview` | 2.00 / 12.00 | $11.54 | $20.62 | **$32.17** ($17.17–$60.85) | prompt <= 200K token |
| `gemini-4-argon` | 2.00 / 10.00 | $9.87 | $17.66 | **$27.53** ($14.85–$51.58) | giá giới thiệu; tăng lên 4,00 / 20,00 từ 2026-12-01 |
| `claude-haiku-5-5` | 0.10 / 0.50 | $0.49 | $0.88 | **$1.38** ($0.74–$2.58) | prompt <= 100K token |
| `claude-sonnet-5-5` | 2.00 / 10.00 | $9.87 | $17.66 | **$27.53** ($14.85–$51.58) |  |
| `claude-opus-5-5` | 4.00 / 20.00 | $19.75 | $35.31 | **$55.06** ($29.71–$103.16) |  |
| `claude-fable-5-1` | 10.00 / 50.00 | $49.37 | $88.28 | **$137.65** ($74.26–$257.90) |  |

## Giả định

- Token vào: dựng đúng prompt runner sẽ gửi trên dữ liệu thật của từng ca; đổi ký tự → token với 4.0 (thấp) / 3.0 (giữa) / 2.5 (cao) ký tự mỗi token (tiếng Việt có dấu tốn token hơn tiếng Anh; mỗi hãng một tokenizer).
- C và no_library: đoạn truy xuất bằng truy vấn gốc (thật còn thêm truy vấn diễn đạt lại), báo cáo dựng từ phân tích chưa có bằng chứng văn bản: có thể THIẾU một ít.
- Token ra nhìn thấy mỗi lời gọi: {"baseline_free": 1500, "ranking": 300, "generate": 500, "rephrase_queries": 300, "classify_passages": 1200, "grounded_report": 2500}; token suy luận ẩn = 1.0 × token ra (tính tiền như token ra); mức thấp/cao nhân token ra với 0.5 / 2.0.
- Báo cáo của C viết trung bình 2.0 lần; hệ số chạy lại vì lỗi 1.1.
- Không tính prompt caching và Batch API (cả hai hãng giảm ~50% khi chạy batch); runner hiện gọi tuần tự.
- Chưa tính chạy thử/chỉnh trên dev nhiều vòng: mỗi vòng chỉnh prompt trên dev tốn thêm một lần cột "Tập dev".

_Kết quả là ước tính phục vụ khảo sát khả thi, không phải con số chính xác._
