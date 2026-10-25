# Bộ câu hỏi gán nhãn — người gán thứ hai (Hoàng Minh Đức)

**Mục đích:** viết **đáp án tham chiếu** cho 11 ca bất thường: theo nguồn bên ngoài, nguyên nhân thật của từng bất thường là gì. Đáp án này dùng để chấm hệ thống. Nhóm cần **hai người gán độc lập** để tính độ đồng thuận (Cohen's kappa); ca nào hai người khác nhau sẽ do Nguyễn Xuân Hải quyết.

## Quy tắc bắt buộc (đọc trước khi làm)

1. **Làm độc lập.** KHÔNG xem nhãn của Nguyễn Tùng Dương (`data/eval/labels/nguyen_tung_duong.yaml`), không hỏi Dương đã chọn gì, không trao đổi về đáp án cho tới khi cả hai làm xong.
2. **Không xem kết quả của hệ thống**: không mở demo (Streamlit, `outputs/demo/`), `outputs/`, `summary.md`, không chạy `run_eval.py`. Đáp án phải đến từ nguồn bên ngoài, không phải từ hệ thống.
3. **Không hỏi LLM (ChatGPT, Gemini, Claude…) "nguyên nhân là gì".** LLM chỉ được dùng để chép câu trả lời của bạn thành file (xem cuối tài liệu).
4. Con số mô tả mỗi ca (ví dụ "tồn kho tăng ~249%") chỉ để bạn **biết ca nào**. Đó là số do hệ thống tính: **không dùng làm căn cứ** cho đáp án.
5. **Chọn từ danh sách giả thuyết của đúng loại ca.** Được chọn nhiều mã. Không mã nào khớp thì ghi `other` kèm mô tả 1–2 câu (bắt buộc).
6. Mỗi mã nên có **ít nhất một câu trích nguyên văn** từ nguồn, kèm tên nguồn và trang (nếu có).

## Nguồn được dùng

- ✅ Báo cáo thường niên (BCTN), báo cáo phân tích của công ty chứng khoán (bất kỳ công ty chứng khoán nào), tin tức (CafeF, Vietstock, VnEconomy…), giải trình kết quả kinh doanh, tài liệu ĐHĐCĐ, **BCTC từ năm 2024 trở đi**.
- ❌ **KHÔNG dùng BCTC hợp nhất của HPG, HSG, NKG các năm 2015–2023.** Hệ thống đã đọc các tài liệu này; dùng lại thì đáp án không còn độc lập.
- **Ưu tiên nguồn công bố SAU khi kỳ kết thúc.** Nguồn ra giữa kỳ chỉ thấy một phần năm: dùng được nhưng ghi chú lại.
- **HSG có năm tài chính kết thúc 30/9**: FY2016 = 01/10/2015–30/09/2016; FY2017 = 01/10/2016–30/09/2017; FY2018 = 01/10/2017–30/09/2018; FY2021 = 01/10/2020–30/09/2021; FY2022 = 01/10/2021–30/09/2022. HPG và NKG: năm dương lịch.

## Danh sách giả thuyết

**A. Tồn kho TĂNG** (dùng cho C01, C02, C04, C05, C07)
- **INV-1:** Tồn kho tăng do doanh nghiệp tích trữ nguyên vật liệu khi giá đầu vào ở vùng thấp.
- **INV-2:** Tồn kho tăng do chuẩn bị mở rộng sản xuất.
- **INV-3:** Tồn kho tăng do hàng bán chậm, nhu cầu yếu.
- **INV-4:** Tồn kho tăng do yếu tố mùa vụ.
- **INV-5:** Giá trị tồn kho tăng do giá đầu vào tăng, không phải số lượng tăng.
- **INV-6:** Biến động tồn kho do thay đổi chính sách kế toán hàng tồn kho.
- **INV-7:** Tồn kho tăng do hàng mua đang đi đường hoặc thời điểm nhập hàng sát cuối kỳ.

**B. Tồn kho GIẢM** (dùng cho C09, C10)
- **DEC-1:** Tồn kho giảm do nhu cầu mạnh, bán vượt kỳ vọng.
- **DEC-2:** Tồn kho giảm do doanh nghiệp chủ động giảm tồn kho để hạn chế rủi ro giá giảm và giải phóng vốn lưu động.
- **DEC-3:** Tồn kho giảm do cắt giảm sản xuất khi nhu cầu yếu.
- **DEC-4:** Giá trị tồn kho giảm do giá đầu vào giảm, số lượng không đổi.
- **DEC-5:** Tồn kho ròng giảm do trích lập dự phòng giảm giá hàng tồn kho.
- **DEC-6:** Tồn kho giảm do doanh nghiệp xả hàng, bán giảm giá để giải phóng tồn kho giá cao.
- **DEC-7:** Tồn kho giảm do hàng mua đang đi đường giảm hoặc thời điểm nhập hàng cuối kỳ.
- **INV-4:** Biến động tồn kho do yếu tố mùa vụ.
- **INV-6:** Biến động tồn kho do thay đổi chính sách kế toán hàng tồn kho.

**C. Biên lợi nhuận gộp TĂNG** (dùng cho C06, C11)
- **GM-1:** Biên gộp thay đổi do giá bán thép thay đổi.
- **GM-2:** Biên gộp thay đổi do giá nguyên liệu đầu vào thay đổi, có độ trễ.
- **GM-3:** Biên gộp thay đổi do hàng tồn kho mua giá thấp (khi giá tăng) hoặc giá cao (khi giá giảm).
- **GM-4:** Biên gộp thay đổi do trích lập hoặc hoàn nhập dự phòng giảm giá hàng tồn kho.
- **GM-5:** Biên gộp thay đổi do cơ cấu sản phẩm hoặc thị trường (nội địa/xuất khẩu) thay đổi.
- **GM-6:** Biên gộp thay đổi do công suất sử dụng thay đổi (chi phí cố định phân bổ trên sản lượng).
- **GM-8:** Biên gộp thay đổi do biện pháp phòng vệ thương mại hoặc biến động tỷ giá.

**D. Biên lợi nhuận gộp GIẢM** (dùng cho C03, C08): như danh sách C, **thêm**
- **GM-7:** Biên gộp giảm do khấu hao tăng khi dự án mới đi vào vận hành.

**Trạng thái (không bắt buộc), chỉ ghi cho các giả thuyết của thư viện (INV/DEC/GM), không ghi cho `other`:**
- `SUPPORTED`: nguồn ủng hộ rõ;
- `PARTIAL`: ủng hộ một phần;
- `CONTRADICTED`: nguồn bác bỏ;
- `UNVERIFIED`: không đủ thông tin.

---

## Phần 1 — 7 ca TEST (làm trước)

### Câu D1 — C03: HPG (Hòa Phát), FY2022 (01/01–31/12/2022), biên lợi nhuận gộp GIẢM
Biên gộp giảm khoảng 15,6 điểm % so với năm 2021. Danh sách: **D**.
```
mã: ...
nguồn: tên, ngày (yyyy-mm-dd), link, trang
trích câu chính: mã: "...", mã: "..."
trạng thái (tùy chọn): mã = ...
other (nếu có): "..."
```

### Câu D2 — C04: NKG (Thép Nam Kim), FY2017 (01/01–31/12/2017), tồn kho TĂNG
Tồn kho tăng khoảng 101% so với năm 2016. Danh sách: **A**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D3 — C07: HPG (Hòa Phát), FY2021 (01/01–31/12/2021), tồn kho TĂNG
Tồn kho tăng khoảng 60% so với năm 2020. Danh sách: **A**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D4 — C08: HSG (Hoa Sen), FY2022 (01/10/2021–30/09/2022), biên lợi nhuận gộp GIẢM
Biên gộp giảm khoảng 8,3 điểm % so với niên độ trước. Danh sách: **D**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D5 — C09: HSG (Hoa Sen), FY2018 (01/10/2017–30/09/2018), tồn kho GIẢM
Tồn kho giảm khoảng 26% so với niên độ trước. Danh sách: **B**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D6 — C10: NKG (Thép Nam Kim), FY2018 (01/01–31/12/2018), tồn kho GIẢM
Tồn kho giảm khoảng 41% so với năm 2017. Danh sách: **B**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D7 — C11: NKG (Thép Nam Kim), FY2021 (01/01–31/12/2021), biên lợi nhuận gộp TĂNG
Biên gộp tăng khoảng 7,6 điểm % so với năm 2020. Danh sách: **C**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

---

## Phần 2 — 4 ca DEV

### Câu D8 — C01: NKG (Thép Nam Kim), FY2021 (01/01–31/12/2021), tồn kho TĂNG
Tồn kho tăng khoảng 249% so với năm 2020. Danh sách: **A**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D9 — C02: HSG (Hoa Sen), FY2021 (01/10/2020–30/09/2021), tồn kho TĂNG
Tồn kho tăng khoảng 124% so với niên độ trước. Danh sách: **A**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D10 — C05: HSG (Hoa Sen), FY2017 (01/10/2016–30/09/2017), tồn kho TĂNG
Tồn kho tăng khoảng 84% so với niên độ trước. Danh sách: **A**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

### Câu D11 — C06: HSG (Hoa Sen), FY2016 (01/10/2015–30/09/2016), biên lợi nhuận gộp TĂNG
Biên gộp tăng khoảng 8,6 điểm % so với niên độ trước. Danh sách: **C**.
```
mã: ...
nguồn: ...
trích câu chính: ...
trạng thái (tùy chọn): ...
other (nếu có): ...
```

---

## Nộp câu trả lời

Có hai cách:

**Cách 1 (khuyến nghị):** mở **một phiên Claude Code MỚI** trong thư mục dự án, câu đầu tiên dán nguyên văn:

> Tôi là Hoàng Minh Đức, người gán nhãn thứ hai (Phase 12a). KHÔNG đọc file `data/eval/labels/nguyen_tung_duong.yaml` và không xem `outputs/`. Tôi sẽ dán câu trả lời từng ca theo `data/eval/questionnaire_annotator2.md`; chỉ chép thành `data/eval/labels/hoang_minh_duc.yaml` theo mẫu `data/eval/templates/labels.yaml`, không gợi ý giả thuyết, rồi chạy `python scripts/eval_tools.py check`.

Sau đó dán câu trả lời từng câu D1, D2…

**Cách 2:** điền thẳng vào file này (thay các dấu `...`), lưu thành `questionnaire_hoang_minh_duc.md` rồi gửi lại. Người chép thành YAML **không được** là Nguyễn Tùng Dương.

Chép xong, lệnh `python scripts/eval_tools.py check` tính kappa và liệt kê các ca bất đồng. Nguyễn Xuân Hải giải quyết các ca đó trong `data/eval/adjudication.yaml` (mẫu: `data/eval/templates/adjudication.yaml`).
