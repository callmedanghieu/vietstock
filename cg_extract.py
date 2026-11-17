#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cg_extract.py — Lọc trang quản trị công ty từ Báo cáo thường niên (BCTN) và trích 3 chỉ tiêu CG.

Cách dùng:
  pip install -r requirements.txt     (pytesseract + poppler + tesseract-ocr-vie nếu có file scan)

  # 1) Chỉ lọc trang, tạo PDF mỏng để upload lên chat (không cần API key)
  python cg_extract.py slim --in ./bctn --out ./slim

  # 2) Tự động trích bằng Claude API, ghi Excel (cần ANTHROPIC_API_KEY)
  python cg_extract.py api --in ./bctn --out ./ket_qua.xlsx
  (có thể đưa nhiều thư mục: --in ./bctn ./bctn_release)

Không cần đổi tên file: mã CK và năm được đọc từ NỘI DUNG tài liệu ("Mã chứng khoán: XXX",
"Báo cáo thường niên 20xx"). Tên file (nếu có dạng VNM_2023.pdf) chỉ dùng để đối chiếu; lệch thì ghi cảnh báo.
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
OCR_DPI = 200


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    # một số BCTN xuất text lỗi "đ" thành ")" hoặc "$"
    s = re.sub(r"[)\$#%&'!](?=[ôồổỗộơờởỡợ])", "đ", s)
    return re.sub(r"\s+", " ", s)


# ---------- Đọc PDF (có OCR cho trang scan) ----------
def ocr_page(args) -> str:
    pdf_path, pageno = args
    try:
        from pdf2image import convert_from_path
        import pytesseract
        img = convert_from_path(str(pdf_path), dpi=OCR_DPI, first_page=pageno, last_page=pageno)[0]
        return pytesseract.image_to_string(img, lang="vie+eng")
    except Exception as e:  # không có tesseract → bỏ qua, báo ra stderr
        print(f"  [OCR bỏ qua trang {pageno}: {e}]", file=sys.stderr)
        return ""


def page_texts(pdf_path: Path):
    """Trả về list text từng trang; OCR (song song) những trang không có text layer."""
    import pdfplumber
    texts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for p in pdf.pages:
            try:
                texts.append(p.extract_text() or "")
            except Exception:
                texts.append("")
    need = [i for i, t in enumerate(texts) if len(t.strip()) < 40]
    if need:
        print(f"  OCR {len(need)}/{len(texts)} trang...", file=sys.stderr)
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=max(1, os.cpu_count() or 1)) as ex:
            for i, t in zip(need, ex.map(ocr_page, [(pdf_path, i + 1) for i in need])):
                texts[i] = t
    return texts


# ---------- Chọn trang ----------
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


