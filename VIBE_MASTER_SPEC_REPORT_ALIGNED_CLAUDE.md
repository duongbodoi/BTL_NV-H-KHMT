# VIBE MASTER SPEC v2 — BÁM SÁT BÁO CÁO GIỮA KỲ NHÓM 8

> Phiên bản v2: đã tích hợp "Giải pháp chi tiết" đã chốt (đo lường chặt hơn, chống rò rỉ, minh bạch AI, gói nộp cuối kỳ). Không đổi bài toán, ngành, 4 bước, 3 cấu hình hay RQ của báo cáo giữa kỳ.

## 0. Vai trò của tài liệu này

Tài liệu này là **single source of truth** cho quá trình vibe-code dự án cuối kỳ.

Claude phải đọc file này trước khi sửa code.

Mọi quyết định triển khai phải ưu tiên theo thứ tự:

1. Phạm vi và mục tiêu đã nêu trong báo cáo giữa kỳ.
2. Khả năng kiểm chứng và tái lập kết quả.
3. Kiến trúc kỹ thuật tối thiểu cần thiết để hiện thực hóa báo cáo.
4. Không tự mở rộng đề tài nếu không có yêu cầu mới.

Nếu code hiện tại mâu thuẫn với tài liệu này, phải báo rõ trước khi sửa.

Nếu spec này mâu thuẫn với báo cáo giữa kỳ, **báo cáo giữa kỳ thắng**, trừ các điểm đã được liệt kê trong mục 26.2 (danh sách điểm khác giữa kỳ đã chốt).

---

# 1. Bài toán nghiên cứu

## 1.1. Bối cảnh

Dự án thuộc chủ đề Fintech — phân tích báo cáo tài chính.

Điểm nghẽn không chỉ là đọc/tóm tắt BCTC. Một con số bất thường chỉ là triệu chứng; cùng một bất thường có thể có nhiều cách giải thích khác nhau tùy mô hình kinh doanh và tri thức ngành.

Ví dụ:
- tồn kho tăng mạnh có thể xấu vì hàng ế;
- cũng có thể tốt vì doanh nghiệp tích trữ nguyên liệu giá thấp;
- cũng có thể do chuẩn bị mở rộng sản xuất;
- cũng có thể chỉ là mùa vụ;
- hoặc do giá tăng chứ lượng không tăng;
- hoặc do thay đổi chính sách kế toán.

Hệ thống không được chỉ tạo một lời giải thích phù hợp với bất thường.

Mục tiêu là:
**đưa ra các giả thuyết cạnh tranh và kiểm tra từng giả thuyết bằng dữ liệu/bằng chứng thực tế.**

## 1.2. Phát biểu bài toán hình thức

- **Input:** một bất thường (công ty, kỳ, loại, các chỉ báo đã tính) + BCTC theo quý nhiều kỳ + thuyết minh/báo cáo thường niên + giá quặng sắt và giá thép.
- **Output:** danh sách giả thuyết đã xếp hạng; mỗi giả thuyết có trạng thái, bằng chứng, phản bác, phần còn thiếu, trích dẫn; báo cáo giải thích.
- **Biến mục tiêu:** giả thuyết đúng theo đáp án tham chiếu (có thể là một **tập** giả thuyết, vì một bất thường có thể có nhiều nguyên nhân cùng đúng).
- **Giả định:**
  - bất thường đã được bước 1 gắn cờ;
  - nguồn bằng chứng chỉ là dữ liệu công khai;
  - thư viện giả thuyết bao phủ các nguyên nhân thường gặp; nguyên nhân ngoài thư viện là giới hạn đã biết, không bị ép vào giả thuyết có sẵn;
  - BCTC có thể bị điều chỉnh lại sau khi công bố.

---

# 2. Hai tầng của bài toán

## Tầng 1 — Phát hiện bất thường

Phát hiện sự lệch:
- so với lịch sử doanh nghiệp;
- so với nhóm cùng ngành;
- so với quan hệ giữa các khoản mục.

Đây là phần tương đối dễ và xử lý chủ yếu bằng code.

## Tầng 2 — Giải thích và xác thực bất thường

Với một bất thường:
- xác định các biến ẩn nghiệp vụ có thể chi phối;
- tải các giả thuyết nghiệp vụ phù hợp từ thư viện;
- xác định nếu giả thuyết đúng thì phải quan sát được gì;
- tìm bằng chứng;
- tìm bằng chứng phản bác;
- ghi rõ phần chưa xác thực được;
- kết luận có điều kiện.

**Đề tài tập trung chủ yếu vào tầng 2.**

---

# 3. Phạm vi bắt buộc

## 3.1. Ngành

Phạm vi thực nghiệm: **ngành thép.**

Không mở rộng sang ngân hàng, bất động sản, bảo hiểm hoặc ngành khác trong MVP cuối kỳ. Không biến đồ án thành hệ thống "mọi ngành".

## 3.2. Loại bất thường

Chỉ hai loại:

1. Tồn kho tăng bất thường.
2. Biên lợi nhuận gộp thay đổi mạnh.

Cả hai loại xét **hai chiều** (tăng và giảm). Không thêm loại anomaly khác.

## 3.3. Công ty và thời gian

- Một nhóm nhỏ công ty niêm yết ngành thép (danh sách chốt trong config, không trong code).
- Dùng nhiều kỳ để xây baseline lịch sử.
- Giai đoạn gốc: 2021–2023.
- **Dữ liệu từ 2024 trở đi chỉ dùng làm nguồn đáp án tham chiếu** (diễn biến kỳ sau), **không đưa vào kho RAG và không đưa vào input của bất kỳ cấu hình nào**. Không hard-code năm trong business logic; mốc năm nằm trong dataset/evaluation config.

## 3.4. Ngoài phạm vi

Hệ thống KHÔNG:
- dự báo giá cổ phiếu;
- khuyến nghị mua/bán;
- kết luận gian lận;
- tự động khẳng định quan hệ nhân quả khi bằng chứng không đủ.

---

# 4. Đầu ra mong muốn

Với **một bất thường cụ thể của một doanh nghiệp trong ngữ cảnh ngành**, hệ thống phải trả:

1. Bất thường được phát hiện.
2. Danh sách giả thuyết giải thích.
3. Xếp hạng giả thuyết.
4. Bằng chứng ủng hộ từng giả thuyết.
5. Bằng chứng phản bác nếu có.
6. Phần dữ liệu/bằng chứng còn thiếu.
7. Trạng thái xác thực.
8. Trích dẫn nguồn.
9. Báo cáo giải thích cuối cùng.

