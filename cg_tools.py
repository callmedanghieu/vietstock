#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cg_tools.py — công cụ phụ cho workflow chạy song song (chia shard + gộp kết quả).

  plan   Chia danh sách PDF cần xử lý thành N phần (shard) và in ra phần của shard k.
         Nguồn: các file đính kèm Release (--assets assets.jsonl) hoặc thư mục (--dir bctn).
         Bỏ qua file đã có trong ket_qua.xlsx (dòng không lỗi).
         Mỗi dòng in ra: <id asset hoặc '-'> <TAB> <tên file>

  merge  Gộp các file kết quả của từng shard vào ket_qua.xlsx (sheet CG_data và Thanh_vien).
"""
import argparse, json, sys
from pathlib import Path

from cg_extract import ERR_PREFIX, HDR, MHDR, apply_coverage, highlight_na, print_coverage


def done_files(xlsx: Path):
    if not xlsx.exists():
        return set()
    from openpyxl import load_workbook
    ws = load_workbook(xlsx)["CG_data"]
    out = set()
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r and len(r) >= 14 and r[13] and not str(r[11] or "").startswith(ERR_PREFIX):
            out.add(r[13])
    return out


def mode_plan(a):
    done = done_files(Path(a.done))
    items = []
    if a.assets:
        for ln in Path(a.assets).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                d = json.loads(ln)
                items.append((str(d["id"]), d["name"]))
    else:
        d = Path(a.dir)
        if d.is_dir():
            items = [("-", p.name) for p in d.iterdir() if p.is_file()]
    items = sorted({n: (i, n) for i, n in items if n.lower().endswith(".pdf") and n not in done}.values(),
                   key=lambda x: x[1])
    mine = [it for k, it in enumerate(items) if k % a.n == a.shard]
    print(f"shard {a.shard}/{a.n}: {len(mine)} / {len(items)} file cần xử lý", file=sys.stderr)
    for i, n in mine:
        print(f"{i}\t{n}")


def mode_check(a):
    """Quét thử thư mục/Release trước khi chạy: công ty nào thiếu BCTN năm nào trong 2019-2025."""
    if a.assets:
        names = [json.loads(l)["name"] for l in Path(a.assets).read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        names = [p.name for p in Path(a.dir).iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    print_coverage(names)


def fmt_row(ws, kind):
    from openpyxl.styles import PatternFill
    row = ws.max_row
    if kind == "data":
        for c in ws[row]:
            if c.column in (5, 8, 9) and isinstance(c.value, float):
                c.number_format = "0.00%"
        highlight_na([c for c in ws[row] if c.column in (5, 8, 9)])
    else:
        if isinstance(ws.cell(row, 7).value, float):
            ws.cell(row, 7).number_format = "0.00%"
        if ws.cell(row, 11).value == "KHÔNG":
            for c in ws[row]:
                c.fill = PatternFill("solid", fgColor="FFF2CC")


def mode_merge(a):
    from openpyxl import Workbook, load_workbook
    tgt = Path(a.target)
    if tgt.exists():
        wb = load_workbook(tgt)
    else:
        wb = Workbook(); wb.active.title = "CG_data"; wb.active.append(HDR)
    ws = wb["CG_data"]
    wm = wb["Thanh_vien"] if "Thanh_vien" in wb.sheetnames else wb.create_sheet("Thanh_vien")
    if wm["A1"].value != MHDR[0]:
        for j, h in enumerate(MHDR, 1):          # ghi thẳng vào dòng 1 (append sẽ để trống dòng 1)
            wm.cell(1, j, h)

    data_rows, mem_rows = [], []
    for f in a.inputs:
        p = Path(f)
        if not p.exists():
            continue
        w = load_workbook(p)
        data_rows += [r for r in w["CG_data"].iter_rows(min_row=2, values_only=True) if r and r[13]]
        if "Thanh_vien" in w.sheetnames:
            mem_rows += [r for r in w["Thanh_vien"].iter_rows(min_row=2, values_only=True) if r and r[12]]
    files = {r[13] for r in data_rows}

    # xoá bản cũ của các file sẽ được ghi đè (vd. dòng lỗi lần trước)
    for r in range(ws.max_row, 1, -1):
        if ws.cell(r, 14).value in files:
            ws.delete_rows(r)
    for r in range(wm.max_row, 1, -1):
        if wm.cell(r, 13).value in files:
            wm.delete_rows(r)

    for r in sorted(data_rows, key=lambda x: (str(x[0]), x[1] or 0, str(x[13]))):
        ws.append(list(r)); fmt_row(ws, "data")
    for r in mem_rows:
        wm.append(list(r)); fmt_row(wm, "mem")
    apply_coverage(wb)
    wb.save(tgt)
    print(f"Gộp {len(data_rows)} dòng dữ liệu, {len(mem_rows)} dòng thành viên từ {len(a.inputs)} file → {tgt}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("plan")
    p1.add_argument("--assets"); p1.add_argument("--dir")
    p1.add_argument("--done", default="ket_qua.xlsx")
    p1.add_argument("--shard", type=int, required=True); p1.add_argument("--n", type=int, required=True)
    p2 = sub.add_parser("merge")
    p2.add_argument("--target", default="ket_qua.xlsx"); p2.add_argument("--inputs", nargs="*", default=[])
    p3 = sub.add_parser("check")
    p3.add_argument("--assets"); p3.add_argument("--dir", default="bctn")
    a = ap.parse_args()
    {"plan": mode_plan, "merge": mode_merge, "check": mode_check}[a.cmd](a)
