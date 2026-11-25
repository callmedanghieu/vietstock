# Cách dùng

## A. Chạy trên máy
Đặt vài PDF vào `bctn/` để thử (đừng commit PDF thật).
```powershell
python cg_extract.py slim --in .\bctn --out .\slim
python cg_extract.py api  --in .\bctn --out .\ket_qua_local.xlsx
```
Chạy lại với cùng `--out`: file đã có dòng hợp lệ được bỏ qua, dòng lỗi được chạy lại. Có thể truyền nhiều thư mục: `--in .\bctn .\bctn_release`.

### Chạy toàn bộ `bctn/` trên máy, chia 4 tiến trình song song
Dự án nằm ở `D:\KLTN\vietstock` (đã chuyển khỏi OneDrive). Mở terminal rồi:
```powershell
cd D:\KLTN\vietstock
$n=4; $files=Get-ChildItem bctn\*.pdf | Sort-Object Name; for($i=0;$i -lt $files.Count;$i++){ $d="parts\part$($i % $n)"; New-Item -ItemType Directory -Force $d | Out-Null; New-Item -ItemType HardLink -Path "$d\$($files[$i].Name)" -Target $files[$i].FullName | Out-Null }
```
Mở 4 cửa sổ terminal, mỗi cửa sổ chạy một phần (đổi `0` thành `1`, `2`, `3`):
```powershell
cd D:\KLTN\vietstock; .\.venv\Scripts\Activate.ps1; $env:PYTHONIOENCODING="utf-8"
python cg_extract.py api --in parts\part0 --out kq0.xlsx --no-coverage
```
Khi cả 4 xong, gộp và tính thiếu BCTN:
```powershell
python cg_tools.py merge --target ket_qua_full.xlsx --inputs kq0.xlsx kq1.xlsx kq2.xlsx kq3.xlsx
```
Dừng giữa chừng rồi chạy lại đúng lệnh thì file đã xong được bỏ qua. `parts/`, `kq*.xlsx`, `ket_qua_full.xlsx` đã nằm trong `.gitignore`. Muốn gộp vào repo: `git pull`, rồi `merge --target ket_qua.xlsx` với các file `kq*.xlsx`.

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
