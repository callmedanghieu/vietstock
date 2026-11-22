# Quy tắc nghiệp vụ

| Quy tắc | Thực hiện trong code |
|---|---|
| Mã CK/năm lấy từ tên file | `parse_name` + `choose` (tên file > nội dung > model; nội dung lệch chỉ ghi cảnh báo). `detect_identity` chỉ là dự phòng |
| Mọi thông tin trong file thuộc năm của tên file | Ví dụ `FPT_Baocaothuongnien_2019.pdf` → toàn bộ là năm 2019; BCTN 2021 nhắc ngày 2022 vẫn là 2021 |
| Chỉ người còn tại vị tại ngày tham chiếu | `split_members` so `appointed_date`/`left_date` với `ref` |
| Ngày tham chiếu | Model → ngày số liệu sở hữu (nếu muộn hơn) → 31/12 của năm (kèm cảnh báo) |
| Độc lập = được gắn mác "Thành viên HĐQT độc lập" của CHÍNH công ty báo cáo (không tính độc lập ở công ty khác ghi trong lý lịch) | Prompt (`PROMPT`, `SCHEMA.independent`) ràng buộc điều này. Đếm `independent` trong danh sách người còn tại vị; số model tự nêu chỉ để cảnh báo khi lệch; "không điều hành" ≠ độc lập |
| BKS = nằm trong BKS | `split_members` không lọc chức danh với BKS (vd. "Thành viên chuyên trách"), chỉ lọc theo ngày |
| Liệt kê hết người có ngày bổ nhiệm/miễn nhiệm | Ghi chú `[CẦN XEM ...]` + sheet `Thanh_vien`; Simba tự xem xét |
| Sở hữu cá nhân, không cộng phần đại diện | Prompt + `own_pct` |
| BKS 0%: tỷ lệ in ra là 0,00% → 0% (kể cả số CP khác 0); không thì có số CP → 0 CP mới là 0%; chỉ có tỷ lệ khác 0 → khác 0 | `is_zero` trong `process_one`: xét `own_pct`==0 trước, rồi `shares` |
| BKS không công bố sở hữu (không có cả số CP lẫn tỷ lệ) → N/A | `process_one`: có TV BKS không xác định được → `kz = None` |
| Sở hữu HĐQT: BCTN đã làm tròn thì lấy đúng số làm tròn | Prompt `own_pct`; tổng = cộng các số đó, không tính lại từ số CP |
| Số liệu tính lại bằng code | Tổng % HĐQT = tổng `own_pct` người còn tại vị; BKS 0% đếm từ danh sách |
| Ban điều hành không thuộc HĐQT | `split_members` loại chức danh bắt đầu bằng Phó TGĐ/TGĐ/Kế toán trưởng/Giám đốc và vai trò phủ định |
| PDF scan | `page_texts` OCR vie+eng trang < 40 ký tự |
| Công ty 1 tầng (không BKS) | Không liệt kê BKS |