"Chưa xác thực" là output hợp lệ. Không được buộc hệ thống phải luôn có câu trả lời.

---

# 5. Câu hỏi nghiên cứu

## RQ chính

Việc kết hợp:
- thư viện giả thuyết nghiệp vụ;
- RAG bắt buộc trích dẫn;
- kiểm chứng bằng code;

có giúp hệ thống giải thích bất thường tốt hơn và trung thực hơn so với LLM thuần và LLM + RAG đơn thuần hay không?

## RQ1
Giả thuyết đúng có xuất hiện ở top-1/top-3 thường xuyên hơn không?

## RQ2
Bắt buộc trích dẫn và kiểm tra bằng chứng có giảm giải thích bịa không?

## RQ3
Những loại giả thuyết nào không thể xác thực bằng dữ liệu công khai?

---

# 6. Kiến trúc nghiên cứu chính thức

Kiến trúc PHẢI bám đúng 4 bước trong báo cáo.

```text
BCTC + thuyết minh + giá quặng/thép
        |
        v
[1] PHÁT HIỆN BẤT THƯỜNG        (code, deterministic, không LLM)
        |
        v
Anomaly
        |
        v
[2] THƯ VIỆN GIẢ THUYẾT NGHIỆP VỤ   (viết tay, có nguồn)
        |
        v
Candidate hypotheses + check list
        |
        v
[3] KIỂM CHỨNG
   |                          |
Code định lượng          RAG trên thuyết minh/BCTC
(mọi con số do code)     (trích dẫn nguyên văn)
   |                          |
   +------------+-------------+
                |      LLM chỉ phân loại bằng chứng
                v
   Evidence per hypothesis
                |
                v
SUPPORTED / PARTIAL / CONTRADICTED / UNVERIFIED  + xếp hạng
                |
                v
[4] BÁO CÁO
LLM chỉ viết lại kết quả đã xác thực
```

Nguyên tắc: **mọi con số do code tính.** LLM không được là nguồn sự thật cho số liệu.

---

# 7. Dữ liệu

## 7.1. Nguồn và thứ tự ưu tiên

| Nhóm | Nguồn | Dùng cho |
|---|---|---|
| BCTC quý có cấu trúc | thư viện vnstock hoặc xuất từ nguồn công khai | **nguồn chính** cho anomaly engine, tỷ số, tăng trưởng, baseline lịch sử, peer |
| Thuyết minh BCTC, báo cáo thường niên (PDF/OCR) | tài liệu công bố của công ty được chọn | kho RAG; trích thành phần tồn kho, XDCB dở dang, capex mà dữ liệu có cấu trúc không có |
| Giá quặng sắt, giá thép | World Bank Pink Sheet (quặng sắt); giá thép từ nguồn công khai, chốt khi thu thập | check giả thuyết chi phí đầu vào/giá bán, có độ trễ |

Quy tắc ưu tiên: dữ liệu có cấu trúc > số trích từ OCR. Hai nguồn mâu thuẫn → fact ở trạng thái `UNRESOLVED` kèm cờ, không tự chọn im lặng.

Chỉ thêm nguồn thực sự cần cho giả thuyết đã định nghĩa.

## 7.2. Chống rò rỉ (bắt buộc)

- Mỗi ca có **mốc as-of = ngày công bố BCTC của kỳ bất thường**.
- Kho RAG, input của baseline A/B, dữ liệu giá nguyên liệu chỉ gồm thông tin **công bố đến mốc as-of**. Retriever phải ép lọc theo as-of trong code (không dựa vào prompt).
- Đáp án tham chiếu lấy từ nguồn **độc lập với kho RAG**: diễn biến kỳ sau, báo cáo phân tích bên ngoài, nguồn công khai khác. Mỗi nhãn phải ghi nguồn.
- Có test tự động chứng minh tài liệu sau mốc as-of không bao giờ được truy xuất.

## 7.3. Chất lượng dữ liệu

- Kiểm tra kỳ, đơn vị (đồng/triệu đồng), tổng thành phần, quan hệ kế toán cơ bản.
- Thiếu dữ liệu → `MISSING`/`UNKNOWN`, **không đặt 0**.
- Ngoại lai: robust z-score (MAD). Không loại giá trị lệch lớn (có thể chính là bất thường); chỉ gắn cờ lỗi dữ liệu khi vi phạm kiểm tra kế toán.
- Đối chiếu ngẫu nhiên N chỉ tiêu từ dữ liệu có cấu trúc với BCTC gốc, ghi tỷ lệ lệch (N do nhóm chọn, ghi trong config).

## 7.4. Data card

Cuối kỳ phải có `docs/DATA_CARD.md` gồm: số công ty, số kỳ, số trang tài liệu, số chunk, các trường chính, kết quả đối chiếu với BCTC gốc, thiên lệch (chỉ công ty niêm yết, một ngành, cỡ mẫu nhỏ, lỗi OCR, BCTC điều chỉnh lại, khác mức chi tiết thuyết minh) và giới hạn.

---

# 8. Bước 1 — Phát hiện bất thường

## 8.1. Inventory anomaly

Chỉ báo:
- tăng trưởng tồn kho (YoY và QoQ);
- số ngày tồn kho = tồn kho / giá vốn × số ngày trong kỳ;
- tồn kho / doanh thu;
- tăng trưởng tồn kho trừ tăng trưởng doanh thu;
- cơ cấu tồn kho (NVL, thành phẩm, dở dang) nếu có.

## 8.2. Gross margin anomaly

```text
gross_margin = gross_profit / net_revenue
```

Theo dõi:
- thay đổi so với kỳ trước và cùng kỳ năm trước (điểm %);
- độ lệch so với lịch sử công ty;
- độ lệch so với trung vị nhóm cùng ngành cùng kỳ.

## 8.3. Phương pháp

- So cùng kỳ năm trước (YoY) trước, rồi mới so kỳ liền trước (QoQ), để giảm báo động giả do mùa vụ.
- Historical baseline bằng z-score; robust z-score/MAD khi đủ kỳ.
- Rule-based relational checks.
- **Ngưỡng hai phía**: cờ khi lệch quá ngưỡng ở cả chiều tăng và chiều giảm. Bước này **không gán nhãn tốt/xấu**.
- Không đủ kỳ để ước lượng baseline đáng tin → dùng luật YoY/QoQ và gắn cờ `weak_baseline`; không giả vờ có z-score.
- Thiếu dữ liệu peer → `MISSING`, không bỏ qua im lặng.
- Không dùng ML anomaly detection phức tạp. Trên tập dev được phép so sánh ngưỡng cố định, z-score, MAD, có/không peer để chọn cấu hình.

