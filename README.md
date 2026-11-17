# vietstock — Trích dữ liệu quản trị công ty từ Báo cáo thường niên

Đính kèm PDF Báo cáo thường niên (BCTN) vào một **Release**, GitHub Actions tự chạy và ghi kết quả vào `ket_qua.xlsx` ở gốc repo.

## Chỉ tiêu trích
1. Tổng số thành viên HĐQT và số thành viên độc lập (→ % INED)
2. Tổng số thành viên BKS và số thành viên BKS sở hữu 0% cổ phần (→ % BKS không sở hữu)
3. Tổng tỷ lệ sở hữu cổ phần cá nhân của HĐQT

## Tên file PDF
Không cần đổi tên: script đọc **mã chứng khoán** và **năm báo cáo** từ nội dung PDF (các dòng "Mã chứng khoán: XXX", "Mã cổ phiếu", "Báo cáo thường niên 20xx", "Annual report 20xx" — lấy giá trị xuất hiện nhiều nhất).
Nếu tên file có dạng `VNM_2023.pdf` thì được dùng để đối chiếu; khi tên file và nội dung lệch nhau, script dùng theo nội dung và ghi cảnh báo `[...]` ở cột Ghi chú. Model đọc thêm một lần nữa làm ý kiến thứ ba.

## Cách dùng

**Một lần:** Settings → Secrets and variables → Actions → New repository secret → tên `ANTHROPIC_API_KEY`, giá trị `sk-ant-...`.

**Mỗi đợt dữ liệu (cách chính — không giới hạn dung lượng):**
1. Vào **Releases** (cột phải trang repo) → **Draft a new release**.
2. Ô *Choose a tag*: gõ tên đợt, ví dụ `dot-01`, rồi bấm *Create new tag*. Tiêu đề đặt tuỳ ý.
3. Kéo thả toàn bộ PDF vào ô *Attach binaries* (mỗi file tối đa 2 GB). Chờ upload xong hết.
4. Bấm **Publish release**. Tab **Actions** tự chạy; xong thì `ket_qua.xlsx` ở gốc repo được cập nhật.

**Cách phụ (đợt nhỏ ≤ 20 file, mỗi file < 25 MB):** upload PDF vào thư mục `bctn/` (Add file → Upload files → Commit). Lưu ý PDF trong `bctn/` tính vào dung lượng repo (giới hạn ~1 GB) và xoá đi vẫn còn trong lịch sử git, nên chỉ dùng để thử.

## Đọc kết quả
- Mỗi dòng = một công ty-năm. File đã xử lý không chạy lại; dòng lỗi API sẽ tự chạy lại ở đợt sau.
- Ưu tiên soát các dòng có cột **Ghi chú** bắt đầu bằng `[...]` (script phải tính lại hoặc phát hiện lệch) hoặc có `N/A`.
- Cột **Chi tiết thành viên** liệt kê từng người kèm tỷ lệ sở hữu để đối chiếu.

## Chạy trên máy (không cần GitHub)
```
pip install -r requirements.txt
set ANTHROPIC_API_KEY=sk-ant-...
python cg_extract.py api --in ./bctn --out ./ket_qua.xlsx      # tự trích
python cg_extract.py slim --in ./bctn --out ./slim              # chỉ cắt trang để upload lên chat
```
File scan được OCR tự động nếu máy có `tesseract` (gói tiếng Việt) và `poppler`; trên GitHub Actions đã cài sẵn.
