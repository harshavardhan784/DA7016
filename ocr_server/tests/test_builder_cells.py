"""The Word builder must keep everything inside a table cell: options, lists, text beside paragraphs, and a forgotten </P>.
(Before this was fixed the options inside cells were silently left out of the Word file.)"""
import os
import re

import pymupdf
import pytest
from docx import Document

from ocr import nb

# shapes copied from a real document: a cell with a paragraph and two option lists; a cell holding only options; a forgotten </P>
Q13 = ('<TABLE border="1"><ROW><CELL><P>प्रश्न 13 अभिकथन (A) संतुलित भोजन<BR/>कारण (R) जंक फूड</P>'
       '<OPTIONS layout="vertical"><OPTION>क) अभिकथन (A) सही है लेकिन कारण (R) गलत है।</OPTION><OPTION>ख) अभिकथन (A) गलत है</OPTION></OPTIONS>'
       '<OPTIONS layout="vertical"><OPTION>(a) (A) is right but (R) is wrong</OPTION><OPTION>b) (A) is wrong but (R) is right</OPTION></OPTIONS></CELL></ROW>'
       '<ROW><CELL><OPTIONS layout="vertical"><OPTION>घ) अभिकथन (A) और कारण (R) दोनों सही है</OPTION></OPTIONS></CELL></ROW></TABLE>')
OPEN = '<TABLE border="1"><ROW><CELL><P>प्रश्न 18 लीग टूर्नामेंट के लाभ लिखो।\n<P>प्रश्न 19 संक्रामक रोग क्या हैं?</P></CELL></ROW></TABLE>'


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    d = tmp_path_factory.mktemp("cells")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "x")
    pdf.save(d / "input.pdf")
    cwd, old = os.getcwd(), os.environ.get("OCR_PDF")
    os.chdir(d)
    os.environ["OCR_PDF"] = str(d / "input.pdf")
    ns = nb.load()

    def run(markup):
        doc = Document()
        ns["_render_formatted_text"](doc, markup)
        return doc
    yield run
    os.chdir(cwd)
    if old is None:
        os.environ.pop("OCR_PDF", None)
    else:
        os.environ["OCR_PDF"] = old


def cell_paragraphs(doc, t=0, r=0):
    return [p.text for p in doc.tables[t].rows[r].cells[0].paragraphs]


def test_options_inside_a_cell_are_kept(build):
    doc = build(Q13)
    first = cell_paragraphs(doc, 0, 0)
    assert first[0].startswith("प्रश्न 13") and "कारण (R) जंक फूड" in first[0]
    for text in ("क) अभिकथन (A) सही है लेकिन कारण (R) गलत है।", "ख) अभिकथन (A) गलत है", "(a) (A) is right but (R) is wrong", "b) (A) is wrong but (R) is right"):
        assert text in first, text                       # one paragraph per option, in order
    assert cell_paragraphs(doc, 0, 1) == ["घ) अभिकथन (A) और कारण (R) दोनों सही है"]    # a cell with only options: no empty line before it


def test_every_word_of_the_markup_reaches_the_word_file(build):
    for markup in (Q13, OPEN, '<TABLE><ROW><CELL><P>intro</P><LIST type="bullet"><ITEM>one</ITEM><ITEM>two</ITEM></LIST></CELL></ROW></TABLE>',
                   '<TABLE><ROW><CELL><P>paragraph</P>stray text beside it</CELL></ROW></TABLE>'):
        doc = build(markup)
        got = "".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        want = re.sub(r"<[^>]+>", "", markup)
        assert re.sub(r"\s", "", want) in re.sub(r"\s", "", got) or sorted(re.sub(r"\s", "", want)) == sorted(re.sub(r"\s", "", got)), markup


def test_a_forgotten_close_tag_makes_two_paragraphs(build):
    assert cell_paragraphs(build(OPEN)) == ["प्रश्न 18 लीग टूर्नामेंट के लाभ लिखो।", "प्रश्न 19 संक्रामक रोग क्या हैं?"]
    doc = build("<P>Q18 first\n<P>Q19 second</P>")
    assert [p.text for p in doc.paragraphs if p.text] == ["Q18 first", "Q19 second"]


def test_plain_cells_are_built_as_before(build):
    doc = build('<TABLE border="1"><ROW header="true"><CELL>A</CELL><CELL>B</CELL></ROW><ROW><CELL><P>x</P><P>y</P></CELL><CELL>2</CELL></ROW></TABLE>')
    assert [c.text for c in doc.tables[0].rows[0].cells] == ["A", "B"] and cell_paragraphs(doc, 0, 1) == ["x", "y"]


def test_a_table_inside_a_cell_is_written_inside_that_cell(build):
    doc = build('<TABLE border="1"><ROW><CELL><P>outer text</P><TABLE border="1"><ROW><CELL colspan="2"><P>SHORT ANSWER TYPE</P></CELL></ROW>'
                '<ROW><CELL>left</CELL><CELL>right</CELL></ROW></TABLE></CELL></ROW></TABLE>')
    cell = doc.tables[0].rows[0].cells[0]
    assert cell.paragraphs[0].text == "outer text" and len(cell.tables) == 1
    inner = cell.tables[0]
    assert [c.text for c in inner.rows[1].cells] == ["left", "right"] and inner.rows[0].cells[0].text == "SHORT ANSWER TYPE"