## 8.4. Nguyên tắc

- deterministic, reproducible;
- threshold nằm trong `configs/anomaly.yaml`, không trong logic;
- không dùng LLM;
- báo số ca bị gắn cờ trên tổng số kỳ (tỷ lệ bất thường).

## 8.5. Output

Mỗi bất thường là một bản ghi `Anomaly` (xem mục 18).

---

# 9. Bước 2 — Thư viện giả thuyết nghiệp vụ

Mỗi loại bất thường có **6–8 giả thuyết viết tay**, **do nhóm soạn** từ báo cáo phân tích ngành, tài liệu chuyên môn, trao đổi với người am hiểu, case thực tế có nguồn.

LLM không phải nguồn tạo thư viện. Claude chỉ được dựng schema, loader, validator và bản nháp rỗng/placeholder gắn `status: draft`. Một giả thuyết chỉ được tính vào thư viện khi `status: reviewed` và có `reviewed_by` là người.

Thư viện dành riêng cho ngành thép, **không gắn với công ty cụ thể**.

## 9.1. Cấu trúc hypothesis

Trường bắt buộc (9 trường của báo cáo):

- id
- anomaly_type
- claim
- business_rationale
- expected_quantitative_evidence
- expected_textual_evidence
- contradictory_evidence
- required_external_data
- critical_missing_evidence

Trường bổ sung (metadata, không đổi nội dung báo cáo):

- `sources`: nguồn xây giả thuyết (bắt buộc khi `reviewed`)
- `status`: `draft` | `reviewed`
- `reviewed_by`
- `checks`: danh sách check có cấu trúc (mục 10.1) — để luật nằm trong dữ liệu, không nằm trong code

Ví dụ:

```yaml
id: inventory_capacity_expansion
anomaly_type: inventory_increase
claim: Tồn kho tăng do chuẩn bị mở rộng sản xuất.

expected_quantitative_evidence:
  - capex tăng
  - xây dựng cơ bản dở dang tăng
  - nguyên vật liệu tăng nếu được công bố

expected_textual_evidence:
  - dự án mở rộng công suất
  - nhà máy mới
  - commissioning / vận hành dự án

contradictory_evidence:
  - dự án bị trì hoãn hoặc hủy
  - capex giảm mạnh

required_external_data: []

critical_missing_evidence:
  - không có bằng chứng nối trực tiếp mở rộng công suất với tồn kho

sources: []
status: draft
```

## 9.2. Danh sách khởi điểm (nhóm duyệt/chỉnh)

Tồn kho tăng bất thường (6, có thể thêm 1–2):
1. Tích trữ nguyên liệu khi giá thấp
2. Chuẩn bị mở rộng sản xuất
3. Hàng ế, bán không được
4. Mùa vụ
5. Giá tăng chứ không phải lượng tăng
6. Thay đổi chính sách kế toán hàng tồn kho
- (ứng viên) thời điểm nhập hàng lệch kỳ

Biên lợi nhuận gộp thay đổi mạnh (6–8):
1. Giá bán thép thay đổi
2. Giá nguyên liệu đầu vào thay đổi (có độ trễ)
3. Thay đổi cơ cấu sản phẩm
4. Thay đổi sản lượng/công suất sử dụng
5. Khấu hao tăng do dự án mới vận hành
6. Trích lập/hoàn nhập dự phòng giảm giá hàng tồn kho
7. Thay đổi chính sách kế toán/phương pháp tính giá vốn
8. Chính sách thương mại, thuế, tỷ giá

Danh sách này là điểm xuất phát; nhóm chốt cuối.

---

# 10. Bước 3 — Kiểm chứng giả thuyết

Đây là phần trung tâm của dự án.

Mỗi giả thuyết được biến thành danh sách check. Ví dụ "Tồn kho tăng do chuẩn bị mở rộng sản xuất":

1. Tồn kho tăng bao nhiêu?
2. XDCB dở dang có tăng không?
3. CAPEX có tăng không?
4. Có dự án mở rộng sản xuất được đề cập không?
5. Nguyên vật liệu có tăng không?
6. Có bằng chứng phản bác không?

## 10.1. Check có cấu trúc

Mỗi check gồm: `id`, `kind` (`quant` | `text`), `importance` (`critical` | `supporting`), `polarity` (`support` | `contradict`), `tool` + `params` (với `quant`) hoặc `queries` (với `text`), điều kiện đạt.

Check `critical` đến từ thư viện. LLM chỉ được diễn đạt lại thành câu truy vấn; **không thêm/bớt check critical**.

## 10.2. Kiểm chứng định lượng

Python/SQL. Không dùng LLM để cộng, trừ, chia, tính growth/margin/ratio. Check trả `PASS` | `FAIL` | `NOT_EVALUABLE` (thiếu dữ liệu), kèm giá trị đầu vào và tham chiếu nguồn.

## 10.3. RAG

RAG dùng cho thuyết minh BCTC, báo cáo thường niên.

- Chunk theo cấu trúc tài liệu (tiêu đề, văn bản, bảng), giữ page/section.
- Query lấy từ `expected_textual_evidence` và `contradictory_evidence`.
- Bắt đầu BM25; so BM25/dense/hybrid trên tập dev; reranker chỉ khi đo thấy cần.
- Trả evidence gốc (document, page, section/chunk, source text), **không** trả câu trả lời tổng hợp.
- Không tìm thấy → check `UNVERIFIED`.

## 10.4. Citation nguyên văn

- Đoạn trích LLM đưa ra phải là **chuỗi con nguyên văn** của chunk gốc, **do code kiểm** (sau khi chuẩn hóa khoảng trắng).
- Không khớp → bằng chứng bị loại, check ghi `MISSING`.
- Mọi textual evidence dùng để kết luận phải có provenance: document, page, section/chunk, source text.

---

# 11. Vai trò của LLM

## Được dùng

1. Diễn đạt lại check thành câu truy vấn (không đổi danh sách check critical).
2. Phân loại từng đoạn truy xuất: `support` | `contradict` | `irrelevant`, bằng structured output gồm nhãn + quote nguyên văn + lý do ngắn.
3. Viết lại kết quả đã xác thực thành báo cáo (bước 4).
4. Trong baseline A (LLM only) và B (LLM + RAG).

