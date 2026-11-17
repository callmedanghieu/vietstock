#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cg_extract.py — Lọc trang quản trị công ty từ Báo cáo thường niên (BCTN) và trích 3 chỉ tiêu CG.

Cách dùng:
  pip install pdfplumber pypdf openpyxl anthropic   (pytesseract + poppler nếu có file scan)

  # 1) Chỉ lọc trang, tạo PDF mỏng để upload lên chat (không cần API key)
  python cg_extract.py slim  --in ./bctn --out ./slim

  # 2) Tự động trích bằng Claude API, ghi Excel (cần ANTHROPIC_API_KEY)
  python cg_extract.py api   --in ./bctn --out ./ket_qua.xlsx

Tên file PDF nên có mã CK và năm, ví dụ: YEG_2024.pdf, VNM_Baocaothuongnien_2023.pdf
"""
import argparse, json, os, re, sys, unicodedata
from pathlib import Path

# ---------- Từ khoá nhận diện trang liên quan ----------
KW_STRONG = [  # mỗi hit = 3 điểm
    "tỷ lệ sở hữu cổ phần", "sở hữu cổ phần", "số cổ phần sở hữu", "cổ phần nắm giữ",
    "thành viên độc lập", "thành viên hđqt độc lập", "độc lập hội đồng quản trị",
    "trưởng ban kiểm soát", "thành viên ban kiểm soát",
]
KW_WEAK = [    # mỗi hit = 1 điểm
    "hội đồng quản trị", "ban kiểm soát", "chủ tịch hđqt", "phó chủ tịch",
    "người nội bộ", "cổ đông nội bộ", "thời gian đương nhiệm", "nhiệm kỳ",
]
KW_NEG = ["kiểm toán độc lập", "ý kiến kiểm toán", "lưu chuyển tiền tệ", "thuyết minh báo cáo tài chính"]

MAX_PAGES = 12      # số trang tối đa giữ lại cho mỗi báo cáo
MIN_SCORE = 4       # điểm tối thiểu để giữ trang


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    # một số BCTN xuất text lỗi "đ" thành ")" hoặc "$"
    s = re.sub(r"[)\$#%&'!](?=[ôồổỗộơờởỡợ])", "đ", s)
    return re.sub(r"\s+", " ", s)


def page_texts(pdf_path: Path):
    """Trả về list text từng trang; OCR trang nào không có text layer."""
    import pdfplumber
    texts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, p in enumerate(pdf.pages):
            t = p.extract_text() or ""
            if len(t.strip()) < 40:
                t = ocr_page(pdf_path, i + 1)
            texts.append(t)
    return texts


def ocr_page(pdf_path: Path, pageno: int) -> str:
    try:
        from pdf2image import convert_from_path
        import pytesseract
        img = convert_from_path(str(pdf_path), dpi=200, first_page=pageno, last_page=pageno)[0]
        return pytesseract.image_to_string(img, lang="vie+eng")
    except Exception as e:  # không có tesseract → bỏ qua, báo ra stderr
        print(f"  [OCR bỏ qua trang {pageno}: {e}]", file=sys.stderr)
        return ""


def score_page(t: str) -> int:
    n = norm(t)
    if any(k in n for k in KW_NEG) and not any(k in n for k in KW_STRONG):
        return 0
    s = sum(3 * n.count(k) for k in KW_STRONG) + sum(n.count(k) for k in KW_WEAK)
    # bảng sở hữu thường có nhiều "%" hoặc số cổ phần
    if re.search(r"\d[,.]\d{1,2}\s*%", n) and ("hđqt" in n or "kiểm soát" in n or "hội đồng" in n):
        s += 3
    return s


def select_pages(texts):
    scored = [(score_page(t), i) for i, t in enumerate(texts)]
    keep = sorted([i for s, i in scored if s >= MIN_SCORE], key=lambda i: -scored[i][0])[:MAX_PAGES]
    return sorted(keep)


def parse_name(pdf_path: Path):
    stem = pdf_path.stem
    m_year = re.search(r"(20\d{2})", stem)
    m_code = re.match(r"([A-Z0-9]{3,4})", stem.upper())
    return (m_code.group(1) if m_code else stem[:4].upper(), int(m_year.group(1)) if m_year else None)


# ---------- Chế độ SLIM ----------
def mode_slim(in_dir: Path, out_dir: Path):
    from pypdf import PdfReader, PdfWriter
    out_dir.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(in_dir.glob("*.pdf")):
        texts = page_texts(pdf)
        pages = select_pages(texts)
        if not pages:
            print(f"{pdf.name}: KHÔNG tìm được trang phù hợp (file scan? kiểm tra lại)")
            continue
        r, w = PdfReader(str(pdf)), PdfWriter()
        for i in pages:
            w.add_page(r.pages[i])
        out = out_dir / f"{pdf.stem}_slim.pdf"
        with open(out, "wb") as f:
            w.write(f)
        print(f"{pdf.name}: {len(r.pages)} → {len(pages)} trang (gốc: {', '.join(str(i+1) for i in pages)})")


# ---------- Chế độ API ----------
PROMPT = """Bạn là trợ lý trích dữ liệu quản trị công ty từ Báo cáo thường niên của công ty niêm yết Việt Nam.
Dưới đây là text của các trang liên quan (mỗi trang bắt đầu bằng "=== TRANG n ===").
Lấy số liệu tại thời điểm CUỐI NĂM báo cáo; người đã miễn nhiệm trong năm không tính.

