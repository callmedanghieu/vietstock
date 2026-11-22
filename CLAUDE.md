# CLAUDE.md — vietstock

Pipeline trích 3 chỉ tiêu quản trị công ty (CG) từ Báo cáo thường niên (BCTN) PDF của công ty niêm yết VN, phục vụ khóa luận "The Impact of Corporate Governance on the Cost of Equity: A Study of Listed Firms in Vietnam". Mẫu: 315 công ty × 8 năm (~2.520 PDF).

3 chỉ tiêu: (1) %INED = TV HĐQT độc lập / tổng TV HĐQT; (2) % BKS không sở hữu cổ phần (0,00%); (3) tổng % sở hữu cá nhân của HĐQT.

## Lệnh thường dùng (PowerShell, tại gốc repo)
```powershell
.\.venv\Scripts\Activate.ps1
python cg_extract.py slim --in .\bctn --out .\slim                  # cắt ~12 trang liên quan, không cần API key
python cg_extract.py api  --in .\bctn --out .\ket_qua_local.xlsx    # gọi Claude, cần ANTHROPIC_API_KEY
python cg_tools.py plan  --dir bctn --done ket_qua.xlsx --shard 0 --n 8
python cg_tools.py merge --target ket_qua.xlsx --inputs shard_0.xlsx shard_1.xlsx
```
Trên Windows đặt `$env:PYTHONIOENCODING="utf-8"` khi in tiếng Việt ra console. Chạy `cg_tools.py` từ gốc repo (không dùng `python -I`).

## Cấu trúc
- `cg_extract.py` — lõi: đọc PDF (pdfplumber, OCR vie+eng khi trang < 40 ký tự), chấm điểm trang, nhận diện mã CK/năm, gọi Claude (forced tool_use, `SCHEMA`), tính lại số liệu bằng code, ghi Excel. Tự đọc `.env`.
- `cg_tools.py` — `plan` (chia shard, bỏ file đã xử lý) và `merge` (gộp shard vào `ket_qua.xlsx`).
- `.github/workflows/extract.yml` — 8 shard song song + job merge; chạy khi publish Release, push `bctn/**`/2 file .py, hoặc `workflow_dispatch` (input `tag`).
- `ket_qua.xlsx` — kết quả. Sheet chính `Tong_hop` (mỗi công ty 1 dòng; cột = % INED, % BKS 0%, tổng % sở hữu HĐQT theo từng năm 2019–2025, năm thiếu để trống; tự dựng lại, không sửa tay). Sheet phụ: `CG_data` 14 cột (chi tiết + Ghi chú, là nguồn/khóa của pipeline), `Thanh_vien` 13 cột, `Thieu_BCTN`. Bot commit tự động kèm `[skip ci]`.
- `docs/` — tài liệu chi tiết; đọc `docs/README.md` trước.

## Quy tắc nghiệp vụ (không đổi nếu chưa hỏi Simba)
- Mã CK và năm lấy từ TÊN FILE (dạng `FPT_Baocaothuongnien_2019.pdf`); mọi thông tin trong file thuộc mã/năm đó. Nội dung PDF chỉ dùng khi tên file không đọc được; lệch nhau chỉ ghi cảnh báo.
- Chỉ tính thành viên còn tại vị tại ngày tham chiếu; người bị loại ghi ở Ghi chú + `Thanh_vien` (tô vàng).
- Độc lập = thành viên được gắn mác "Thành viên HĐQT độc lập" CỦA CHÍNH công ty đang báo cáo; chức vụ độc lập ở công ty khác không tính (đếm theo danh sách, không dùng số model tự nêu; lệch thì chỉ cảnh báo). Sở hữu = cá nhân, không cộng phần đại diện tổ chức.
- BKS = ai nằm trong BKS là tính (kể cả "Thành viên chuyên trách"), không lọc theo từ khóa chức danh; chỉ loại theo ngày miễn nhiệm/bổ nhiệm.
- Mỗi công ty cần đủ BCTN 2019–2025. Quét trước bằng `python cg_tools.py check --dir bctn` (`api` cũng tự in báo cáo này). Năm thiếu được ghi `[Thiếu BCTN: ...]` cuối cột Ghi chú và liệt kê ở sheet `Thieu_BCTN`.
- Mọi người có ngày bổ nhiệm/miễn nhiệm được liệt kê ở Ghi chú (`[CẦN XEM ...]`) và sheet `Thanh_vien` để Simba tự xem xét.
- Sở hữu BKS 0%: tỷ lệ in ra là 0,00% → ghi nhận 0% (kể cả khi số CP khác 0); nếu không có tỷ lệ 0,00% mà có số lượng CP → 0 CP mới là 0%, khác 0 là khác 0%; chỉ có tỷ lệ khác 0 → khác 0. Không có cả hai → N/A (không coi là 0).
- Sở hữu HĐQT: BCTN làm tròn (lên hoặc xuống) thì lấy đúng số đã làm tròn.
- Tổng % HĐQT và số BKS 0% do code tính lại từ danh sách thành viên, không tin số model tự cộng.
- Phó TGĐ, Kế toán trưởng (Ban điều hành) không tính vào HĐQT. PDF scan phải OCR.
Chi tiết: `docs/business-rules.md`.

## Lưu ý khi làm việc
- Không commit `.env`, `.venv/`, PDF thật. PDF lớn đi qua GitHub Release.
- Cột `File` (cột 14 của `CG_data`, cột 13 của `Thanh_vien`) là khóa để bỏ file đã xử lý; dòng có Ghi chú bắt đầu `API lỗi`/`LỖI`/... sẽ tự chạy lại.
- Khi sửa `split_members` hoặc `detect_identity`, test lại với YEG 2025 (HĐQT 4/2 độc lập, 6,56%) và FPT 2021 (7/3, BKS 3/1, 10,16%).
- Workflow chỉ giữ 1 run chờ: publish Release lần lượt, đợi run trước xong.
- API key ở `.env` (local) hoặc secret `ANTHROPIC_API_KEY` (Actions). Không in key ra log/chat.
