import copy
import re

import pymupdf
import pytest
import requests

from ocr import store
from ocr import config

def _block(bid, page, bn, typ, markup):
    return {"block_id": bid, "page": page, "bbox_xyxy": None, "bbox_norm": bn, "bodhan_type": typ,
            "is_picture": False, "image_path": None, "formatted_text": markup}


BLOCKS = [
    _block("p1_b1", 1, [.1, .05, .9, .10], "title", '<P align="center" bold="true" size="16">Model Question Paper</P>'),
    _block("p1_b2", 1, [.1, .15, .9, .30], "list", '<LIST type="number"><ITEM level="0">1) State Ohm\'s law.<RIGHT>[2]</RIGHT></ITEM></LIST>'),
    _block("p1_b3", 1, [.1, .35, .9, .55], "table", '<TABLE border="true"><ROW header="true"><CELL>Quantity</CELL><CELL>Symbol</CELL></ROW><ROW><CELL>Current</CELL><CELL>I</CELL></ROW></TABLE>'),
    _block("p1_b4", 1, [.1, .85, .9, .95], "text", "<P>The current through a conductor is directly proportional to the</P>"),
    _block("p2_b1", 2, [.1, .05, .9, .10], "text", "<P>potential difference across its ends.</P>"),
    _block("p2_b2", 2, [.1, .15, .9, .25], "text", "<P>Next paragraph.</P>"),
]


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("BODHAN_OCR_API_KEY", "test")
    monkeypatch.chdir(tmp_path)
    store.config.JOBS.mkdir()
    pdf = pymupdf.open()
    for t in ("page one", "page two"):
        pdf.new_page().insert_text((72, 72), t)
    jid = store.new_job("u1", "u1@example.com", "t.pdf", pdf.tobytes(), 2)
    d = store.job_dir(jid)
    store.write_json(d / "blocks_pre.json", {"route": "single_gemini", "route_info": {"why": "test"},
                                             "models": {}, "blocks": copy.deepcopy(BLOCKS)})
    return d


class FakeResp:
    status_code, headers, ok = 200, {}, True

    def __init__(self, data): self._d = data
    def json(self): return self._d
    def raise_for_status(self): pass


@pytest.fixture
def fake_models(monkeypatch):
    """Stands in for OpenRouter (echoes each block's text inside <P>) and Bodhan (reads 'alpha beta')."""
    seen = []

    def post(url, *a, **kw):
        body = kw.get("json") or {}
        seen.append((url, body))
        if "bodhan" in url:
            return FakeResp({"blocks": [{"order": 1, "type": "Text", "text": "alpha beta", "bbox_xyxy": [0, 0, 5, 5]}]})
        text = next(p["text"] for p in body["messages"][0]["content"] if p.get("type") == "text")
        out = "".join(f'<BLOCK id="{i}">\n<P>{re.sub(r"<CURRENT_TAGS>.*?</CURRENT_TAGS>", "", t, flags=re.S).strip()}</P>\n</BLOCK>\n'
                      for i, t in re.findall(r'<BLOCK id="([^"]+)" type=[^>]*>\n(.*?)\n</BLOCK>', text, re.S))
        return FakeResp({"choices": [{"message": {"content": out}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}, "model": body["model"]})
    monkeypatch.setattr(requests, "post", post)
    return seen
