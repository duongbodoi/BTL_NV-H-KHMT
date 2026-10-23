# BẢN NHÁP — Thay đổi spec và DEVIATIONS (chờ nhóm duyệt)

> **Trạng thái: NHÁP.** Chưa sửa `VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md` và `DEVIATIONS_FROM_MIDTERM.md`. Nhóm duyệt/chỉnh nội dung dưới đây rồi mới chép vào hai file đó. Claude không tự sửa spec (spec mục 22).
>
> Căn cứ: các quyết định nhóm đã chốt cho Phase 3-4 — dữ liệu là BCTC **năm**, nguồn OCR ViFinQA, ba công ty HPG/HSG/NKG, so sánh theo năm (YoY), giả thuyết mùa vụ không kiểm được bằng dữ liệu năm.
>
> Lưu ý quan trọng: spec mục 0 nói **báo cáo giữa kỳ thắng** nếu spec mâu thuẫn với giữa kỳ, trừ các điểm trong mục 26.2. Các thay đổi dưới đây (BCTC quý → BCTC năm, bỏ QoQ) phải được nhóm **đối chiếu với báo cáo giữa kỳ thực tế** (Claude không có báo cáo giữa kỳ). Nếu giữa kỳ ghi "quý", đây là điểm lệch quan trọng cần giải trình với giảng viên.

---

## A. Đề xuất sửa spec

### A.1. Mục 3.3 — Công ty và thời gian

**Hiện tại (rút gọn):** "Một nhóm nhỏ công ty niêm yết ngành thép (danh sách chốt trong config)... Giai đoạn gốc: 2021–2023. Dữ liệu từ 2024 trở đi chỉ dùng làm nguồn đáp án tham chiếu..."

**Đề xuất:**

> - Nhóm công ty: **HPG, HSG, NKG** (chốt trong `configs/dataset.yaml`, không trong code).
> - Dữ liệu là **BCTC hợp nhất theo năm tài chính**, không phải quý. Một "kỳ" là một năm tài chính, ký hiệu `FY<năm>` kèm `fiscal_period_end` (ngày kết thúc năm tài chính). HSG có năm tài chính kết thúc 30/9, HPG và NKG kết thúc 31/12.
> - Giai đoạn gốc: các năm tài chính 2021–2023. Dữ liệu từ năm tài chính 2024 trở đi chỉ làm nguồn đáp án tham chiếu (diễn biến kỳ sau), không đưa vào kho RAG và input của bất kỳ cấu hình nào. Mốc năm nằm trong config.
> - Mốc as-of của mỗi ca là ngày công bố BCTC năm của năm bất thường.
> - So sánh giữa các công ty (peer) ghép theo năm tài chính `FY`; khi `fiscal_period_end` khác nhau giữa các công ty thì **gắn cờ** (`peer_fiscal_end_differs`), không coi là cùng thời điểm.

### A.2. Mục 7.1 — Nguồn dữ liệu và thứ tự ưu tiên

**Đề xuất sửa dòng đầu của bảng và quy tắc ưu tiên:**

| Nhóm | Nguồn | Dùng cho |
|---|---|---|
| BCTC năm hợp nhất (OCR) | bộ OCR ViFinQA tại `data/raw/vifinqa` (không commit) | **nguồn chính** cho anomaly engine, tỷ số, tăng trưởng YoY, baseline lịch sử, peer; đồng thời là nguồn của kho RAG (thuyết minh) |
| Thuyết minh, báo cáo thường niên | cùng bộ OCR | kho RAG; thành phần tồn kho, XDCB dở dang, capex |
| Giá quặng sắt, giá thép | như hiện tại | như hiện tại (độ trễ tính theo năm) |

> **Không có nguồn dữ liệu có cấu trúc.** Số liệu lấy từ OCR; vì vậy mọi số phải qua kiểm tra Phase 4 (quan hệ kế toán, đối chiếu mẫu với BCTC gốc). Nhánh đọc CSV có cấu trúc vẫn còn trong code nhưng **không dùng**; quy tắc "có cấu trúc > OCR" chỉ áp dụng nếu nhánh đó được bật sau này.
>
> **Số bị điều chỉnh ở năm sau:** giá trị chính của năm N lấy từ báo cáo năm N. Số so sánh về năm N trong báo cáo năm N+1 được lưu riêng; nếu khác thì fact chính gắn cờ `restated_later` (không chuyển thành `UNRESOLVED`). Chỉ khi không có báo cáo năm N thì dùng số so sánh, kèm cờ `comparative_only`.
>
> Hai nguồn/hai tài liệu cùng năm báo cáo mà lệch nhau, hoặc một nơi ghi "-" còn nơi khác có số, vẫn là `UNRESOLVED` (giữ nguyên spec).