# ---------- Nhận diện mã CK / năm từ NỘI DUNG tài liệu ----------
def fold(s: str) -> str:
    """Bỏ dấu tiếng Việt nhưng giữ chữ hoa/thường (để tìm 'Mã chứng khoán: XXX')."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.replace("đ", "d").replace("Đ", "D")


LABEL = (r"(?i:ma\s*(?:chung\s*khoan|co\s*phieu|ck)(?:\s*niem\s*yet)?(?:\s*\([^)]{1,25}\))?"
         r"(?:\s*/\s*(?:securities\s*(?:symbol|code)|stock\s*code|ticker(?:\s*symbol)?))?"
         r"|stock\s*code|securities\s*(?:symbol|code)|ticker(?:\s*symbol)?)")
TK = r"([A-Z][A-Z0-9]{2})"
# (regex, trọng số): nhãn → mã; mã → nhãn; nhãn ở cuối dòng và mã ở cuối dòng kế tiếp (bố cục 2 cột); tín hiệu phụ
RE_TICKERS = [
    (re.compile(LABEL + r"\s*(?:(?i:la|is)\s+)?[:\-–]?\s*(?:(?:HOSE|HNX|UPCOM|HSX)\s*[:\-–]\s*)?\(?" + TK + r"\b"), 3),
    (re.compile(r"\b" + TK + r"\s*[-–:]?\s*" + LABEL), 3),
    (re.compile(LABEL + r"\s*:?[ \t]*\n[^\n]*?\b" + TK + r"[ \t]*$", re.M), 3),
    (re.compile(r"(?i:phat\s*hanh)\s*\(\s*" + TK + r"\s*\)"), 2),
    (re.compile(r"(?i:co\s*phieu)\s+" + TK + r"\b"), 1),
]
RE_YEAR = re.compile(r"(?i)(?:bao\s*cao\s*thuong\s*nien|annual\s*report)[^0-9]{0,80}?(20[0-3]\d)\b")
NOT_TICKER = {"HNX", "HSX", "HOS", "UPC", "OTC", "ETF", "VND", "USD", "EUR", "JPY", "CTC", "CEO", "CFO", "COO",
              "BKS", "TGD", "VSD", "SSC", "ESG", "KPI", "GDP", "CPI", "EPS", "ROA", "ROE", "IPO", "ERP", "CRM",
              "BOD", "BOM", "VAS", "CIT", "VAT", "PIT", "FDI", "ODA", "MOU", "CSR", "ISO", "PDF", "TAP", "CTY"}
ID_KEYS = ["bao cao thuong nien", "annual report", "ma chung khoan", "ma co phieu", "ma ck",
           "stock code", "ticker", "ten cong ty", "ten giao dich", "ten doanh nghiep"]


def detect_identity(texts):
    """Trả về (mã CK, năm, mã-có-chắc-không, năm-có-chắc-không, các dòng nhận diện gửi kèm cho model)."""
    from collections import Counter
    tick, year = Counter(), Counter()
    for t in texts:
        f = fold(t)
        for rx, w in RE_TICKERS:
            for m in rx.finditer(f):
                if m.group(1) not in NOT_TICKER:
                    tick[m.group(1)] += w
        for m in RE_YEAR.finditer(f):
            year[int(m.group(1))] += 1
    code_d, code_score = (tick.most_common(1)[0] if tick else (None, 0))
    year_d, year_strong = (year.most_common(1)[0][0], True) if year else (None, False)
    if year_d is None:  # dự phòng: năm đứng một mình ở 3 trang đầu (trang bìa)
        c = Counter(int(y) for t in texts[:3] for y in re.findall(r"(?<!\d)(20[0-3]\d)(?!\d)", t))
        year_d = c.most_common(1)[0][0] if c else None
    lines = []
    for t in texts[:15]:
        for ln in t.splitlines():
            if any(k in fold(ln).lower() for k in ID_KEYS):
                lines.append(ln.strip()[:160])
            if len(lines) >= 25:
                break
        if len(lines) >= 25:
            break
    return code_d, year_d, code_score >= 3, year_strong, lines


def choose(label, v_file, v_doc, v_model, doc_strong=True):
    """Ưu tiên: nội dung tài liệu (nếu nhận diện chắc) > tên file > model. Trả về (giá trị, [cảnh báo])."""
    flags = []
    if v_doc is not None and v_file is not None:
        if v_doc == v_file:
            v = v_doc
        elif doc_strong:
            v = v_doc
            flags.append(f"{label} theo tên file ({v_file}) khác nội dung tài liệu ({v_doc}) – dùng theo tài liệu")
        else:
            v = v_file
            flags.append(f"{label} nhận diện yếu từ tài liệu ({v_doc}) khác tên file ({v_file}) – dùng theo tên file")
    elif v_doc is not None:
        v = v_doc
        if not doc_strong:
            flags.append(f"{label} nhận diện yếu từ tài liệu ({v_doc}) – kiểm tra")
    elif v_file is not None:
        v = v_file
        flags.append(f"Không tìm thấy {label.lower()} trong tài liệu – dùng theo tên file ({v_file})")
    else:
        v = v_model
        flags.append(f"{label} chỉ lấy được từ model ({v_model}) – kiểm tra")
    if v_model is not None and v is not None and v_model != v:
        flags.append(f"Model đọc {label.lower()} = {v_model}, khác giá trị dùng ({v}) – kiểm tra")
    return v, flags


# ---------- Mã CK / năm từ tên file ----------
STOP_TOKENS = {"BAO", "CAO", "NAM", "VAN", "BAN", "TAP", "CTY", "CTC", "PDF", "DOC", "THE", "AND",
               "VER", "NEW", "OLD", "BCT", "HNX", "HSX", "UPC", "VSD", "SSC", "FIN", "ENG", "VIE"}


def parse_name(pdf_path: Path):
    """'VNM_2023.pdf' | 'VNM2023.pdf' | 'VNM_Baocaothuongnien_2023.pdf' | 'Bao cao thuong nien 2023 - VNM.pdf'"""
    stem = unicodedata.normalize("NFKD", pdf_path.stem).encode("ascii", "ignore").decode().upper()
    year = None
    m = re.search(r"(?<!\d)(20[0-3]\d)(?!\d)", stem)
    if m:
        year = int(m.group(1))
    code = None
    for t in re.split(r"[^A-Z0-9]+", stem):
        m2 = re.fullmatch(r"([A-Z][A-Z0-9]{2})(20[0-3]\d)?", t)
        if m2 and m2.group(1) not in STOP_TOKENS:
            code = m2.group(1)
            if year is None and m2.group(2):
                year = int(m2.group(2))
            break
    return code, year


# ---------- Chế độ SLIM ----------
def mode_slim(in_dirs, out_dir: Path):
    from pypdf import PdfReader, PdfWriter
    out_dir.mkdir(parents=True, exist_ok=True)
    for pdf in list_pdfs(in_dirs):
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


def list_pdfs(in_dirs):
    out = []
    for d in in_dirs:
        d = Path(d)
        if d.is_dir():
            out += [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    return sorted(out, key=lambda p: p.name)


# ---------- Chế độ API ----------
PROMPT = """Bạn là trợ lý trích dữ liệu quản trị công ty từ Báo cáo thường niên của công ty niêm yết Việt Nam.
Dưới đây là một khối "DÒNG NHẬN DIỆN" (các dòng có tên công ty/mã CK/năm) và text của các trang liên quan (mỗi trang bắt đầu bằng "=== TRANG n ===").
Lấy số liệu tại thời điểm CUỐI NĂM báo cáo; người đã miễn nhiệm trong năm không tính.

