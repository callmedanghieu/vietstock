# Tóm tắt dự án vietstock

## Mục tiêu
Phục vụ khóa luận *"The Impact of Corporate Governance on the Cost of Equity: A Study of Listed Firms in Vietnam"*. Dự án tự động trích 3 chỉ tiêu quản trị công ty (CG) từ Báo cáo thường niên (BCTN, file PDF) của các công ty niêm yết ở Việt Nam, thay cho việc nhập tay.

Quy mô dự kiến: khoảng 713 công ty (412 HOSE + 302 HNX) × 5 năm (2021–2025).

## 3 chỉ tiêu
1. **% thành viên HĐQT độc lập** = số thành viên HĐQT độc lập / tổng số thành viên HĐQT.
2. **% thành viên BKS không sở hữu cổ phần** = số thành viên Ban kiểm soát có sở hữu cá nhân bằng 0 / tổng số thành viên BKS.
3. **Tổng % sở hữu cá nhân của HĐQT** = cộng tỷ lệ sở hữu cá nhân của từng thành viên HĐQT.

## Cách chạy (mỗi file PDF)
1. **Đọc PDF:** lấy text từng trang bằng pdfplumber. Trang scan hoặc font lỗi thì dùng OCR (Tesseract, tiếng Việt + Anh).
2. **Chọn trang liên quan:** chấm điểm từ khóa (HĐQT, BKS, sở hữu...) trên toàn file, tối đa 16 trang. Mục lục (5 trang đầu) chỉ dùng để ưu tiên trang và tăng tốc OCR.
3. **Gọi mô hình Claude (một lần mỗi file):** gửi các trang đã chọn, mô hình trả về danh sách thành viên HĐQT/BKS theo cấu trúc cố định (tên, chức vụ, độc lập hay không, sở hữu, ngày bổ nhiệm/miễn nhiệm).
4. **Tính lại bằng code:** tổng và tỷ lệ do chương trình tự tính từ danh sách thành viên, không tin số mô hình tự cộng.
5. **Ghi Excel:** sheet `Tong_hop` (mỗi công ty một dòng, cột theo năm), `CG_data` (chi tiết + ghi chú), `Thanh_vien`, `Thieu_BCTN`.

Mã chứng khoán và năm lấy từ **tên file**, không lấy từ nội dung PDF.

## Quy tắc nghiệp vụ chính
- Chỉ tính thành viên còn tại vị ở ngày tham chiếu; người có ngày bổ nhiệm/miễn nhiệm được ghi chú để tự xem lại.
- **Độc lập:** chỉ tính thành viên được gắn mác "độc lập" của chính công ty đang báo cáo.
- **Sở hữu:** chỉ lấy sở hữu cá nhân, không lấy phần đại diện (vốn nhà nước, tổ chức). Nếu BCTN chỉ nêu số cổ phiếu thì chia cho số cổ phiếu lưu hành.
- **BKS 0%:** ưu tiên số cổ phiếu cá nhân (khác 0 → có sở hữu; bằng 0 → 0%). Chỉ khi không có số cổ phiếu mới dùng tỷ lệ: 0 hoặc 0,00 → 0%; số dương rất nhỏ → khác 0.
- Không tìm được dữ liệu thì ghi **N/A và tô vàng** để kiểm tra thủ công. Không đoán số.
- Năm thiếu BCTN được ghi `[Thiếu BCTN: ...]` và liệt kê ở sheet `Thieu_BCTN`.

## Các file chính
- `cg_extract.py`: phần lõi (đọc PDF, chọn trang, gọi Claude, tính lại, ghi Excel).
- `cg_tools.py`: chia việc thành nhiều phần (`plan`), gộp kết quả (`merge`), kiểm tra thiếu BCTN (`check`).
- `docs/`: tài liệu chi tiết; đọc `docs/README.md` trước.

## Trạng thái hiện tại
- Đã chạy thử trên 100 mã, BCTN 2021–2025 (492 file): **336 file đã xong**, chưa gộp thành file tổng. Chưa có lỗi API.
- Chưa đối chiếu kỹ với PDF gốc, và chưa có bộ dữ liệu chuẩn để đo độ chính xác.
- Một số dòng còn trống (N/A), nhất là sở hữu HĐQT và số thành viên BKS ở một số công ty.

## Hạn chế đã biết
- **OCR (Tesseract)** cho kết quả không ổn định ở file scan, đặc biệt ở bảng và số nhỏ. Đang cân nhắc bỏ OCR, dùng PyMuPDF để đọc text và mô hình đọc ảnh trang cho file scan hoặc lỗi font. Chưa quyết định và chưa triển khai.
- Kết quả giữa các lần chạy có thể lệch nhau chút, vì có gọi mô hình.
- Chi phí gọi mô hình ước tính khoảng 0,005 USD mỗi file.