## Không được dùng

- tự tính số liệu, tự tạo số;
- tự bịa evidence;
- thay đổi kết quả calculation;
- quyết định trạng thái SUPPORTED/PARTIAL/CONTRADICTED/UNVERIFIED (luật trong code);
- khẳng định quan hệ nhân quả khi evidence không đủ;
- dùng "trí nhớ ngành" thay cho evidence trong cấu hình Full System;
- sinh đáp án tham chiếu.

Ghi chú: giữa kỳ chỉ nêu LLM ở bước 4; việc dùng LLM phân loại bằng chứng ở bước 3 là điểm mở rộng đã chốt (mục 26.2).

---

# 12. Trạng thái kiểm chứng và xếp hạng

## 12.1. Trạng thái

| Trạng thái | Điều kiện |
|---|---|
| SUPPORTED | mọi check critical đạt và không có bằng chứng phản bác rõ |
| PARTIAL | có bằng chứng ủng hộ nhưng thiếu ít nhất một mảnh critical |
| CONTRADICTED | có bằng chứng rõ chống lại; nếu cùng lúc có bằng chứng ủng hộ, hiển thị cả hai |
| UNVERIFIED | dữ liệu hiện có không đủ để chạy các check critical |

Luật gộp là **hàm xác định trong code**. Thiếu dữ liệu không bao giờ thành SUPPORTED; dữ liệu thiếu không bị coi là 0.

## 12.2. Xếp hạng

Thứ tự: SUPPORTED > PARTIAL > UNVERIFIED > CONTRADICTED. Cùng trạng thái: tỷ lệ check critical đạt, rồi số check supporting đạt; hòa thì chia chung hạng.

Hệ quả có chủ ý: giả thuyết đúng nhưng không kiểm được bằng dữ liệu công khai sẽ xếp dưới giả thuyết PARTIAL; hiện tượng này được phân tích trong RQ3.

"UNVERIFIED" là kết quả có giá trị nghiên cứu.

---

# 13. Bước 4 — Báo cáo

LLM chỉ nhận dữ liệu đã xử lý: anomaly, calculated metrics, hypotheses, verified evidence, contradiction, missing evidence, citations, verification status. Không đọc lại toàn bộ BCTC.

Report gồm: 1. Bất thường; 2. Quan sát định lượng; 3. Top hypotheses; 4. Evidence từng hypothesis; 5. Contradictory evidence; 6. Missing evidence; 7. Kết luận có điều kiện; 8. Nguồn.

Ràng buộc:
- Sau khi LLM viết, code trích mọi con số trong báo cáo và đối chiếu bảng metrics (có dung sai làm tròn, hiểu định dạng số tiếng Việt). Lệch → từ chối và sinh lại. **Số lần lệch đếm trên bản nháp đầu tiên** để làm metric.
- Mọi khẳng định gắn một nguồn: mã evidence kèm trích dẫn, hoặc một số do code tính. Khẳng định không có nguồn bị loại.
- Kết luận có điều kiện; nêu rõ giả thuyết chưa xác thực và vì sao.
- Cấm: khuyến nghị mua/bán, kết luận gian lận, khẳng định nhân quả khi evidence không đủ. Có kiểm tra tự động bằng từ khóa/mẫu cho nhóm cấm này.
- Báo cáo bằng tiếng Việt; tên kỹ thuật giữ tiếng Anh.

---

# 14. Ba cấu hình đánh giá và biến thể

| Cấu hình | Đầu vào | Cách làm | Không có |
|---|---|---|---|
| A. LLM only | mô tả bất thường + bảng chỉ số cơ bản do code tính | LLM tự sinh và xếp hạng giả thuyết | retrieval, thư viện, kiểm chứng |
| B. LLM + RAG | như A + đoạn truy xuất từ kho tài liệu (lọc as-of) | LLM sinh giả thuyết có tham khảo đoạn truy xuất | thư viện, luật kiểm chứng, trạng thái |
| C. Full System | như B | 4 bước đầy đủ | — |

Cùng một mô hình LLM và cùng thiết lập (ghi trong `configs/llm.yaml`). **Mỗi ca chạy 3 lần**; báo trung bình và độ biến động.

## 14.1. So khớp giả thuyết giữa cấu hình

- **Chạy chính:** A và B sinh giả thuyết tự do; hai người gán nhãn **mù** (không biết cấu hình nào sinh) ánh xạ từng giả thuyết về taxonomy của thư viện, có mục `other`.
- **Chạy phụ:** A và B nhận cùng danh sách giả thuyết của thư viện, để tách đóng góp của quy trình kiểm chứng khỏi đóng góp của thư viện.

## 14.2. Ablation của Full System

- bỏ RAG (chỉ định lượng);
- bỏ bước kiểm chứng (thư viện + LLM xếp hạng, không check/trạng thái);
- bỏ thư viện (giả thuyết do LLM sinh tự do thay cho thư viện).

---

# 15. Evaluation

Mục tiêu: **18 ca** (trong khoảng 15–20 của báo cáo), cân bằng hai loại bất thường nếu dữ liệu cho phép.

Kết quả là **khảo sát khả thi, không phải kết luận thống kê** (cỡ mẫu nhỏ). Câu này phải xuất hiện trong mọi bảng kết quả xuất ra.

## 15.1. Chia dev/test

- **5 ca dev**: chỉnh ngưỡng, prompt, luật, chọn cách retrieval.
- **13 ca test**: đóng băng trước khi chạy; không chỉnh gì sau khi xem kết quả test.
- Cơ chế: `configs/eval_freeze.json` lưu hash của config, prompt, ngưỡng, danh sách ca test. Runner từ chối chạy test nếu hash lệch (trừ khi có cờ ghi nhận `--unfreeze-reason`, và lý do được ghi vào kết quả).

## 15.2. Đáp án tham chiếu

- **Do người gán nhãn**, không do LLM/Claude sinh.
- Hai thành viên gán độc lập; tính Cohen's kappa; bất đồng giải quyết với thành viên thứ ba và ghi lại.
- Đáp án là một tập id giả thuyết trong taxonomy (kể cả `other`).
- Mỗi nhãn ghi nguồn độc lập với kho RAG; validator kiểm nguồn không nằm trong kho RAG.

## 15.3. Metric

