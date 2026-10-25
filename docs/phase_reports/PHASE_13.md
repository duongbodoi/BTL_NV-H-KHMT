# Báo cáo Phase 13 — Demo

- **Ngày:** 2026-10-25
- **Thực hiện:** Claude Code (Claude Opus 5.5), dưới sự kiểm tra của nhóm
- **Spec:** mục 13, 20 (Phase 13), 3.4, 23
- **Trạng thái:** code xong, test bằng dữ liệu tổng hợp và chạy được trên kết quả thật của C01 (dev, run1). Qua 1 vòng review độc lập, mọi lỗi HIGH/MEDIUM/LOW đã sửa (mục 5).

## Phase này để làm gì?

Trình diễn hệ thống cho hội đồng: một ca được hiển thị theo đúng 4 bước, gồm bất thường → giả thuyết và trạng thái kiểm chứng → bằng chứng có trích dẫn, kèm phần còn thiếu → giải thích cuối. Trang có dòng "Hỗ trợ phân tích, không phải tư vấn đầu tư". Spec ghi rõ demo không phải mục tiêu nghiên cứu chính.

## 1. Đã làm được gì

| Thành phần | Mô tả |
|---|---|
| `demo/view.py` | Dựng `CaseView` từ kết quả ĐÃ chạy ở Phase 12: `RunRecord` của C (xếp hạng, verdict, báo cáo), A/B để so sánh, chỉ báo từ `anomaly_candidates.csv`, mã/nội dung giả thuyết từ thư viện. Giá trị check định dạng bằng cùng hàm số Việt với báo cáo (`report.numbers.display`). |
| `demo/text.py` + `scripts/demo.py` | Demo dòng lệnh, không cần Streamlit: `--list`, `--case C01 [--run run1] [--split dev]`. |
| `demo/app.py` | Demo web Streamlit: chọn tập/ca/lần chạy; chỉ báo (metric), bảng giả thuyết + trạng thái, chi tiết bằng chứng/trích dẫn (tài liệu, trang)/phần thiếu cho top-k, biểu đồ chuỗi giá mà check đã dùng, báo cáo cuối, so sánh A/B. Dòng miễn trừ ở đầu và cuối trang. Đáp án tham chiếu chỉ hiện khi bật và đã có đáp án CUỐI. |

**Không gọi API khi trình diễn**: demo chỉ đọc file. Lúc trình bày không phụ thuộc mạng hay hạn mức free tier, và mọi thứ hiển thị đúng là thứ đã được đo trong đánh giá.

## 2. File

- **Tạo:** `src/steel_xai/demo/{view,text,app}.py`, `scripts/demo.py`, `tests/test_demo.py`, file này.
- **Sửa:** `src/steel_xai/demo/__init__.py`, `pyproject.toml` (extra `demo = ["streamlit>=1.50,<2"]`), `README.md` (mục demo).

## 3. Quyết định thiết kế

- **Đọc kết quả đánh giá thay vì chạy pipeline trực tiếp:** demo và bảng metric dùng cùng một nguồn số liệu, nên hội đồng thấy đúng ca đã được chấm. Muốn có ca mới thì chạy `run_eval.py run` trước.
- **Streamlit là phụ thuộc tùy chọn:** CLI vẫn chạy khi chưa cài Streamlit; test app tự bỏ qua nếu thiếu Streamlit.
- **Ngôn ngữ trình bày:** trạng thái giữ mã tiếng Anh (SUPPORTED…) kèm nghĩa tiếng Việt, để khớp với báo cáo và spec.

## 4. Tests

`tests/test_demo.py`: 13 test.
- Chỉ liệt kê lần chạy `ok`.
- Nội dung `CaseView`: số đã định dạng, trích dẫn có tài liệu/trang, phần thiếu, giả thuyết ngoài thư viện, chuỗi giá, baseline.
- Văn bản CLI có đủ mọi phần spec yêu cầu và dòng miễn trừ ở đầu và cuối.
- CLI `--list` / `--case` / ca không tồn tại.
- App Streamlit chạy qua `AppTest`, không lỗi, có đủ các mục.

