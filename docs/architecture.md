# Kiến trúc

```
PDF ─ page_texts (pdfplumber / OCR) ─► select_pages (chấm điểm từ khóa, ≤ 12 trang)
   └ detect_identity (mã CK, năm) ─┐
                                   ▼
        call_claude (tool_use ghi_ket_qua) ─► split_members ─► tính lại số liệu ─► Excel
```

## Chấm điểm trang
`KW_STRONG` 3 điểm/lần, `KW_WEAK` 1 điểm, `KW_NEG` (kiểm toán, lưu chuyển tiền tệ...) loại trang; +3 nếu có tỷ lệ `x,y%` cùng từ HĐQT/BKS. Giữ trang ≥ `MIN_SCORE` = 4, tối đa `MAX_PAGES` = 12.

## `ket_qua.xlsx` — sheet chính và sheet phụ
- **`Tong_hop` (sheet chính, đứng đầu)**: mỗi công ty một dòng; 22 cột = Mã CK + 3 nhóm × 7 năm (`% INED 2019`…`2025`, `% BKS không sở hữu cổ phần 2019`…`2025`, `Tổng % sở hữu HĐQT 2019`…`2025`). Năm thiếu BCTN để trống. Tự dựng lại từ `CG_data` mỗi lần chạy/gộp (`build_summary`), đừng sửa tay. Năm ngoài 2019–2025 (vd. 2018) không hiện ở đây, vẫn còn ở `CG_data`.
- Sheet phụ: `CG_data` (chi tiết và Ghi chú, mô tả bên dưới), `Thanh_vien`, `Thieu_BCTN`.

### Chi tiết các sheet
- `CG_data` (14 cột): Mã CK, Năm, Tổng TV HĐQT, TV độc lập, %INED, Tổng TV BKS, BKS 0%, % BKS không sở hữu, Tổng % sở hữu HĐQT, Ngày số liệu SH, Trang nguồn, Ghi chú, Chi tiết thành viên (JSON), File.
- `Thanh_vien` (13 cột): từng người (cả người bị loại, tô vàng), cột Tính vào số liệu = Có/KHÔNG, File.

## Workflow `extract.yml`
`extract` (matrix 8 shard): checkout main → `plan` → cài OCR nếu shard có file → tải asset (`gh api` octet-stream) → `cg_extract.py api` → upload artifact `result-K`.
`merge`: tải artifact → `cg_tools.py merge` → commit `[skip ci]`.
`concurrency: extract`, không hủy run đang chạy.
