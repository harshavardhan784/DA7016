"""The editor's markup converter must not change what the notebook's DOCX builder produces.

Run `npm test` in web_src first: it writes web_src/tests/roundtrip.out.json (original markup and its round trip).
"""
import json
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from pipeline import runtime as nb

OUT = Path(__file__).resolve().parent.parent / "web_src" / "tests" / "roundtrip.out.json"
CORPUS = Path(__file__).resolve().parent.parent / "web_src" / "tests" / "corpus.json"
pytestmark = pytest.mark.skipif(not OUT.exists(), reason="run `npm test` in web_src first")


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    d = tmp_path_factory.mktemp("fid")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "x")
    pdf.save(d / "input.pdf")
    import os
    cwd, old = os.getcwd(), os.environ.get("OCR_PDF")
    os.chdir(d)
    os.environ["OCR_PDF"] = str(d / "input.pdf")
    ns = nb.load()

    def run(markup):
        doc = Document()
        ns["_render_formatted_text"](doc, markup)
        return describe(doc)
    yield run
    os.chdir(cwd)
    if old is None:
        os.environ.pop("OCR_PDF", None)
    else:
        os.environ["OCR_PDF"] = old


def run_key(r):
    f = r.font
    return (r.bold, r.italic, r.underline, f.strike, f.superscript, f.subscript, f.size and f.size.pt, f.name,
            str(f.color.rgb) if f.color and f.color.type else None, f.highlight_color)


def para(p):
    pf = p.paragraph_format
    runs = []
    for r in p.runs:                                  # adjacent runs with identical formatting are one run
        k = run_key(r)
        if runs and runs[-1][0] == k:
            runs[-1][1] += r.text
        else:
            runs.append([k, r.text])
    eq = [("".join(t.text or "" for t in m.iter("{http://schemas.openxmlformats.org/officeDocument/2006/math}t"))) for m in
          p._p.iter("{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")]
    return {"equations": eq, "style": p.style.name, "align": p.alignment, "left": pf.left_indent, "first": pf.first_line_indent,
            "before": pf.space_before, "after": pf.space_after, "line": pf.line_spacing,
            "tabs": [t.position for t in pf.tab_stops], "runs": [tuple(x) for x in runs]}


def cell_desc(tc, doc):
    """Everything in a cell, in order: its paragraphs and any table nested in it."""
    out = []
    for el in tc.iterchildren():
        if el.tag.endswith("}p"):
            out.append(("p", para(Paragraph(el, doc))))
        elif el.tag.endswith("}tbl"):
            out.append(("t",) + table_desc(Table(el, doc), doc))
    return out


def table_desc(t, doc):
    rows = [[(tc.grid_span, cell_desc(tc, doc)) for tc in row._tr.tc_lst] for row in t.rows]
    return (t.style.name if t.style is not None else None, t.alignment, len(t.columns), rows)


def describe(doc):
    out = []
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}p"):
            out.append(("p", para(Paragraph(el, doc))))
        elif el.tag.endswith("}tbl"):
            out.append(("t",) + table_desc(Table(el, doc), doc))
    return out


def Paragraph_list(tc, doc):
    return [Paragraph(p, doc) for p in tc.p_lst]


def cases():
    if not OUT.exists():
        return []                                     # skipped until `npm test` has written the results
    refuse = {c["id"] for c in json.loads(CORPUS.read_text(encoding="utf-8")) if c.get("refuse")}
    return [(r, r["id"] in refuse) for r in json.loads(OUT.read_text(encoding="utf-8"))]


@pytest.mark.parametrize("case,refused", cases(), ids=lambda x: x["id"] if isinstance(x, dict) else str(x))
def test_round_trip_builds_the_same_document(render, case, refused):
    if refused:
        assert case["out"] is None and case["reason"]
        return
    assert case["out"] is not None, case["reason"]
    assert render(case["out"]) == render(case["markup"])
    assert render(case["markup"]), "corpus entry produced an empty document"
