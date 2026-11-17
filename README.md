# vietstock — Trích dữ liệu quản trị công ty từ Báo cáo thường niên

Thả PDF Báo cáo thường niên vào thư mục `bctn/`, GitHub Actions tự chạy và ghi kết quả vào `ket_qua.xlsx`.

## Chỉ tiêu trích
1. Tổng số thành viên HĐQT và số thành viên độc lập (→ % INED)
2. Tổng số thành viên BKS và số thành viên BKS sở hữu 0% cổ phần (→ % BKS không sở hữu)
3. Tổng tỷ lệ sở hữu cổ phần cá nhân của HĐQT

## Cách dùng
1. **Settings → Secrets and variables → Actions → New repository secret**: tên `ANTHROPIC_API_KEY`, giá trị là key `sk-ant-...` (làm một lần).
2. Upload PDF vào `bctn/` (kéo thả trên web: tối đa 100 file/lần, 25 MB/file). Tên file dạng `MÃCK_NĂM.pdf`, ví dụ `VNM_2023.pdf`, `YEG_Baocaothuongnien_2024.pdf`.
3. Chờ tab **Actions** chạy xong (vài phút cho mỗi 20–30 file). `ket_qua.xlsx` ở gốc repo được cập nhật tự động; file đã xử lý sẽ không chạy lại.
4. Mở `ket_qua.xlsx`, ưu tiên soát các dòng có cột **Ghi chú** không rỗng hoặc có `N/A`.

## Lưu ý
- Repo GitHub nên giữ dưới ~1 GB. Sau khi một đợt đã có kết quả trong Excel, có thể xoá PDF khỏi `bctn/` để repo nhẹ (kết quả vẫn giữ nguyên).
- File scan sẽ được OCR tự động (tesseract tiếng Việt) trên máy ảo của GitHub.
- Chạy local: `pip install -r requirements.txt` rồi `python cg_extract.py api --in bctn --out ket_qua.xlsx` (hoặc `slim` để chỉ cắt trang).
