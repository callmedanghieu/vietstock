# Xử lý sự cố và vấn đề đã biết

| Triệu chứng | Nguyên nhân / cách xử |
|---|---|
| `UnicodeEncodeError` khi in tiếng Việt | `$env:PYTHONIOENCODING="utf-8"` |
| `[OCR bỏ qua trang N ...]` | Chưa cài tesseract/poppler (xem setup.md) |
| `API lỗi: ...` ở Ghi chú | Thiếu/sai key hoặc rate limit; chạy lại, dòng lỗi tự được thử lại |
| `ModuleNotFoundError: cg_extract` | Chạy `cg_tools.py` từ gốc repo, đừng dùng `python -I` |
| Publish nhiều release liền, run bị bỏ | Chỉ giữ 1 run chờ; publish lần lượt |
| Artifact shard trống | Shard không có file hoặc lỗi sớm; xem log shard |

## Vấn đề đã biết (từ lần chạy release `test`)
- **Đã sửa**: file VNM 2020/2022 từng ra `MCM`/`GIA` vì ưu tiên nội dung hơn tên file. Nay mã CK và năm lấy theo tên file; hai dòng cũ trong `ket_qua.xlsx` cần chạy lại để cập nhật.
- BKS `N/A` nhiều ở FPT 2022–2025: báo cáo không công bố sở hữu từng TV BKS (đúng quy tắc, không phải lỗi).
- HPG: số TV độc lập = 0 ở mọi năm — nên đối chiếu báo cáo gốc.
- VNM 2020/2022: thiếu sở hữu cá nhân nhiều TV HĐQT nên tổng % là N/A.