| Metric | Định nghĩa | RQ | Áp dụng |
|---|---|---|---|
| Hit@1, Hit@3 | tỷ lệ ca có ≥1 giả thuyết đúng ở top-1/top-3 | RQ1 | A, B, C |
| MRR | trung bình nghịch đảo hạng của giả thuyết đúng đầu tiên | RQ1 | A, B, C |
| Citation support rate | tỷ lệ trích dẫn thực sự hỗ trợ claim (kiểm nguyên văn bằng code, rồi hai người đánh giá) | RQ2 | A, B, C |
| Unsupported claim rate | tỷ lệ khẳng định trong báo cáo không có bằng chứng/số do code hỗ trợ | RQ2 | A, B, C |
| Numeric mismatch | số lần số trong báo cáo khác số code tính (bản nháp đầu) | RQ2 | A, B, C |
| Verification accuracy | tỷ lệ trạng thái khớp nhãn chuẩn, trên cặp (ca, giả thuyết) thuộc top-3 của C ở tập test | RQ2, RQ3 | C và ablation |
| False-SUPPORTED rate | tỷ lệ cặp gán SUPPORTED nhưng nhãn chuẩn không phải SUPPORTED | RQ2 | C và ablation |
| Chất lượng UNVERIFIED | tỷ lệ UNVERIFIED mà nguyên nhân thật là dữ liệu công khai không đủ (không phải lỗi retrieval/luật) | RQ3 | C |
| Độ trễ, token, tiền mỗi ca | đo thật | khả năng áp dụng | A, B, C |

## 15.4. Độ tin cậy

- khoảng tin cậy bootstrap hoặc Wilson cho Hit@k;
- trung bình và độ lệch trên 3 lần chạy;
- bảng kết quả từng ca cho cả ba cấu hình;
- kappa của đáp án tham chiếu.

## 15.5. Phân tích ca sai

Mỗi ca mà giả thuyết đúng không nằm trong top-3 của C được gán một nguyên nhân: `retrieval_miss`, `rule_wrong`, `llm_classification_wrong`, `ambiguous_reference`, `data_missing`. Việc gán nhãn do người làm; Claude chỉ dựng template và bảng tổng hợp.

## 15.6. RQ3

Bảng loại giả thuyết × tỷ lệ UNVERIFIED × lý do (cần dữ liệu nội bộ, thuyết minh không công bố, thiếu số lượng...).

## 15.7. Chỉ số thành công mặc định (nhóm có thể đổi **trước** khi chạy test)

- Hit@3: C > B > A.
- Numeric mismatch của C = 0 ở báo cáo cuối (số lệch ở bản nháp đầu báo riêng).
- Citation support rate của C ≥ 90%.
- Unsupported claim rate của C thấp hơn cả A và B.

Kết quả đọc trung thực theo từng RQ: nếu C không vượt ở Hit@3 nhưng vượt rõ ở citation/unsupported thì vẫn là kết quả hợp lệ.

## 15.8. Trực quan hóa

Biểu đồ cột Hit@1/Hit@3 kèm CI cho ba cấu hình; heatmap trạng thái theo loại giả thuyết; một ca minh họa (số liệu, đường giá quặng có độ trễ, đoạn trích).

Không biến evaluation thành benchmark quá lớn ngoài scope.

---

# 16. Nguyên tắc kỹ thuật tối thiểu

Các kỹ thuật sau là hỗ trợ hiện thực hóa, không phải mục tiêu nghiên cứu riêng.

- **Document parsing:** OCR text → giữ page, table/text, provenance.
- **Table parsing:** trích số phục vụ anomaly/verification; không xây platform tài liệu tài chính tổng quát. Hiểu định dạng số Việt Nam (dấu chấm ngăn nghìn, dấu phẩy thập phân, số âm trong ngoặc).
- **Metric normalization:** đủ để so sánh BCTC các công ty thép được chọn; từ điển nhãn nằm trong config, không taxonomy mọi ngành.
- **Storage:** Python structures, pandas, DuckDB/Parquet nếu hữu ích; không bắt buộc database phức tạp.
- **Retrieval:** BM25 trước; dense/hybrid/reranker chỉ khi đo thấy cần.
- **LLM client:** lớp bọc mỏng, model và tham số trong config, structured output bắt buộc, log token/độ trễ mỗi lần gọi, cache trên đĩa theo hash (key gồm `run_id` để 3 lần chạy không trùng cache). Không thêm agent framework.

---

# 17. Yêu cầu không hard-code công ty

Code phân tích không được viết riêng cho HPG. HPG chỉ là fixture/example.

Không được dựa vào: page cố định, row cố định, year cố định, tên Dung Quất, label chỉ xuất hiện ở một công ty.

Được phép có: steel-specific hypothesis library, steel-specific external data, steel-specific business rules. Đây là domain scope, không phải company hard-code.

---

# 18. Data contracts (schema dùng chung giữa các phase)

Dùng dataclass/Pydantic, có `to_dict`/`from_dict`, serialize JSON. Tên có thể điều chỉnh khi audit-first, nhưng khái niệm phải đủ.

- `Provenance`: `doc_id`, `page`, `block_id`/`chunk_id`, `source_text` (nếu là văn bản).
- `ValueStatus`: `OK` | `MISSING` | `UNKNOWN` | `NOT_APPLICABLE` | `UNRESOLVED`.
- `FinancialFact`: `company`, `period`, `metric`, `value` (nullable), `unit`, `status`, `source` (`structured` | `ocr`), `provenance`, `validation_flags`.
- `Anomaly`: `company`, `period`, `type`, `direction` (`up` | `down`), `indicators` (giá trị), `thresholds_hit`, `baseline_used`, `severity`, `flags` (`weak_baseline`, ...), `asof_date`.
- `Hypothesis`: như mục 9.1 (+ `checks`).
- `Check`, `CheckResult`: `PASS` | `FAIL` | `NOT_EVALUABLE`, `values`, `inputs`.
- `Evidence`: `kind` (`quant` | `text`), `polarity`, `check_id`, `provenance`, `quote` (đã qua kiểm nguyên văn).
- `HypothesisVerdict`: `hypothesis_id`, `status`, `rank`, `evidence`, `contradictions`, `missing`.
- `CaseRecord`: `case_id`, `split` (`dev` | `test`), `anomaly`, `reference_set`, `reference_sources`.
- `RunRecord`: `case_id`, `config` (A/B/C/ablation), `run_id`, `outputs`, `tokens`, `latency`.

---

# 19. Cấu trúc repo và config (gợi ý)

Nếu repo đã có cấu trúc khác: audit-first và thích nghi, không tái cấu trúc hàng loạt.

