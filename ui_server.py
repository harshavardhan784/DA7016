"""Runs the real generated script (every notebook cell the pipeline uses) against a fake model."""
import json
import re

import pymupdf
import pytest
import requests

from conftest import FakeResp
from ocr import config, nb, ops, store

REPLY = ('<BLOCK id="1" kind="text" bbox="0.10,0.10,0.90,0.20">\n<P align="center" bold="true">Title line</P>\n</BLOCK>\n'
         '<BLOCK id="2" kind="picture" bbox="100,400,600,700"></BLOCK>\n'
         '<BLOCK id="3" kind="text" bbox="0.10,0.80,0.90,0.90">\n<P>A sentence that does not end and</P>\n</BLOCK>\n')
REPLY2 = '<BLOCK id="1" kind="text" bbox="0.10,0.05,0.90,0.10">\n<P>continues here.</P>\n</BLOCK>\n'


@pytest.fixture
def chinese_job(tmp_path, monkeypatch):
    """A digital PDF whose text is not Latin, so the router picks the single-model route without a probe call."""
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setenv("OCR_DATA", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("BODHAN_OCR_API_KEY", "test")
    config.JOBS.mkdir()
    pdf = pymupdf.open()
    for _ in range(2):
        pdf.new_page().insert_text((72, 72), "\n".join(["这是一个用于测试的文档页面。" * 2] * 8), fontname="china-s", fontsize=9)
    jid = store.new_job("u1", "u1@example.com", "zh.pdf", pdf.tobytes(), 2)
    monkeypatch.chdir(config.JOBS / jid)
    monkeypatch.setenv("OCR_PDF", str(config.JOBS / jid / "input.pdf"))
    return config.JOBS / jid


def test_full_run_then_rebuild_matches(chinese_job, monkeypatch):
    calls = []

    def post(url, *a, **kw):
        calls.append(url)
        page = sum("openrouter" in u for u in calls)
        return FakeResp({"choices": [{"message": {"content": REPLY if page % 2 else REPLY2}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001}})
    monkeypatch.setattr(requests, "post", post)

    exec(compile(nb.run_script(), "pipeline_script", "exec"), {"__name__": "__main__"})   # the notebook, start to end

    pre = json.loads((chinese_job / "blocks_pre.json").read_text(encoding="utf-8"))
    assert pre["route"] == "single_gemini" and len(pre["blocks"]) == 4
    assert any(b["is_picture"] and b["image_path"] for b in pre["blocks"])
    assert all("compare_out" in b["image_path"] for b in pre["blocks"] if b["is_picture"])         # relative to the job folder
    made_by_notebook = sorted((chinese_job / "compare_out" / "infinite_ocr").glob("*.docx"))[-1]

    ops.rebuild(chinese_job)                                                   # same final cell, from the saved blocks
    from docx import Document

    def text(p):
        d = Document(p)
        return [x.text for x in d.paragraphs], len(d.inline_shapes)
    assert text(chinese_job / "output.docx") == text(made_by_notebook)
    ui = store.read_json(chinese_job / "ui.json")
    assert ui["blocks"][next(i for i, b in ui["blocks"].items() if b["has_image"])]["has_image"]
    assert any(b["absorbed"] for b in ui["blocks"].values())                   # the cut-off sentence joined across the page break
    assert "continues here." in " ".join(text(chinese_job / "output.docx")[0])


def test_child_process_protocol(chinese_job):
    """The real `python -m ocr.ops` path: request file in, result file out, errors come back as messages."""
    store.write_json(chinese_job / "blocks_pre.json", {"route": "single_gemini", "route_info": {}, "models": {}, "blocks": [
        {"block_id": "p1_b1", "page": 1, "bbox_xyxy": None, "bbox_norm": [.1, .1, .9, .2], "bodhan_type": "text",
         "is_picture": False, "image_path": None, "formatted_text": "<P>Hello.</P>"}]})
    assert ops.call(chinese_job, "rebuild") == {"rev": 0}
    assert (chinese_job / "output.docx").exists() and not list((chinese_job / "ops").iterdir())
    with pytest.raises(RuntimeError, match="unknown or empty selection"):
        ops.call(chinese_job, "review", {"mode": "format", "blocks": ["zzz"]})
