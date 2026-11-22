# Cài đặt

```powershell
git clone https://github.com/callmedanghieu/vietstock.git
cd vietstock
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env       # rồi mở .env điền ANTHROPIC_API_KEY
```

## File `.env`
| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `ANTHROPIC_API_KEY` | Có (chế độ `api`) | Khóa Claude API, dạng `sk-ant-...` |
| `CG_MODEL` | Không | Model dùng để trích, mặc định `claude-haiku-5-5` |

`cg_extract.py` tự đọc `.env` (python-dotenv). `.env` nằm trong `.gitignore`. Trên GitHub Actions khóa lấy từ secret `ANTHROPIC_API_KEY`.

## OCR cho PDF scan
Cần `tesseract` (kèm gói tiếng Việt `vie`) và `poppler` trong PATH.
- Tesseract: bản UB Mannheim (github.com/UB-Mannheim/tesseract/wiki), tick "Vietnamese" khi cài.
- Poppler: tải bản Windows, thêm thư mục `bin` vào PATH.

Kiểm tra: `tesseract --list-langs` có `vie`; `pdftoppm -h` chạy được. Thiếu thì trang scan bị bỏ qua (cảnh báo `[OCR bỏ qua ...]`).

Máy dev dùng Python 3.14; workflow dùng 3.11.