```text
configs/        anomaly.yaml, retrieval.yaml, llm.yaml, eval.yaml, dataset.yaml, eval_freeze.json
data/           raw/ (không commit dữ liệu bản quyền nếu cần), processed/, eval/ (cases, labels)
hypotheses/     inventory_*.yaml, gross_margin_*.yaml
src/<pkg>/      parsing/, extraction/, normalization/, validation/, anomaly/,
                hypotheses/, retrieval/, verification/, llm/, report/, eval/, demo/
tests/          mirror của src, có fixtures/ (HPG chỉ là fixture)
docs/           DATA_CARD.md, DEPLOYMENT_ANALYSIS.md, SLIDE_OUTLINE.md, CASE_STUDIES.md
AI_USAGE_LOG.md
DEVIATIONS_FROM_MIDTERM.md
```

Quy tắc config: mọi ngưỡng, độ trễ giá, top-k, model LLM, danh sách công ty, mốc thời gian nằm trong config. API key chỉ qua biến môi trường, không commit.

---

# 20. Phase triển khai

Các phase phục vụ trực tiếp pipeline 4 bước. Mỗi phase ghi: Mục tiêu · Đầu ra · Tiêu chí đạt · Ngoài phạm vi.

## Phase 0 — Foundation
- Mục tiêu: project sạch, test/lint, config loader, cấu trúc thư mục, giữ baseline legacy, tạo `AI_USAGE_LOG.md` và `DEVIATIONS_FROM_MIDTERM.md`.
- Tiêu chí đạt: `pytest` và `ruff` chạy được trên checkout sạch; config load được; không có secret trong repo.
- Ngoài phạm vi: mọi feature.

## Phase 1 — Document parsing
- Mục tiêu: OCR text → Document/Page/Block(text|table), giữ provenance.
- Tiêu chí đạt: id ổn định/deterministic; truy ngược được block về trang; test với fixture tổng hợp.

## Phase 2 — Table/data extraction
- Mục tiêu: table → dữ liệu số thô (dòng/cột/kỳ/số) + adapter đọc BCTC có cấu trúc (vnstock hoặc CSV/Parquet xuất từ nguồn công khai).
- Tiêu chí đạt: parse số Việt Nam đúng; lỗi parse → `UNRESOLVED`, không bỏ qua im lặng.
- Ngoài phạm vi: canonical mapping quá rộng.

## Phase 3 — Financial metric normalization
- Mục tiêu: chuẩn hóa metric cần cho scope: total inventory, raw materials, finished goods, WIP; revenue, COGS, gross profit, gross margin; CIP, CAPEX (nếu lấy đáng tin cậy).
- Tiêu chí đạt: từ điển nhãn trong config; quy tắc ưu tiên nguồn (mục 7.1) có test; mâu thuẫn → `UNRESOLVED`.

## Phase 4 — Validation
- Mục tiêu: kiểm kỳ, đơn vị, tổng thành phần, quan hệ kế toán cơ bản; báo cáo đối chiếu ngẫu nhiên với BCTC gốc (mẫu do người cung cấp).
- Tiêu chí đạt: cờ validation gắn vào fact; `reconciliation_report` sinh được.

## Phase 5 — Anomaly engine
- Mục tiêu: inventory và gross-margin anomaly theo mục 8 (hai phía, YoY trước, MAD, peer, `weak_baseline`).
- Tiêu chí đạt: deterministic; threshold trong config; unit test cho từng chỉ báo; sinh danh sách bất thường ứng viên để người chọn ca đánh giá.
- Ngoài phạm vi: anomaly khác, ML.

## Phase 6 — Hypothesis library
- Mục tiêu: schema + loader + validator + thư viện 6–8 giả thuyết/loại (**nội dung do người soạn**).
- Tiêu chí đạt: validator từ chối giả thuyết thiếu trường/nguồn/check critical; chỉ giả thuyết `reviewed` được tải vào pipeline; test schema.

## Phase 7 — RAG evidence retrieval
- Mục tiêu: index thuyết minh + báo cáo thường niên; chunk theo cấu trúc; lọc as-of; retriever interface (BM25 trước).
- Tiêu chí đạt: output evidence + provenance; test chứng minh không rò rỉ tài liệu sau as-of; không trả câu trả lời tổng hợp.

## Phase 8 — Quantitative verification tools
- Mục tiêu: các check mà thư viện yêu cầu (growth, ratio, so peer, so lịch sử, quan hệ khoản mục, so giá nguyên liệu có độ trễ).
- Tiêu chí đạt: registry tool khớp tên `tool` trong thư viện; `NOT_EVALUABLE` khi thiếu dữ liệu; unit test cho mọi tính toán.

## Phase 9 — LLM reasoning integration
- Mục tiêu: LLM client, diễn đạt query, phân loại evidence (structured output), kiểm quote nguyên văn bằng code.
- Tiêu chí đạt: toàn bộ test mock API; quote không khớp bị loại; log token/độ trễ.

## Phase 10 — Hypothesis verification/ranking
- Mục tiêu: hàm gộp check → trạng thái; xếp hạng theo mục 12.
- Tiêu chí đạt: golden test cho từng trạng thái và quy tắc xếp hạng/hòa; thiếu dữ liệu không bao giờ thành SUPPORTED.

## Phase 11 — Grounded report
- Mục tiêu: LLM viết từ verdict; bộ kiểm sau gồm đối chiếu số, liên kết claim–evidence, bộ lọc nội dung cấm; sinh lại khi lệch; đếm lệch ở bản nháp đầu.
- Tiêu chí đạt: test kiểm số (kể cả định dạng Việt), test bộ lọc cấm, test mock LLM.

## Phase 12a — Bộ ca và đáp án tham chiếu (công cụ)
- Mục tiêu: dựng công cụ; **người làm nội dung**. Gồm: template ca và nhãn (YAML/CSV), validator nhãn (id có trong taxonomy, nguồn độc lập với kho RAG), tính Cohen's kappa, khóa chia dev/test và tạo `eval_freeze.json`, template ánh xạ mù cho baseline, template phân loại ca sai.
- Tiêu chí đạt: test validator, kappa, cơ chế freeze.
- Claude KHÔNG chọn ca, KHÔNG gán nhãn, KHÔNG viết đáp án.
- Nên bắt đầu sớm, song song từ khi có Phase 5, vì gán nhãn tay tốn thời gian nhất.