Quy tắc:
- ticker: mã chứng khoán của công ty (3 ký tự) theo khối "DÒNG NHẬN DIỆN" hoặc các trang; year: năm của báo cáo thường niên (năm được báo cáo, không phải năm phát hành). Không thấy → null.
- TV độc lập HĐQT: đếm người được ghi rõ "độc lập" ở BẤT KỲ chỗ nào trong các trang (tiêu đề bio, bảng HĐQT, danh sách trong BCTC, nghị quyết). "Không điều hành" mà không ghi "độc lập" thì KHÔNG đếm. Nếu báo cáo nêu rõ số lượng (ví dụ "02 thành viên độc lập", "02/04 thành viên là độc lập") thì ưu tiên số đó và nêu tên những người xác định được.
- TV BKS sở hữu 0%: thành viên Ban kiểm soát có số cổ phần = 0 hoặc tỷ lệ 0%/0,00%. Công ty mô hình 1 tầng (không có BKS, có Ủy ban kiểm toán) → bks_total = null.
- Tổng % sở hữu HĐQT: cộng tỷ lệ sở hữu CÁ NHÂN của các thành viên HĐQT cuối năm (không cộng phần đại diện cho tổ chức). Không tìm được → null.
- members: liệt kê TỪNG thành viên HĐQT và BKS tại cuối năm với tỷ lệ sở hữu cá nhân (own_pct, ví dụ 5.76 nghĩa là 5,76%; 0 nếu ghi 0%; null nếu không có số liệu).
- Không đoán. Thiếu thì để null và nói rõ trong "note".
Điền kết quả vào tool ghi_ket_qua.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": ["string", "null"], "description": "Mã chứng khoán 3 ký tự, null nếu không thấy"},
        "year": {"type": ["integer", "null"], "description": "Năm báo cáo thường niên"},
        "board_total": {"type": ["integer", "null"]},
        "board_independent": {"type": ["integer", "null"]},
        "bks_total": {"type": ["integer", "null"]},
        "bks_zero": {"type": ["integer", "null"]},
        "board_own_pct": {"type": ["number", "null"], "description": "6.56 nghĩa là 6,56%"},
        "own_date": {"type": ["string", "null"], "description": "Ngày của số liệu sở hữu, dd/mm/yyyy"},
        "members": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "body": {"type": "string", "enum": ["HDQT", "BKS"]},
            "role": {"type": "string"}, "independent": {"type": "boolean"},
            "own_pct": {"type": ["number", "null"]}},
            "required": ["name", "body", "role", "independent", "own_pct"]}},
        "pages": {"type": "string", "description": "Các trang nguồn"},
        "note": {"type": "string", "description": "Ghi chú ngắn: thay đổi nhân sự, mâu thuẫn, thiếu số liệu"},
    },
    "required": ["ticker", "year", "board_total", "board_independent", "bks_total", "bks_zero",
                 "board_own_pct", "own_date", "members", "pages", "note"],
}


