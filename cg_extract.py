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

try:  # đọc ANTHROPIC_API_KEY từ file .env (nếu có); trên GitHub Actions dùng secret nên bỏ qua
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

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
        import pytesseract, shutil
        win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"   # cài bằng installer Windows thường không vào PATH
        if not shutil.which("tesseract") and os.path.exists(win):
            pytesseract.pytesseract.tesseract_cmd = win
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
    if code_score < 3:
        code_d = None
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
    """Ưu tiên: TÊN FILE (nguồn chính, mọi thông tin trong file thuộc mã/năm đó) > nội dung tài liệu > model.
    Trả về (giá trị, [cảnh báo]). Lệch nội dung chỉ để cảnh báo, không đổi giá trị."""
    flags = []
    if v_file is not None:
        v = v_file
        if v_doc is not None and v_doc != v_file and doc_strong:
            flags.append(f"{label} nội dung tài liệu ({v_doc}) khác tên file ({v_file}) – dùng theo tên file")
    elif v_doc is not None:
        v = v_doc
        flags.append(f"Tên file không có {label.lower()} – dùng theo nội dung tài liệu ({v_doc})")
    else:
        v = v_model
        flags.append(f"{label} chỉ lấy được từ model ({v_model}) – kiểm tra")
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


# ---------- Kiểm tra mỗi công ty đã đủ BCTN 2019-2025 chưa ----------
COV_YEARS = list(range(2019, 2026))
RE_MISS = re.compile(r"\s*\[Thiếu BCTN[^\]]*\]")
CHD = ["Mã CK", "Năm có BCTN", "Năm thiếu BCTN", "Trạng thái"]


def coverage_from_names(names):
    """names: tên file PDF → ({mã: {năm}}, [tên file không đọc được mã/năm])."""
    cov, bad = {}, []
    for n in names:
        code, year = parse_name(Path(n))
        if code and year:
            cov.setdefault(code, set()).add(year)
        else:
            bad.append(n)
    return cov, bad


def print_coverage(names):
    cov, bad = coverage_from_names(names)
    full = [c for c, ys in cov.items() if all(y in ys for y in COV_YEARS)]
    print(f"Kiểm tra BCTN {COV_YEARS[0]}-{COV_YEARS[-1]}: {len(cov)} công ty, {len(full)} đủ, {len(cov) - len(full)} thiếu")
    for c in sorted(cov):
        miss = [y for y in COV_YEARS if y not in cov[c]]
        if miss:
            print(f"  {c}: thiếu BCTN {', '.join(map(str, miss))}")
    for n in bad:
        print(f"  [không đọc được mã/năm từ tên file] {n}")


SGROUPS = ["% INED", "% BKS không sở hữu cổ phần", "Tổng % sở hữu HĐQT"]   # thứ tự khớp CG_data cột 5, 8, 9
SHDR = ["Mã CK"] + [f"{g} {y}" for g in SGROUPS for y in COV_YEARS]


