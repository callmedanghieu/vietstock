# Cách dùng

## A. Chạy trên máy
Đặt vài PDF vào `bctn/` để thử (đừng commit PDF thật).
```powershell
python cg_extract.py slim --in .\bctn --out .\slim
python cg_extract.py api  --in .\bctn --out .\ket_qua_local.xlsx
```
Chạy lại với cùng `--out`: file đã có dòng hợp lệ được bỏ qua, dòng lỗi được chạy lại. Có thể truyền nhiều thư mục: `--in .\bctn .\bctn_release`.

## B. Chạy trên GitHub (khối lượng lớn)
1. Releases → Draft a new release, tag ví dụ `dot-01`, đính kèm ~300–400 PDF, Publish.
2. Actions tự chạy: 8 shard song song → job `merge` commit `ket_qua.xlsx`.
3. Đợi run xong rồi mới publish release tiếp theo (chỉ giữ 1 run chờ). 2.520 file ≈ 7–9 release.

Chạy lại một release: Actions → "Trích dữ liệu CG từ BCTN" → Run workflow → nhập `tag`. File đã có kết quả tốt được bỏ qua; muốn ép chạy lại thì xóa dòng đó khỏi `ket_qua.xlsx`.

## Quét thử độ đủ BCTN 2019–2025
Sau khi upload PDF vào `bctn/`, chạy trước khi trích:
```powershell
python cg_tools.py check --dir bctn
```
In ra mỗi công ty thiếu BCTN năm nào (đọc mã/năm từ tên file). Với Release: `python cg_tools.py check --assets assets.jsonl`. Lệnh `cg_extract.py api` cũng tự in báo cáo này. Kết quả: cột Ghi chú của `CG_data` có thêm `[Thiếu BCTN: 2020, 2022]` ở cuối, và sheet `Thieu_BCTN` liệt kê năm có/thiếu của từng công ty (tính trên toàn bộ `ket_qua.xlsx`, nên tự cập nhật khi các đợt sau bổ sung năm còn thiếu).

## C. Công cụ phụ
- `cg_tools.py plan --assets assets.jsonl | --dir bctn --done ket_qua.xlsx --shard K --n 8` — in `<id>\t<tên file>` của shard K.
- `cg_tools.py merge --target ket_qua.xlsx --inputs shard_*.xlsx` — gộp; dòng cũ cùng tên file bị thay.

## D. Đọc kết quả
Soát trước các dòng có Ghi chú bắt đầu bằng `[...]` hoặc có `N/A`. Chi tiết từng người ở sheet `Thanh_vien`.

## E. Chi phí và giới hạn
Khoảng 1 phút/file. 8 máy gọi API cùng lúc có thể chạm rate limit (SDK tự retry tối đa 6 lần). Theo dõi chi phí ở console Anthropic.