Toàn repo: **1209 passed**, ruff sạch.

## 5. Review độc lập

1 vòng (agent riêng): 0 CRITICAL, 1 HIGH, 5 MEDIUM, 8 LOW. Reviewer xác nhận: demo **không gọi mạng** (chạy với socket bị chặn), lần chạy lỗi bị loại, đáp án tham chiếu chỉ hiện khi đã có đáp án cuối và người xem bật, ca đồng hạng hiển thị đúng.

| Mức | Phát hiện | Sửa |
|---|---|---|
| HIGH | Bằng chứng phản bác của giả thuyết ngoài top-3 bị ẩn (C01: 4/7 CONTRADICTED ở hạng 4–6), dễ hiểu nhầm là không có phản bác | luôn hiện mục phản bác cho mọi giả thuyết bị phản bác |
| MEDIUM | Chỉ báo đoán đơn vị theo tên (`105,5%` thay vì `105,5 điểm %`), lệch với báo cáo | dùng `indicator_units` + cùng hàm `display` với báo cáo; thêm nhãn tiếng Việt |
| MEDIUM | Check critical thất bại hiện ở cả "phản bác" và "kỳ vọng không xảy ra" | chỉ hiện ở mục phản bác |
| MEDIUM | Thư viện lệch với lúc chạy: số bị định dạng sai im lặng | nạp cả giả thuyết draft; không rõ đơn vị thì ghi "(không rõ đơn vị)" + cảnh báo |
| MEDIUM | Thiếu/lệch `anomaly_candidates.csv`: chỉ báo biến mất im lặng | cảnh báo hiện trên trang |
| MEDIUM | So sánh chỉ có A/B, không có C | thêm dòng C cùng chỉ số bản nháp đầu, ghi rõ "bản nháp đầu" |
| LOW | Nhãn "LLM viết…" khi báo cáo bị từ chối; baseline lỗi bị bỏ im lặng; gợi ý lệnh sai cho tập test; không hiện cờ dữ liệu; `nan%`; thiếu nhãn/đơn vị biểu đồ; trích dẫn bị Markdown đổi định dạng; `reconfigure` lỗi khi redirect | đã sửa tất cả |

Thêm 5 test cho các điểm trên (phản bác ngoài top, không lặp check, báo cáo bị từ chối, thiếu candidates/baseline hỏng, thư viện lệch, escape trích dẫn, tập test rỗng + đáp án tham chiếu trên app).

Ghi chú cho Phase 11 (chưa sửa, ngoài phạm vi demo): câu "không ghi nhận bằng chứng phản bác" trong báo cáo LLM chỉ đúng với top-k đưa vào báo cáo.

## 6. Known limitations

- Chỉ hiển thị ca đã chạy cấu hình C. Hiện mới có C01 (dev, run1).
- Kết quả C01 thật cho thấy RAG đóng góp ít: 2/11 check văn bản FOUND, 3 trích dẫn bị loại vì không khớp nguyên văn, xếp hạng của C trùng no_rag. Đây là việc chỉnh trên dev (truy vấn, số đoạn, prompt phân loại), không phải lỗi của demo.
- Biểu đồ chuỗi giá chỉ có khi check của giả thuyết dùng dữ liệu giá ngoài.

## 7. Chưa làm vì ngoài scope

Gói nộp (Phase 14). Chạy pipeline trực tiếp từ giao diện (cố ý không làm: phụ thuộc mạng/hạn mức lúc trình bày).

## 8. Hard-code công ty

Không. Test dùng mã giả AAA; danh sách chỉ báo theo loại bất thường.

## 9. AI_USAGE_LOG.md

Đã thêm mục "2026-10-25 — Phase 13".

## 10. Khác báo cáo giữa kỳ

Không.