## Phase 12 — Baselines + Evaluation
- Mục tiêu: runner cho A, B, C và 3 ablation; chạy chính/phụ cho baseline; 3 lần/ca; tính toàn bộ metric mục 15.3; CI; bảng từng ca; bảng ca sai; bảng RQ3; biểu đồ mục 15.8; xuất CSV/Markdown kèm câu "khảo sát khả thi".
- Tiêu chí đạt: runner từ chối chạy test khi freeze lệch; test metric trên dữ liệu giả; kết quả tái lập từ cache/seed.

## Phase 13 — Demo
- Mục tiêu: CLI hoặc Streamlit hiển thị anomaly, hypothesis, status, evidence, citation, missing evidence, final explanation, và dòng "hỗ trợ phân tích, không phải tư vấn đầu tư".
- Demo không phải mục tiêu nghiên cứu chính.

## Phase 14 — Gói nộp cuối kỳ
- Mục tiêu: Claude soạn bản nháp, **người xác minh và hoàn thiện**: `docs/DATA_CARD.md`, `AI_USAGE_LOG.md` (bản tổng hợp), `DEVIATIONS_FROM_MIDTERM.md`, bảng Coreach (góp ý → thay đổi/lý do giữ nguyên; **người cung cấp nội dung Coreach**), `docs/DEPLOYMENT_ANALYSIS.md` (người dùng, kịch bản, hạ tầng, chi phí đo thật, rủi ro, pháp lý, mở rộng), `docs/SLIDE_OUTLINE.md` chia theo trọng số rubric, `docs/CASE_STUDIES.md`.
- Ngoài phạm vi: viết phần pháp lý khi chưa có thông tin do người cung cấp.

---

# 21. Phân chia việc: người làm, Claude làm

## 21.1. Chỉ người làm (Claude không tự quyết/sinh)

- chọn bài toán và phạm vi;
- nội dung thư viện giả thuyết và nguồn của nó;
- chọn 18 ca, chia dev/test;
- đáp án tham chiếu, gán nhãn, giải quyết bất đồng;
- ánh xạ mù giả thuyết của baseline;
- gán nguyên nhân ca sai;
- chốt chỉ số thành công và ngưỡng (trước khi chạy test);
- nội dung Coreach;
- kiểm tra giấy phép dữ liệu và quy định pháp lý.

## 21.2. Claude làm (dưới sự kiểm tra của người)

- code, test, công cụ, template, bản nháp tài liệu;
- review phạm vi thay đổi (mục 25);
- báo cáo phase (mục 27).

## 21.3. Mỗi thành viên nắm một module

Mỗi phase hoàn thành phải ghi trong `AI_USAGE_LOG.md` "phần người cần tự xác minh" để thành viên phụ trách giải thích được khi hỏi đáp.

---

# 22. Luật cho Claude

Trước mỗi phase:

1. Đọc file này.
2. Đọc code hiện tại.
3. Nêu: hiện trạng; mục tiêu phase; file dự kiến sửa; rủi ro.
4. Chỉ implement phase được yêu cầu.

Không:
- tự chuyển framework;
- tự thêm agent framework;
- tự mở rộng sang nhiều ngành;
- tự thêm anomaly ngoài scope;
- tự đổi mục tiêu nghiên cứu;
- tự sinh nội dung thuộc mục 21.1;
- tự sửa config/prompt/ngưỡng sau khi tập test đã đóng băng.

---

# 23. Error handling

Nguyên tắc: **Không biết > đoán sai.**

Trạng thái hợp lệ: `UNKNOWN`, `MISSING`, `NOT_APPLICABLE`, `UNRESOLVED`, `UNVERIFIED`, `NOT_EVALUABLE`.

Không:
- missing → 0;
- parse fail → bỏ qua im lặng;
- retrieval không có evidence → LLM tự lấp;
- thiếu dữ liệu → SUPPORTED;
- trích dẫn không khớp nguyên văn → vẫn dùng.

---

# 24. Test gate

Mỗi phase phải:

- thêm test cho behavior mới;
- giữ test cũ;
- chạy pytest;
- chạy Ruff;
- không làm yếu assertion để pass.

Calculation quan trọng phải có unit test. LLM tests phải mock API. Unit test không được tiêu token API. Test chống rò rỉ as-of và test freeze là bắt buộc.

---

# 25. Review gate

Sau mỗi phase:

1. Review độc lập (ưu tiên agent/phiên khác không thấy quá trình viết code).
2. Phân loại: CRITICAL / HIGH / MEDIUM / LOW.
3. Fix CRITICAL/HIGH và MEDIUM nếu ảnh hưởng phase sau.
4. Regression test.
5. Người spot-check ít nhất một phép tính hoặc một số trích từ BCTC gốc.
6. Commit.

Không chạy nhiều phase liên tục mà không review.

---

# 26. Minh bạch AI và khác biệt so với giữa kỳ

## 26.1. AI_USAGE_LOG.md

Sau mỗi phase Claude thêm một mục: ngày, phase, việc Claude đã làm, các lỗi/sai sót đã bắt được khi review, **phần người cần tự xác minh**. Nhóm bổ sung cách kiểm tra đã thực hiện.

## 26.2. DEVIATIONS_FROM_MIDTERM.md

Ghi mọi điểm khác báo cáo giữa kỳ kèm lý do. Các điểm đã chốt:

| Điểm | Giữa kỳ | Bản này | Lý do |
|---|---|---|---|
| LLM phân loại bằng chứng ở bước 3 | LLM chỉ viết lại ở bước 4 | thêm phân loại bằng chứng, quote kiểm bằng code | cần đọc hiểu thuyết minh dài; rủi ro giới hạn bằng structured output + kiểm nguyên văn |
| Năm 2024 trở đi | giai đoạn 2021–2023 | chỉ làm nguồn đáp án tham chiếu | diễn biến kỳ sau của ca cuối 2023 nằm sau 2023; không đưa vào RAG nên không rò rỉ |
| Đo lường | Hit@k, citation, số lệch | thêm MRR, unsupported claim, false-SUPPORTED, dev/test, CI, kappa, ablation | cỡ mẫu nhỏ cần kiểm chứng tách biệt và nêu độ tin cậy |
| So khớp giả thuyết giữa cấu hình | chưa nêu | chạy chính (ánh xạ mù) + chạy phụ (cùng danh sách) | baseline sinh tự do, C dùng thư viện cố định |

Mọi điểm khác phát sinh trong quá trình làm phải được thêm vào đây trước khi nộp.

---

# 27. Format phản hồi của Claude