def call_claude(text: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("CG_MODEL", "claude-haiku-5-5"),
        max_tokens=8000,
        tools=[{"name": "ghi_ket_qua", "description": "Ghi kết quả trích dữ liệu quản trị công ty",
                "input_schema": SCHEMA}],
        tool_choice={"type": "tool", "name": "ghi_ket_qua"},
        messages=[{"role": "user", "content": PROMPT + "\n\n" + text}],
    )
    for b in msg.content:
        if getattr(b, "type", "") == "tool_use":
            return dict(b.input)
    return {"note": "Model không trả tool_use (stop_reason=%s)" % msg.stop_reason}


ERR_PREFIX = ("API lỗi", "JSON parse fail", "Model không trả", "LỖI")
HDR = ["Mã CK", "Năm BCTN", "Tổng TV HĐQT", "TV độc lập HĐQT", "% INED", "Tổng TV BKS",
       "TV BKS sở hữu 0%", "% BKS không sở hữu", "Tổng % sở hữu HĐQT", "Ngày số liệu SH",
       "Trang nguồn", "Ghi chú", "Chi tiết thành viên", "File"]


def na(v):
    return v if v is not None else "N/A"


def process_one(pdf: Path, seen: set):
    """Trả về (row, summary). Ném exception nếu lỗi nặng (caller ghi dòng LỖI)."""
    code_f, year_f = parse_name(pdf)
    texts = page_texts(pdf)
    code_d, year_d, c_strong, y_strong, id_lines = detect_identity(texts)
    pages = select_pages(texts)
    if not pages:
        return ([code_d or code_f or pdf.stem[:3].upper(), year_d or year_f] + ["N/A"] * 7 +
                ["", "", "Không tìm được trang phù hợp (file scan chưa OCR được? kiểm tra lại)", "", pdf.name],
                "không có trang phù hợp")
    blob = "=== DÒNG NHẬN DIỆN CÔNG TY / NĂM (trích từ phần đầu báo cáo) ===\n" + "\n".join(id_lines)
    blob += "\n\n" + "\n\n".join(f"=== TRANG {i+1} ===\n{texts[i]}" for i in pages)
    try:
        d = call_claude(blob)
    except Exception as e:
        d = {"note": f"API lỗi: {e}"}

    code_m = (d.get("ticker") or "").strip().upper() or None
    year_m = d.get("year")
    code, flags = choose("Mã CK", code_f, code_d, code_m, c_strong)
    year, fl_y = choose("Năm", year_f, year_d, year_m, y_strong)
    flags += fl_y
    code = code or pdf.stem[:3].upper()
    if (code, year) in seen:
        flags.append("Trùng mã CK-năm với dòng khác")
    seen.add((code, year))

    bt, bi = d.get("board_total"), d.get("board_independent")
    kt, kz = d.get("bks_total"), d.get("bks_zero")
    members = d.get("members") or []
    # Tự đếm TV BKS sở hữu 0% từ danh sách (tin cậy hơn số model điền)
    bmem = [m for m in members if m.get("body") == "BKS"]
    if bmem and all(m.get("own_pct") is not None for m in bmem):
        kz_calc = sum(1 for m in bmem if float(m["own_pct"]) == 0)
        if kz != kz_calc:
            flags.append(f"BKS 0% đếm lại từ danh sách = {kz_calc} (model điền {kz})")
        kz = kz_calc
        if kt is not None and len(bmem) != kt:
            flags.append(f"Số TV BKS trong danh sách ({len(bmem)}) khác bks_total ({kt}) – kiểm tra")
    # Tự cộng tỷ lệ sở hữu cá nhân HĐQT từ danh sách thành viên
    own = d.get("board_own_pct")
    mem = [m for m in members if m.get("body") == "HDQT"]
    if mem and all(m.get("own_pct") is not None for m in mem):
        own_calc = round(sum(float(m["own_pct"]) for m in mem), 4)
        if own is None or abs(own_calc - float(own)) > 0.01:
            flags.append(f"Tổng SH HĐQT tính lại từ danh sách TV = {own_calc}% (model điền {own})")
        own = own_calc
    if mem and bt is not None and len(mem) != bt:
        flags.append(f"Số TV HĐQT trong danh sách ({len(mem)}) khác board_total ({bt}) – kiểm tra")

    ined = round(bi / bt, 4) if bt and bi is not None else "N/A"
    pbks = round(kz / kt, 4) if kt and kz is not None else "N/A"
    note = d.get("note") or ""
    if note.startswith(ERR_PREFIX):
        full_note = note                      # giữ nguyên để lần sau chạy lại
    else:
        full_note = ("".join(f"[{f}] " for f in flags) + note).strip()
    row = [code, year, na(bt), na(bi), ined, na(kt), na(kz), pbks,
           round(own / 100, 4) if own is not None else "N/A", d.get("own_date") or "",
           d.get("pages") or ", ".join(str(i+1) for i in pages), full_note,
           json.dumps(members, ensure_ascii=False), pdf.name]
    return row, f"HĐQT {bt}/{bi} độc lập, BKS {kt}/{kz} 0%, SH {own}% ({len(pages)} trang)" + \
        (f" | {len(flags)} cảnh báo" if flags else "")