### A.3. Mục 8.3 (và 8.1) — Phương pháp phát hiện bất thường

**Hiện tại:** "So cùng kỳ năm trước (YoY) trước, rồi mới so kỳ liền trước (QoQ)... robust z-score/MAD khi đủ kỳ."

**Đề xuất:**

> - Chỉ so **YoY theo năm tài chính**; bỏ QoQ (dữ liệu năm). `configs/anomaly.yaml: compare_order` đã đặt `[yoy]`.
> - Baseline lịch sử: mỗi công ty có tối đa vài năm trong 2021–2023 (kèm số so sánh của năm trước đó nếu có), nên **hầu hết ca sẽ có cờ `weak_baseline`**; không giả vờ có z-score/MAD khi không đủ kỳ. Dùng luật YoY (ngưỡng hai phía) làm cơ sở.
> - So với nhóm cùng ngành: chỉ có hai công ty còn lại làm peer; thiếu peer thì `MISSING`. Peer ghép theo năm tài chính, gắn cờ khi `fiscal_period_end` khác nhau.
> - Số ngày tồn kho (8.1): "số ngày trong kỳ" lấy từ độ dài năm tài chính (khoảng cách `fiscal_period_end` giữa hai năm), không mặc định 365.

### A.4. Các mục khác bị ảnh hưởng (nhóm cân nhắc)

- **Mục 9.2 (thư viện giả thuyết) và RQ3:** giả thuyết **"Mùa vụ"** (tồn kho) **không kiểm được bằng dữ liệu năm**: không có chuỗi quý để thấy mùa vụ. Đề xuất giữ trong thư viện nhưng ghi rõ `critical_missing_evidence` là "cần dữ liệu quý"; kết quả kỳ vọng là `UNVERIFIED` và đưa vào bảng RQ3 như một ví dụ có chủ ý. Nhóm quyết định giữ hay bỏ.
- **Mục 15 (đánh giá):** "kỳ sau" của ca cuối 2023 là năm 2024 (nguồn đáp án), giữ nguyên.
- **Mục 28 (trạng thái):** danh sách trạng thái phase đã lỗi thời; cập nhật sau khi nhóm đánh dấu Phase 0-4.
- **Mục 6, 8.1:** các chỗ nhắc "quý" cần rà lại cho nhất quán.

---

## B. Đề xuất thêm vào `DEVIATIONS_FROM_MIDTERM.md`

Mẫu theo bảng ở spec mục 26.2. Cột "Giữa kỳ" **nhóm điền theo báo cáo giữa kỳ thực tế**; Claude chỉ ghi nội dung đã biết từ spec v2.

| Điểm | Giữa kỳ | Bản này | Lý do |
|---|---|---|---|
| Tần suất dữ liệu | *(nhóm điền; spec v2 viết "BCTC theo quý nhiều kỳ")* | BCTC **năm** hợp nhất | nguồn khả dụng là bộ OCR ViFinQA theo năm; không có nguồn quý |
| Nguồn số liệu chính | *(nhóm điền; spec v2 viết "vnstock hoặc xuất từ nguồn công khai")* | OCR ViFinQA tại `data/raw/vifinqa`; không có nguồn có cấu trúc | không có nguồn có cấu trúc; bù bằng kiểm tra Phase 4 và đối chiếu mẫu với BCTC gốc (N = 20) |
| Công ty | *(nhóm điền)* | HPG, HSG, NKG | cỡ mẫu nhỏ; HSG có năm tài chính kết thúc 30/9 nên so peer phải ghép theo năm tài chính và gắn cờ |
| So sánh bất thường | YoY rồi QoQ (spec 8.3) | chỉ YoY theo năm tài chính | dữ liệu năm; hệ quả: nhiều ca `weak_baseline`, ít peer |
| Giả thuyết mùa vụ | có trong danh sách khởi điểm | không kiểm được bằng dữ liệu năm → `UNVERIFIED` | không có chuỗi quý; đưa vào phân tích RQ3 |
| Số điều chỉnh ở năm sau | chưa nêu | giá trị chính lấy từ báo cáo năm N; số so sánh lưu riêng; khác thì cờ `restated_later` | tránh biến mọi số trình bày lại thành `UNRESOLVED`, vẫn minh bạch |

### Điểm nhóm cần tự đối chiếu trước khi chép vào DEVIATIONS

1. Báo cáo giữa kỳ thực sự viết "quý" hay "năm"? Nếu "quý", dòng đầu là lệch lớn cần lý do chính thức.
2. Danh sách công ty trong giữa kỳ có phải HPG/HSG/NKG không?
3. Giả thuyết mùa vụ có nằm trong 6 giả thuyết tồn kho của giữa kỳ không?
