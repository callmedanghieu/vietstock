from app.agents import document_intelligence as di
from app.services.pdf_parser import load_or_parse, page_text_independent, parse_pdf


def test_searchable_pdf_parsed_with_pages_tables_and_vietnamese(pdfs):
    d = parse_pdf(pdfs["ABC_2024.pdf"], "ABC_2024")
    assert not d.is_scanned and d.n_pages == 3
    p2 = d.pages[1]
    assert "HDQT" in p2.tags and p2.tables
    assert "Thành viên HĐQT độc lập" in " ".join(" ".join(r) for r in p2.tables[0])
    assert any("Nguyễn Văn An" in c for r in p2.tables[0] for c in r)
    assert "1.200.000" in " ".join(" ".join(r) for r in p2.tables[0])     # thousands separators preserved


def test_relevant_pages_ranked_and_cover_page_ignored(pdfs):
    d = parse_pdf(pdfs["ABC_2024.pdf"], "ABC_2024")
    rel = [p.page for p in di.relevant_pages(d)]
    assert rel == [2, 3] and 1 not in rel


def test_scanned_pdf_detected_and_flagged(pdfs):
    d = parse_pdf(pdfs["ABC_2021.pdf"], "ABC_2021")
    assert d.is_scanned and any("SCANNED_NO_OCR" in w for w in d.warnings)


def test_independent_page_read_for_verification(pdfs):
    assert "Nguyễn Văn An" in page_text_independent(pdfs["ABC_2024.pdf"], 2)
    assert page_text_independent(pdfs["ABC_2024.pdf"], 99) == ""


def test_parse_cache_keyed_by_checksum(pdfs, tmp_path):
    a = load_or_parse(pdfs["ABC_2024.pdf"], "ABC_2024", tmp_path)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    b = load_or_parse(pdfs["ABC_2024.pdf"], "ABC_2024", tmp_path)
    assert a.sha256 == b.sha256 and len(list(tmp_path.glob("*.json"))) == 1


def test_real_pdf_end_to_end_extraction_with_citations(pdfs, settings):
    from app.agents import governance_extraction as ge
    d = parse_pdf(pdfs["ABC_2024.pdf"], "ABC_2024")
    r = ge.extract(settings, "ABC", 2024, d)
    hd = [m for m in r.members if m.governance_body == "HDQT" and m.active_at_cutoff]
    assert len(hd) == 5 and sum(1 for m in hd if m.independence.value == "TRUE") == 2
    assert all(m.evidence and m.evidence.page == 2 and m.evidence.document_id == "ABC_2024" for m in hd)
    assert len([m for m in r.members if m.governance_body == "BKS"]) == 3


def test_text_layout_pdf_falls_back_to_lines(pdfs, settings):
    from app.agents import governance_extraction as ge
    r = ge.extract(settings, "ABC", 2020, parse_pdf(pdfs["ABC_2020.pdf"], "ABC_2020"))
    assert {m.person_name for m in r.members} == {"Nguyễn Văn An", "Trần Thị Bình"}
    assert all(any(n.startswith("REVIEW") for n in m.review_notes) for m in r.members)