def mode_api(in_dirs, out_xlsx: Path):
    from openpyxl import Workbook, load_workbook
    if out_xlsx.exists():
        wb = load_workbook(out_xlsx); ws = wb.active
        for row in range(ws.max_row, 1, -1):   # xoá dòng lỗi lần trước để chạy lại
            if str(ws.cell(row, 12).value or "").startswith(ERR_PREFIX):
                ws.delete_rows(row)
    else:
        wb = Workbook(); ws = wb.active; ws.title = "CG_data"; ws.append(HDR)
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    done = {r[13] for r in rows}
    seen = {(r[0], r[1]) for r in rows}

    pdfs = list_pdfs(in_dirs)
    print(f"{len(pdfs)} file PDF, {len([p for p in pdfs if p.name in done])} đã có trong Excel")
    for pdf in pdfs:
        if pdf.name in done:
            continue
        try:
            row, summary = process_one(pdf, seen)
        except Exception as e:
            code_f, year_f = parse_name(pdf)
            row = [code_f or pdf.stem[:3].upper(), year_f] + ["N/A"] * 7 + ["", "", f"LỖI xử lý file: {e}", "", pdf.name]
            summary = f"LỖI {e}"
        ws.append(row)
        for c in ws[ws.max_row]:
            if c.column in (5, 8, 9) and isinstance(c.value, float):
                c.number_format = "0.00%"
        wb.save(out_xlsx)
        print(f"{pdf.name} → {row[0]} {row[1]}: {summary}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["slim", "api"])
    ap.add_argument("--in", dest="inp", nargs="+", required=True, help="thư mục chứa PDF BCTN (một hoặc nhiều)")
    ap.add_argument("--out", required=True, help="slim: thư mục xuất; api: file .xlsx")
    a = ap.parse_args()
    (mode_slim if a.mode == "slim" else mode_api)(a.inp, Path(a.out))