Quy tắc:
- TV độc lập HĐQT: chỉ đếm người được ghi rõ "độc lập"; "không điều hành" mà không ghi "độc lập" thì KHÔNG đếm.
- TV BKS sở hữu 0%: thành viên Ban kiểm soát có số cổ phần = 0 hoặc tỷ lệ 0%/0,00%. Công ty mô hình 1 tầng (không có BKS) → bks_total = null.
- Tổng % sở hữu HĐQT: cộng tỷ lệ sở hữu CÁ NHÂN của các thành viên HĐQT cuối năm (không cộng phần đại diện cho tổ chức). Không tìm được → null.
- Không đoán. Thiếu thì để null và nói rõ trong "note".

Trả về DUY NHẤT một JSON object:
{"board_total": int|null, "board_independent": int|null, "bks_total": int|null, "bks_zero": int|null,
 "board_own_pct": float|null (ví dụ 6.56 nghĩa là 6,56%), "own_date": "dd/mm/yyyy"|null,
 "members": [{"name": str, "body": "HDQT"|"BKS", "role": str, "independent": bool, "own_pct": float|null}],
 "pages": "các trang nguồn", "note": "ghi chú ngắn"}
"""


def call_claude(text: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("CG_MODEL", "claude-haiku-5-5"),
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT + "\n\n" + text}],
    )
    raw = msg.content[0].text
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {"note": "JSON parse fail: " + raw[:200]}


def mode_api(in_dir: Path, out_xlsx: Path):
    from openpyxl import Workbook, load_workbook
    hdr = ["Mã CK", "Năm BCTN", "Tổng TV HĐQT", "TV độc lập HĐQT", "% INED", "Tổng TV BKS",
           "TV BKS sở hữu 0%", "% BKS không sở hữu", "Tổng % sở hữu HĐQT", "Ngày số liệu SH",
           "Trang nguồn", "Ghi chú", "Chi tiết thành viên", "File"]
    if out_xlsx.exists():
        wb = load_workbook(out_xlsx); ws = wb.active
        done = {(r[13]) for r in ws.iter_rows(min_row=2, values_only=True)}
    else:
        wb = Workbook(); ws = wb.active; ws.title = "CG_data"; ws.append(hdr); done = set()
    for pdf in sorted(in_dir.glob("*.pdf")):
        if pdf.name in done:
            continue
        code, year = parse_name(pdf)
        texts = page_texts(pdf)
        pages = select_pages(texts)
        if not pages:
            ws.append([code, year] + ["N/A"] * 7 + ["", "", "Không tìm được trang phù hợp", "", pdf.name])
            wb.save(out_xlsx); print(f"{pdf.name}: không có trang phù hợp"); continue
        blob = "\n\n".join(f"=== TRANG {i+1} ===\n{texts[i]}" for i in pages)
        try:
            d = call_claude(blob)
        except Exception as e:
            d = {"note": f"API lỗi: {e}"}
        bt, bi = d.get("board_total"), d.get("board_independent")
        kt, kz = d.get("bks_total"), d.get("bks_zero")
        ined = round(bi / bt, 4) if bt and bi is not None else "N/A"
        pbks = round(kz / kt, 4) if kt and kz is not None else "N/A"
        own = d.get("board_own_pct")
        ws.append([code, year, bt if bt is not None else "N/A", bi if bi is not None else "N/A", ined,
                   kt if kt is not None else "N/A", kz if kz is not None else "N/A", pbks,
                   round(own / 100, 4) if own is not None else "N/A", d.get("own_date") or "",
                   d.get("pages") or ", ".join(str(i+1) for i in pages), d.get("note") or "",
                   json.dumps(d.get("members", []), ensure_ascii=False), pdf.name])
        for c in ws[ws.max_row]:
            if c.column in (5, 8, 9) and isinstance(c.value, float):
                c.number_format = "0.00%"
        wb.save(out_xlsx)
        print(f"{pdf.name}: HĐQT {bt}/{bi} độc lập, BKS {kt}/{kz} 0%, SH {own}% ({len(pages)} trang)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["slim", "api"])
    ap.add_argument("--in", dest="inp", required=True, help="thư mục chứa PDF BCTN")
    ap.add_argument("--out", required=True, help="slim: thư mục xuất; api: file .xlsx")
    a = ap.parse_args()
    (mode_slim if a.mode == "slim" else mode_api)(Path(a.inp), Path(a.out))