def build_summary(wb):
    """Sheet chính 'Tong_hop': mỗi công ty một dòng; cột = % INED / % BKS không sở hữu / Tổng % sở hữu HĐQT theo từng năm
    2019-2025. Năm thiếu BCTN → ô để trống. Mọi thông tin khác (tổng TV, ghi chú, cảnh báo, trang nguồn) ở sheet phụ
    CG_data / Thanh_vien / Thieu_BCTN."""
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter
    ws = wb["CG_data"]
    data = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r and r[0] and isinstance(r[1], int) and (r[0], r[1]) not in data:
            data[(r[0], r[1])] = (r[4], r[7], r[8])
    if "Tong_hop" in wb.sheetnames:
        del wb["Tong_hop"]
    wt = wb.create_sheet("Tong_hop", 0)
    wt.append(SHDR)
    for c in wt[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    for code in sorted({k[0] for k in data}):
        row = [code]
        for gi in range(len(SGROUPS)):
            for y in COV_YEARS:
                v = data.get((code, y))
                row.append(v[gi] if v else None)
        wt.append(row)
        for cell in wt[wt.max_row][1:]:
            if isinstance(cell.value, float):
                cell.number_format = "0.00%"
    for j in range(2, len(SHDR) + 1):
        wt.column_dimensions[get_column_letter(j)].width = 12
    wt.freeze_panes = "B2"
    wb.active = 0


def apply_coverage(wb):
    """Ghi '[Thiếu BCTN: ...]' cuối cột Ghi chú của từng dòng công ty và lập sheet Thieu_BCTN (tính trên cả workbook)."""
    ws = wb["CG_data"]
    cov = {}
    for r in range(2, ws.max_row + 1):
        c, y = ws.cell(r, 1).value, ws.cell(r, 2).value
        if c and isinstance(y, int):
            cov.setdefault(c, set()).add(y)
    for r in range(2, ws.max_row + 1):
        note = RE_MISS.sub("", str(ws.cell(r, 12).value or ""))
        miss = [y for y in COV_YEARS if y not in cov.get(ws.cell(r, 1).value, set(COV_YEARS))]
        if miss:
            note = (note + f" [Thiếu BCTN: {', '.join(map(str, miss))}]").strip()
        ws.cell(r, 12).value = note
    if "Thieu_BCTN" in wb.sheetnames:
        del wb["Thieu_BCTN"]
    wt = wb.create_sheet("Thieu_BCTN")
    wt.append(CHD)
    for c in sorted(cov):
        miss = [y for y in COV_YEARS if y not in cov[c]]
        wt.append([c, ", ".join(str(y) for y in sorted(cov[c])), ", ".join(map(str, miss)),
                   ("Thiếu BCTN: " + ", ".join(map(str, miss))) if miss else "Đủ"])
    build_summary(wb)


# ---------- Chế độ API ----------
PROMPT = """Bạn là trợ lý trích dữ liệu quản trị công ty từ Báo cáo thường niên của công ty niêm yết Việt Nam.
Dưới đây là một khối "DÒNG NHẬN DIỆN" (các dòng có tên công ty/mã CK/năm) và text của các trang liên quan (mỗi trang bắt đầu bằng "=== TRANG n ===").
NĂM BÁO CÁO: year là năm tài chính được báo cáo. BCTN 2021 thường phát hành đầu/giữa 2022 và nhắc nhiều ngày tháng của 2022 — vẫn là năm 2021; bỏ qua ngày phát hành và ngày họp năm sau khi xác định year.
NGÀY THAM CHIẾU (ref_date, dd/mm/yyyy): thời điểm MUỘN NHẤT mà phần thông tin HĐQT/BKS/sở hữu trong báo cáo được công bố hoặc cập nhật (ví dụ "tại ngày 11/04/2025", "danh sách chốt ngày…", ngày phát hành/công bố báo cáo). Không có → null.

Quy tắc:
- ticker: mã chứng khoán của công ty (3 ký tự) theo khối "DÒNG NHẬN DIỆN" hoặc các trang. Không thấy → null.
- members: CHỈ liệt kê người có chức danh thành viên HĐQT (kể cả Chủ tịch, Phó Chủ tịch, thành viên độc lập/không điều hành) hoặc thành viên BKS. Phó Tổng Giám đốc, Kế toán trưởng, giám đốc các khối KHÔNG phải thành viên HĐQT trừ khi báo cáo ghi rõ họ cũng là thành viên HĐQT; một người đã thôi làm Phó TGĐ không có nghĩa là rời HĐQT.
  Liệt kê TẤT CẢ cá nhân từng là thành viên HĐQT hoặc BKS được nhắc trong báo cáo (kể cả người đã miễn nhiệm/từ nhiệm hoặc mới bổ nhiệm), mỗi người một dòng, kèm:
  appointed_date (ngày bắt đầu giữ chức trong nhiệm kỳ hiện tại; null nếu không nêu hoặc từ trước năm báo cáo),
  left_date (ngày miễn nhiệm/từ nhiệm; null nếu còn tại vị),
  reason (ngắn, nếu có thay đổi nhân sự),
  independent (true CHỈ KHI người này là "Thành viên HĐQT độc lập" của CHÍNH công ty đang báo cáo, được ghi ở chức danh/danh sách HĐQT của công ty này. Chức vụ độc lập tại CÔNG TY KHÁC (trong phần lý lịch, quá trình công tác, chức vụ đang nắm giữ ở tổ chức khác) KHÔNG được tính; "không điều hành" mà không ghi "độc lập" thì false),
  own_pct (tỷ lệ sở hữu cổ phần CÁ NHÂN đúng như BCTN in: nếu báo cáo đã làm tròn (lên hoặc xuống) thì GIỮ NGUYÊN số làm tròn đó, không tự tính lại từ số cổ phần; 5.76 nghĩa là 5,76%; 0 nếu ghi 0%; null nếu báo cáo không có tỷ lệ; không cộng phần đại diện cho tổ chức),
  shares (SỐ LƯỢNG cổ phiếu cá nhân đang nắm giữ nếu báo cáo nêu, số nguyên, ví dụ 1.250.000 → 1250000; 0 nếu ghi 0 cổ phiếu; null nếu báo cáo không nêu số lượng. Không cộng cổ phần đại diện tổ chức).
  Việc loại người đã rời/chưa nhậm chức do chương trình tự tính từ appointed_date/left_date và ref_date, nên hãy điền ngày chính xác.
- board_independent: nếu báo cáo nêu rõ số lượng (ví dụ "02 thành viên độc lập", "02/04 thành viên là độc lập") thì điền số đó (tại ngày tham chiếu); nếu không thì để null.
- Công ty mô hình 1 tầng (không có BKS, có Ủy ban kiểm toán) → không liệt kê BKS.
- Không đoán. Thiếu thì để null và nói rõ trong "note".
Điền kết quả vào tool ghi_ket_qua.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": ["string", "null"], "description": "Mã chứng khoán 3 ký tự, null nếu không thấy"},
        "year": {"type": ["integer", "null"], "description": "Năm báo cáo thường niên"},
        "ref_date": {"type": ["string", "null"], "description": "Ngày tham chiếu/công bố của thông tin HĐQT-BKS, dd/mm/yyyy"},
        "board_total": {"type": ["integer", "null"], "description": "Số TV HĐQT báo cáo nêu rõ (nếu có)"},
        "board_independent": {"type": ["integer", "null"], "description": "Số TV độc lập báo cáo nêu rõ (nếu có)"},
        "bks_total": {"type": ["integer", "null"]},
        "bks_zero": {"type": ["integer", "null"]},
        "board_own_pct": {"type": ["number", "null"], "description": "6.56 nghĩa là 6,56%"},
        "own_date": {"type": ["string", "null"], "description": "Ngày của số liệu sở hữu, dd/mm/yyyy"},
        "members": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "body": {"type": "string", "enum": ["HDQT", "BKS"]},
            "role": {"type": "string"}, "independent": {"type": "boolean", "description": "true chỉ khi là TV HĐQT độc lập của chính công ty đang báo cáo, không phải công ty khác"},
            "own_pct": {"type": ["number", "null"]},
            "shares": {"type": ["integer", "null"], "description": "Số cổ phiếu cá nhân nắm giữ, null nếu báo cáo không nêu"},
            "appointed_date": {"type": ["string", "null"]}, "left_date": {"type": ["string", "null"]},
            "reason": {"type": "string"}},
            "required": ["name", "body", "role", "independent", "own_pct", "shares", "appointed_date", "left_date", "reason"]}},
        "pages": {"type": "string", "description": "Các trang nguồn"},
        "note": {"type": "string", "description": "Ghi chú ngắn: thay đổi nhân sự, mâu thuẫn, thiếu số liệu"},
    },
    "required": ["ticker", "year", "ref_date", "board_total", "board_independent", "bks_total", "bks_zero",
                 "board_own_pct", "own_date", "members", "pages", "note"],
}


def normalize(d: dict) -> dict:
    """Model đôi khi trả members dạng chuỗi JSON hoặc lẫn phần tử không phải object."""
    mem = d.get("members")
    if isinstance(mem, str):
        try:
            mem = json.loads(mem)
        except Exception:
            mem = None
    if isinstance(mem, dict):
        mem = [mem]
    d["members"] = [m for m in (mem or []) if isinstance(m, dict)]
    return d


def call_claude(text: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(max_retries=6)
    kw = dict(
        model=os.environ.get("CG_MODEL", "claude-haiku-5-5"),
        max_tokens=8000,
        tools=[{"name": "ghi_ket_qua", "description": "Ghi kết quả trích dữ liệu quản trị công ty",
                "input_schema": SCHEMA}],
        messages=[{"role": "user", "content": PROMPT + "\n\n" + text}],
    )
    try:  # ép buộc gọi tool; một số model (vd. Sonnet 5.5) không hỗ trợ → chuyển sang auto
        msg = client.messages.create(tool_choice={"type": "tool", "name": "ghi_ket_qua"}, **kw)
    except anthropic.BadRequestError as e:
        if "tool_choice" not in str(e):
            raise
        msg = client.messages.create(tool_choice={"type": "auto"}, **kw)
    for b in msg.content:
        if getattr(b, "type", "") == "tool_use":
            return normalize(dict(b.input))
    return {"note": "Model không trả tool_use (stop_reason=%s)" % msg.stop_reason}


ERR_PREFIX = ("API lỗi", "JSON parse fail", "Model không trả", "LỖI")
HDR = ["Mã CK", "Năm BCTN", "Tổng TV HĐQT", "TV độc lập HĐQT", "% INED", "Tổng TV BKS",
       "TV BKS sở hữu 0%", "% BKS không sở hữu", "Tổng % sở hữu HĐQT", "Ngày số liệu SH",
       "Trang nguồn", "Ghi chú", "Chi tiết thành viên", "File"]


MHDR = ["Mã CK", "Năm BCTN", "Họ tên", "Cơ quan", "Chức vụ", "Độc lập", "Sở hữu cá nhân", "Bổ nhiệm",
        "Miễn nhiệm", "Ngày tham chiếu", "Tính vào số liệu", "Lý do / ghi chú", "File"]


def na(v):
    return v if v is not None else "N/A"


def parse_date(x):
    """'11/04/2025' | '11/4/2025' | '04/2025' -> datetime.date hoặc None."""
    import datetime as dt
    if not x:
        return None
    m = re.search(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(20\d{2}|19\d{2})", str(x))
    try:
        if m:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.search(r"(\d{1,2})\s*[/.\-]\s*(20\d{2})", str(x))
        if m:
            return dt.date(int(m.group(2)), int(m.group(1)), 1)
    except ValueError:
        return None
    return None


def split_members(members, ref):
    """Tách thành viên theo ngày tham chiếu: còn tại vị / bị loại (kèm lý do)."""
    keep, drop = [], []
    for m in members:
        rl = fold(m.get("role") or "").lower()
        need = ("hdqt", "hoi dong quan tri", "chu tich", "thanh vien", "uy vien", "doc lap")
        exec_first = rl.startswith(("pho tong giam doc", "tong giam doc", "ke toan truong", "pho giam doc", "giam doc"))
        negated = bool(re.search(r"khong\s+(?:nen|phai|thuoc|duoc\s+nhac|ro|co\s+ten)", rl))
        # BKS: nằm trong danh sách BKS là tính, không xét từ khóa chức danh (vd. "Thành viên chuyên trách")
        if m.get("body") != "BKS" and (exec_first or negated or not any(k in rl for k in need)):
            drop.append((m, f"Chức vụ '{m.get('role')}' không thuộc {m.get('body')} (có thể là Ban điều hành)"))
            continue
        ap, lf = parse_date(m.get("appointed_date")), parse_date(m.get("left_date"))
        why = None
        if ref and lf and lf <= ref:
            why = f"Miễn nhiệm/từ nhiệm {lf:%d/%m/%Y}, trước ngày tham chiếu {ref:%d/%m/%Y}"
        elif ref and ap and ap > ref:
            why = f"Bổ nhiệm {ap:%d/%m/%Y}, sau ngày tham chiếu {ref:%d/%m/%Y}"
        elif not ref and lf:
            why = f"Đã miễn nhiệm/từ nhiệm {lf:%d/%m/%Y} (không có ngày tham chiếu)"
        (drop if why else keep).append((m, why))
    return [m for m, _ in keep], [(m, w) for m, w in drop]


def process_one(pdf: Path, seen: set):
    """Trả về (row, summary). Ném exception nếu lỗi nặng (caller ghi dòng LỖI)."""
    code_f, year_f = parse_name(pdf)
    texts = page_texts(pdf)
    code_d, year_d, c_strong, y_strong, id_lines = detect_identity(texts)
    pages = select_pages(texts)
    process_one.last_members = []
    if not pages:
        return ([code_d or code_f or pdf.stem[:3].upper(), year_d or year_f] + ["N/A"] * 7 +
                ["", "", "Không tìm được trang phù hợp (file scan chưa OCR được? kiểm tra lại)", "", pdf.name],
                "không có trang phù hợp")
    blob = "=== DÒNG NHẬN DIỆN CÔNG TY / NĂM (trích từ phần đầu báo cáo) ===\n" + "\n".join(id_lines)
    blob += "\n\n" + "\n\n".join(f"=== TRANG {i+1} ===\n{texts[i]}" for i in pages)
    d = {}
    for attempt in range(2):                    # gọi lại một lần nếu kết quả hỏng/không có danh sách thành viên
        try:
            d = call_claude(blob)
        except Exception as e:
            d = {"note": f"API lỗi: {e}"}
        if d.get("members"):
            break

    code_m = (d.get("ticker") or "").strip().upper() or None
    year_m = d.get("year")
    code, flags = choose("Mã CK", code_f, code_d, code_m, c_strong)
    year, fl_y = choose("Năm", year_f, year_d, year_m, y_strong)
    flags += fl_y
    code = code or pdf.stem[:3].upper()
    if (code, year) in seen:
        flags.append("Trùng mã CK-năm với dòng khác")
    seen.add((code, year))

    # ---- Ngày tham chiếu: ngày công bố/cập nhật trong báo cáo; thiếu → ngày số liệu sở hữu; thiếu nữa → 31/12 của năm ----
    ref, own_dt = parse_date(d.get("ref_date")), parse_date(d.get("own_date"))
    if ref is None and own_dt:
        ref = own_dt
        flags.append(f"Báo cáo không nêu ngày tham chiếu → dùng ngày số liệu sở hữu {ref:%d/%m/%Y}")
    elif ref and own_dt and own_dt > ref:
        flags.append(f"Ngày tham chiếu model chọn {ref:%d/%m/%Y} sớm hơn ngày số liệu sở hữu {own_dt:%d/%m/%Y} → dùng {own_dt:%d/%m/%Y}")
        ref = own_dt
    if ref is None and year:
        import datetime as dt
        ref = dt.date(int(year), 12, 31)
        flags.append(f"Không xác định được ngày tham chiếu → dùng 31/12/{year}")
    members = d.get("members") or []
    in_office, dropped = split_members(members, ref)
    bmem = [m for m in in_office if m.get("body") == "BKS"]
    mem = [m for m in in_office if m.get("body") == "HDQT"]

    # ---- HĐQT ----
    bt_model, bi_model = d.get("board_total"), d.get("board_independent")
    if mem:
        bt = len(mem)
        bi_calc = sum(1 for m in mem if m.get("independent"))
        bi = bi_calc            # ai được gắn mác "độc lập" thì tính là độc lập; không dùng số model tự nêu
        if bt_model is not None and bt_model != bt:
            flags.append(f"Báo cáo nêu {bt_model} TV HĐQT, danh sách tại ngày tham chiếu có {bt} – kiểm tra")
        if bi_model is not None and bi_model != bi_calc:
            flags.append(f"Báo cáo nêu {bi_model} TV độc lập nhưng danh sách có {bi_calc} người gắn mác độc lập – dùng {bi_calc} – kiểm tra")
    else:
        bt, bi = bt_model, bi_model
    # Tổng tỷ lệ sở hữu cá nhân HĐQT (chỉ người còn tại vị)
    own = d.get("board_own_pct")
    if mem and all(m.get("own_pct") is not None for m in mem):
        own = round(sum(float(m["own_pct"]) for m in mem), 4)
    elif mem:
        miss = ", ".join(m.get("name", "?") for m in mem if m.get("own_pct") is None)
        flags.append(f"Thiếu số liệu sở hữu của TV HĐQT: {miss} → tổng % sở hữu để N/A")
        own = None
    # ---- BKS ----
    kt, kz = d.get("bks_total"), d.get("bks_zero")
    if bmem:
        kt = len(bmem)
        # Quy tắc: tỷ lệ in ra là 0,00% → 0% (kể cả số CP khác 0, do làm tròn); tỷ lệ khác 0/không có
        # mà báo cáo có số lượng CP → 0 CP mới là 0%; chỉ có tỷ lệ khác 0 → khác 0
        def is_zero(m):
            if m.get("own_pct") is not None and float(m["own_pct"]) == 0:
                return True
            if m.get("shares") is not None:
                return int(m["shares"]) == 0
            if m.get("own_pct") is not None:
                return False
            return None
        unk = [m for m in bmem if is_zero(m) is None]
        if unk:
            flags.append("Báo cáo không công bố sở hữu của TV BKS: " + ", ".join(m.get("name", "?") for m in unk) +
                         " → BKS 0% để N/A (kiểm tra thủ công)")
            kz = None
        else:
            kz = sum(1 for m in bmem if is_zero(m))
    # ---- Người bị loại ----
    for m, why in dropped:
        flags.append(f"Loại {m.get('name','?')} ({m.get('body')}): {why}")
    # ---- Mọi người có ngày bổ nhiệm/miễn nhiệm (kể cả còn tại vị) để người dùng tự xem xét ----
    dated = [f"{m.get('name','?')} ({m.get('body')}"
             + (f", bổ nhiệm {m['appointed_date']}" if m.get("appointed_date") else "")
             + (f", miễn nhiệm {m['left_date']}" if m.get("left_date") else "") + ")"
             for m in members if m.get("appointed_date") or m.get("left_date")]
    if dated:
        flags.append("CẦN XEM – có ngày bổ nhiệm/miễn nhiệm: " + "; ".join(dated))

    # Dữ liệu cho sheet thành viên
    mrows = []
    kept_ids = {id(m) for m in in_office}
    for m in members:
        why = next((w for mm, w in dropped if mm is m), "")
        mrows.append([code, year, m.get("name"), m.get("body"), m.get("role"),
                      "Có" if m.get("independent") else "Không",
                      (m["own_pct"] / 100) if isinstance(m.get("own_pct"), (int, float)) else "N/A",
                      m.get("appointed_date") or "", m.get("left_date") or "", f"{ref:%d/%m/%Y}" if ref else "",
                      "Có" if id(m) in kept_ids else "KHÔNG", (why or (m.get("reason") or "")) + (f" | SL CP: {m['shares']:,}" if isinstance(m.get("shares"), int) else ""), pdf.name])

    ined = round(bi / bt, 4) if bt and bi is not None else "N/A"
    pbks = round(kz / kt, 4) if kt and kz is not None else "N/A"
    note = d.get("note") or ""
    if note.startswith(ERR_PREFIX):
        full_note = note                      # giữ nguyên để lần sau chạy lại
    else:
        full_note = ("".join(f"[{f}] " for f in flags) + note).strip()
    row = [code, year, na(bt), na(bi), ined, na(kt), na(kz), pbks,
           round(own / 100, 4) if own is not None else "N/A", d.get("own_date") or (f"{ref:%d/%m/%Y}" if ref else ""),
           d.get("pages") or ", ".join(str(i+1) for i in pages), full_note,
           json.dumps([m for m in members if id(m) in kept_ids], ensure_ascii=False), pdf.name]
    process_one.last_members = mrows
    return row, f"HĐQT {bt}/{bi} độc lập, BKS {kt}/{kz} 0%, SH {own}% ({len(pages)} trang)" + \
        (f" | {len(flags)} cảnh báo" if flags else "")


def mode_api(in_dirs, out_xlsx: Path):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import PatternFill
    if out_xlsx.exists():
        wb = load_workbook(out_xlsx); ws = wb["CG_data"]   # sheet đầu tiên giờ là Tong_hop
        for row in range(ws.max_row, 1, -1):   # xoá dòng lỗi lần trước để chạy lại
            if str(ws.cell(row, 12).value or "").startswith(ERR_PREFIX):
                fname = ws.cell(row, 14).value
                ws.delete_rows(row)
                if "Thanh_vien" in wb.sheetnames:
                    wsm = wb["Thanh_vien"]
                    for r2 in range(wsm.max_row, 1, -1):
                        if wsm.cell(r2, 13).value == fname:
                            wsm.delete_rows(r2)
    else:
        wb = Workbook(); ws = wb.active; ws.title = "CG_data"; ws.append(HDR)
    wm = wb["Thanh_vien"] if "Thanh_vien" in wb.sheetnames else wb.create_sheet("Thanh_vien")
    if wm["A1"].value != MHDR[0]:
        for j, h in enumerate(MHDR, 1):          # ghi thẳng vào dòng 1 (append sẽ để trống dòng 1)
            wm.cell(1, j, h)
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    done = {r[13] for r in rows}
    seen = {(r[0], r[1]) for r in rows}

    pdfs = list_pdfs(in_dirs)
    print(f"{len(pdfs)} file PDF, {len([p for p in pdfs if p.name in done])} đã có trong Excel")
    print_coverage([p.name for p in pdfs])          # quét thử trước: công ty nào thiếu năm nào
    for pdf in pdfs:
        if pdf.name in done:
            continue
        process_one.last_members = []
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
        for mr in getattr(process_one, "last_members", []):
            wm.append(mr)
            if isinstance(mr[6], float):
                wm.cell(wm.max_row, 7).number_format = "0.00%"
            if mr[10] == "KHÔNG":
                for c in wm[wm.max_row]:
                    c.fill = PatternFill("solid", fgColor="FFF2CC")
        wb.save(out_xlsx)
        print(f"{pdf.name} → {row[0]} {row[1]}: {summary}")
    apply_coverage(wb)
    wb.save(out_xlsx)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["slim", "api"])
    ap.add_argument("--in", dest="inp", nargs="+", required=True, help="thư mục chứa PDF BCTN (một hoặc nhiều)")
    ap.add_argument("--out", required=True, help="slim: thư mục xuất; api: file .xlsx")
    a = ap.parse_args()
    (mode_slim if a.mode == "slim" else mode_api)(a.inp, Path(a.out))