Toàn bộ phản hồi bằng tiếng Việt. Code/tên kỹ thuật giữ tiếng Anh.

Cuối mỗi phase phải báo:

1. Tóm tắt.
2. File tạo.
3. File sửa.
4. Quyết định thiết kế.
5. Tests.
6. Commands đã chạy.
7. Passed/skipped/failed.
8. Known limitations.
9. Những gì chưa làm vì ngoài scope.
10. Có hard-code công ty hay không.
11. Dòng đã thêm vào `AI_USAGE_LOG.md` và danh sách "phần người cần xác minh".
12. Điểm nào (nếu có) khác báo cáo giữa kỳ và đã ghi vào `DEVIATIONS_FROM_MIDTERM.md`.

---

# 28. Trạng thái hiện tại

Tài liệu này là spec chính thức bắt đầu từ **Phase 0**.

Repository có thể đã có code legacy. Sự tồn tại của code **không đồng nghĩa phase đã hoàn thành**.

Trạng thái ban đầu:

- Phase 0: NOT VERIFIED
- Phase 1: NOT VERIFIED
- Phase 2: NOT STARTED
- Phase 3: NOT STARTED
- Phase 4: NOT STARTED
- Phase 5: COMPLETED (2026-10-24; ngưỡng và as-of còn tạm, xem docs/phase_reports/PHASE_5.md)
- Phase 6: IN PROGRESS (2026-10-24; công cụ xong, chờ nhóm soạn thư viện 6–8 giả thuyết/loại, xem docs/phase_reports/PHASE_6.md)
- Phase 7: NOT STARTED
- Phase 8: NOT STARTED
- Phase 9: NOT STARTED
- Phase 10: NOT STARTED
- Phase 11: NOT STARTED
- Phase 12a: NOT STARTED
- Phase 12: NOT STARTED
- Phase 13: NOT STARTED
- Phase 14: NOT STARTED

Quy tắc cập nhật trạng thái:

1. Phase đã có code từ trước → **audit-first**: đối chiếu với yêu cầu phase, xác định phần đạt/chưa đạt, chỉ sửa phần cần thiết, không làm lại từ đầu nếu không cần.
2. Một phase chỉ thành `COMPLETED` sau khi: implementation đạt scope; test/lint pass; review độc lập xong; lỗi bắt buộc đã sửa; regression test pass; `AI_USAGE_LOG.md` đã cập nhật.
3. Không bắt đầu phase tiếp theo trước khi phase hiện tại `COMPLETED`.
4. Ngoại lệ về thứ tự: **Phase 12a có thể chạy song song** từ khi Phase 5 `COMPLETED`, vì phần việc của người (chọn ca, gán nhãn) kéo dài. Phần Claude (công cụ) vẫn theo quy tắc 2.

---

# 29. Definition of Done cuối kỳ

Dự án hoàn thành khi:

1. Nhận dữ liệu của nhóm công ty thép đã chọn.
2. Phát hiện được các anomaly trong scope (hai chiều).
3. Tải được hypothesis library (6–8 giả thuyết đã `reviewed` cho mỗi loại).
4. Kiểm tra hypothesis bằng code định lượng và RAG textual evidence (lọc as-of).
5. Có citation nguyên văn, kiểm bằng code.
6. Cho phép UNVERIFIED.
7. Rank hypotheses theo luật mục 12.
8. LLM viết grounded explanation, qua bộ kiểm số và nội dung cấm.
9. Chạy được ba cấu hình A/B/C, ba ablation, hai cách so khớp baseline.
10. Chạy evaluation 18 ca (5 dev, 13 test, đóng băng trước khi chạy test, mỗi ca 3 lần).
11. Xuất bảng kết quả (Hit@k, MRR, CI, citation, unsupported, numeric mismatch, verification accuracy, false-SUPPORTED, UNVERIFIED, độ trễ/chi phí), bảng ca sai, bảng RQ3, biểu đồ.
12. Có gói nộp (Phase 14): data card, AI usage log, deviations, bảng Coreach, phân tích triển khai, dàn slide, demo chạy được.
13. Không đưa ra khuyến nghị đầu tư hoặc kết luận gian lận.

---

# 30. Ánh xạ thang chấm cuối kỳ (kiểm tra nhanh)

| Tiêu chí (điểm) | Nằm ở đâu trong dự án |
|---|---|
| Bài toán (15) | mục 1.2, 5, 15.7; slide bài toán |
| Kỹ thuật (20) | mục 6, 14 (A/B/C, ablation, so sánh retrieval và anomaly), 7.2 (chống rò rỉ), Fintech: false-SUPPORTED, giải thích được, độ trễ/chi phí, không khuyến nghị đầu tư |
| Dữ liệu (15) | mục 7, `docs/DATA_CARD.md`, Phase 4 |
| Kết quả (20) | mục 15, Phase 12/12a: dev/test, CI, kappa, phân tích ca sai, biểu đồ |
| Khả năng áp dụng (10) | `docs/DEPLOYMENT_ANALYSIS.md`, Phase 13 |
| Phần dùng AI và tự làm (10) | mục 21, 26.1, `AI_USAGE_LOG.md` |
| Coreach (5) | bảng Coreach (Phase 14), người cung cấp nội dung |
| Trình bày (5) | `docs/SLIDE_OUTLINE.md`; mỗi thành viên nắm một module |
| Điểm thưởng | demo chạy được; dữ liệu thực; khả năng giải thích sâu |
| Điểm trừ cần tránh | không khớp giữa kỳ mà không giải thích (mục 26.2); không khai báo AI; vượt giờ |

---

# 31. Lệnh giao việc ngắn cho Claude

Sau khi file này nằm ở root repository, người dùng có thể giao:

> Đọc kỹ VIBE_MASTER_SPEC_REPORT_ALIGNED_CLAUDE.md (v2). Đây là spec chính thức của dự án bắt đầu từ Phase 0. Thực hiện Phase X theo chế độ audit-first: kiểm tra code hiện tại trước, đối chiếu với yêu cầu phase, chỉ sửa phần còn thiếu hoặc sai. Không triển khai phase sau. Không tự sinh nội dung thuộc mục 21.1. Trước khi code hãy inspect repository và báo kế hoạch ngắn. Sau khi code chạy đầy đủ test/lint, tự review phạm vi thay đổi, cập nhật AI_USAGE_LOG.md và báo cáo hoàn toàn bằng tiếng Việt.

Đây là lệnh mặc định cho toàn bộ quá trình vibe-code với Claude.
