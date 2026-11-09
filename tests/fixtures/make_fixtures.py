"""Generates SYNTHETIC test PDFs (fictional company 'ABC'). They are NOT real annual reports and must never be
used as research data. Run:  python -m tests.fixtures.make_fixtures [outdir]"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
pdfmetrics.registerFont(TTFont("DJ", FONT))
ST = ParagraphStyle("n", fontName="DJ", fontSize=9, leading=12)
H = ParagraphStyle("h", fontName="DJ", fontSize=13, leading=16, spaceAfter=6)


def P(t, st=ST):
    return Paragraph(t, st)


def grid(rows, widths=None):
    t = Table([[P(str(c)) for c in r] for r in rows], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    return t


HDR = ["STT", "Họ và tên", "Chức vụ", "Số cổ phiếu sở hữu", "Tỷ lệ sở hữu", "Ngày bổ nhiệm", "Ngày miễn nhiệm"]
W = [30, 100, 120, 80, 60, 70, 70]


def clean_2024(path: Path, ticker: str = "ABC"):
    d = SimpleDocTemplate(str(path), pagesize=A4)
    s = [P("BÁO CÁO THƯỜNG NIÊN 2024", H), P(f"Công ty Cổ phần Ví Dụ {ticker} (SYNTHETIC TEST DOCUMENT)"),
         P(f"Mã chứng khoán: {ticker}. Báo cáo thường niên năm 2024, phát hành tháng 03/2025."), PageBreak(),
         P("Cơ cấu Hội đồng quản trị", H),
         P("Danh sách thành viên Hội đồng quản trị tại ngày 17/03/2025:"),
         grid([HDR,
               ["1", "Nguyễn Văn An", "Chủ tịch HĐQT", "1.200.000", "2,40%", "", ""],
               ["2", "Trần Thị Bình", "Thành viên HĐQT độc lập", "0", "0,00%", "", ""],
               ["3", "Lê Văn Cường", "Thành viên HĐQT", "500.000", "1,00%", "", ""],
               ["4", "Phạm Thị Dung", "Thành viên HĐQT độc lập", "10.000", "0,02%", "", ""],
               ["5", "Hoàng Văn Em", "Thành viên HĐQT", "300.000", "0,60%", "25/04/2024", ""],
               ["", "Tổng cộng", "", "2.010.000", "4,02%", "", ""]], W),
         Spacer(1, 12), P("Thành viên HĐQT đã miễn nhiệm trong năm 2024:"),
         grid([HDR, ["1", "Vũ Văn Giang", "Thành viên HĐQT", "100.000", "0,20%", "", "25/04/2024"]], W),
         PageBreak(), P("Ban Kiểm soát", H), P("Danh sách thành viên Ban Kiểm soát tại ngày 17/03/2025:"),
         grid([HDR,
               ["1", "Nguyễn Thị Hoa", "Trưởng Ban Kiểm soát", "0", "0,00%", "", ""],
               ["2", "Mai Thị Lan", "Thành viên Ban Kiểm soát", "", "0,00%", "", ""],
               ["3", "Đinh Văn Minh", "Thành viên Ban Kiểm soát", "2.000", "0,004%", "", ""]], W)]
    d.build(s)


def messy_2023(path: Path):
    """No composition date; '-' cells; related-person column; narrative aggregate conflicting with the sum."""
    hdr = ["STT", "Họ và tên", "Chức vụ", "Số cổ phiếu sở hữu cá nhân", "Tỷ lệ sở hữu cá nhân",
           "Số cổ phiếu người có liên quan"]
    w = [30, 110, 130, 80, 70, 80]
    d = SimpleDocTemplate(str(path), pagesize=A4)
    s = [P("BÁO CÁO THƯỜNG NIÊN 2023", H), P("Công ty Cổ phần Ví Dụ ABC (SYNTHETIC TEST DOCUMENT) - Mã chứng khoán: ABC"),
         P("Báo cáo thường niên năm 2023."), PageBreak(), P("Hội đồng quản trị", H),
         grid([hdr,
               ["1", "Nguyễn Văn An", "Chủ tịch HĐQT", "1.000.000", "2,00%", "300.000"],
               ["2", "Trần Thị Bình", "Thành viên HĐQT độc lập", "-", "-", "0"],
               ["3", "Lê Văn Cường", "Thành viên HĐQT", "500.000", "1,00%", "0"]], w),
         Spacer(1, 10),
         P("Tổng tỷ lệ sở hữu của HĐQT là 5,00% vốn điều lệ."), PageBreak(), P("Ban Kiểm soát", H),
         grid([hdr,
               ["1", "Nguyễn Thị Hoa", "Trưởng Ban Kiểm soát", "0", "0,00%", "5.000"],
               ["2", "Lý Văn Khoa", "Thành viên Ban Kiểm soát", "-", "-", "0"]], w)]
    d.build(s)


def no_bks(path: Path):
    d = SimpleDocTemplate(str(path), pagesize=A4)
    d.build([P("BÁO CÁO THƯỜNG NIÊN 2022", H), P("Công ty Cổ phần Ví Dụ ABC (SYNTHETIC) Mã chứng khoán: ABC năm 2022"),
             PageBreak(), P("Công ty áp dụng mô hình không có Ban Kiểm soát; Ủy ban kiểm toán trực thuộc Hội đồng quản trị."),
             P("Hội đồng quản trị tại ngày 31/12/2022"),
             grid([HDR, ["1", "Nguyễn Văn An", "Chủ tịch HĐQT", "1.000", "0,001%", "", ""],
                   ["2", "Trần Thị Bình", "Thành viên HĐQT độc lập", "0", "0,00%", "", ""]], W)])


def wrong_year(path: Path):
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [P("BÁO CÁO THƯỜNG NIÊN 2019", H), P("Công ty Cổ phần Ví Dụ ABC. Mã chứng khoán: ABC. Báo cáo năm 2019." * 3)])


def wrong_company(path: Path):
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [P("BÁO CÁO THƯỜNG NIÊN 2024", H), P("Công ty Cổ phần Khác. Mã chứng khoán: XYZ. Báo cáo năm 2024." * 3)])


def scanned(path: Path):
    """Image-only PDF: no text layer."""
    import fitz
    src = fitz.open()
    pg = src.new_page()
    pg.insert_text((72, 72), "BAO CAO THUONG NIEN 2021 ABC", fontsize=20)
    pix = pg.get_pixmap(dpi=60)
    doc = fitz.open()
    p = doc.new_page(width=pix.width, height=pix.height)
    p.insert_image(p.rect, stream=pix.tobytes("jpeg"))
    doc.save(str(path), deflate=True, garbage=4)


def text_layout(path: Path):
    """Governance data as plain text lines (no ruled table)."""
    d = SimpleDocTemplate(str(path), pagesize=A4)
    mono = ParagraphStyle("m", fontName="DJ", fontSize=9, leading=12)
    d.build([P("BÁO CÁO THƯỜNG NIÊN 2020 - ABC (SYNTHETIC) Mã chứng khoán: ABC năm 2020", H), PageBreak(),
             P("Hội đồng quản trị tại ngày 31/12/2020", H),
             P("Nguyễn Văn An&nbsp;&nbsp;&nbsp;Chủ tịch HĐQT&nbsp;&nbsp;&nbsp;1.000.000&nbsp;&nbsp;&nbsp;2,00%", mono),
             P("Trần Thị Bình&nbsp;&nbsp;&nbsp;Thành viên HĐQT độc lập&nbsp;&nbsp;&nbsp;0&nbsp;&nbsp;&nbsp;0,00%", mono)])


ALL = {"ABC_2024.pdf": clean_2024, "ABC_2023.pdf": messy_2023, "ABC_2022.pdf": no_bks, "WRONGYEAR_2024.pdf": wrong_year,
       "WRONGCO_2024.pdf": wrong_company, "ABC_2021.pdf": scanned, "ABC_2020.pdf": text_layout}


def make_clean_for(ticker: str, path: Path):
    clean_2024(path, ticker)


def make_all(outdir: Path) -> dict[str, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    res = {}
    for name, fn in ALL.items():
        fn(outdir / name)
        res[name] = outdir / name
    return res


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "pdfs"
    for k, v in make_all(out).items():
        print(k, v.stat().st_size)
