"""Starts the real web app with a sample document and a stand-in model, so the screen can be driven by tests/ui_check.py.

    python tests/ui_server.py [port]        (AUTH is off; nothing here touches OpenRouter or Bodhan)
"""
import copy
import os
import re
import sys
import tempfile
from pathlib import Path

from ocr import api, config, ops

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
tmp = Path(tempfile.mkdtemp(prefix="ocr_ui_"))
os.environ.update(OCR_DATA=str(tmp), AUTH_DISABLED="1", OPENROUTER_API_KEY="x", BODHAN_OCR_API_KEY="x")

import cv2
import numpy as np
import pymupdf
import requests
import uvicorn

from ocr import store

W, H = 595, 842
b = lambda r: [round(r[0] / W, 4), round(r[1] / H, 4), round(r[2] / W, 4), round(r[3] / H, 4)]


def make_pdf():
    d = pymupdf.open()
    p = d.new_page(width=W, height=H)
    p.insert_textbox((60, 50, 535, 85), "Model Question Paper", fontsize=18, align=1, fontname="hebo")
    p.insert_textbox((60, 100, 535, 150), "Instructions: answer all questions. Marks are shown at the right of each question.", fontsize=11)
    p.insert_textbox((60, 170, 535, 250), "1) State Ohm's law.  [2]\n2) Define resistance and state its SI unit.  [2]\n3) Name the instrument used to measure current.  [1]", fontsize=11)
    for i in range(4):
        p.draw_line((60, 270 + i * 30), (535, 270 + i * 30))
    for x in (60, 220, 380, 535):
        p.draw_line((x, 270), (x, 360))
    for r, row in enumerate([["Quantity", "Symbol", "Unit"], ["Current", "I", "Ampere"], ["Voltage", "V", "Volt"]]):
        for c, t in enumerate(row):
            p.insert_text((66 + c * 160, 290 + r * 30), t, fontsize=11)
    p.draw_rect((60, 380, 280, 470)); p.draw_circle((170, 425), 25)
    p.insert_textbox((60, 760, 535, 800), "The current through a conductor is directly proportional to the", fontsize=11)
    p = d.new_page(width=W, height=H)
    p.insert_textbox((60, 50, 535, 75), "potential difference across its ends.", fontsize=11)
    p.insert_textbox((60, 100, 535, 140), "Next paragraph begins here and runs on for a little while so that it has some length.", fontsize=11)
    p.insert_text((250, 812), "Scanned by CamScanner", fontsize=8)
    p = d.new_page(width=W, height=H)
    p.insert_textbox((60, 50, 535, 120), "Third page with one block that has a box and one that the pipeline could not place.", fontsize=11)
    return d


def seed():
    pdf = make_pdf()
    jid = store.new_job("local", "local", "sample_paper.pdf", pdf.tobytes(), 3)
    d = store.job_dir(jid)
    crops = d / "compare_out" / "infinite_ocr" / "crops"
    crops.mkdir(parents=True)
    img = np.full((90, 220, 3), 255, np.uint8); cv2.rectangle(img, (5, 5), (214, 84), (60, 120, 60), 2); cv2.circle(img, (110, 45), 25, (60, 120, 60), 2)
    cv2.imwrite(str(crops / "pic.png"), img)
    blk = lambda i, pg, r, t, mk, pic=False, path=None: {"block_id": i, "page": pg, "bbox_xyxy": None, "bbox_norm": b(r) if r else None,
                                                         "bodhan_type": t, "is_picture": pic, "image_path": path, "formatted_text": mk}
    blocks = [
        blk("p1_b1", 1, (60, 50, 535, 85), "title", '<P align="center" heading="1" size="18" bold="true">Model Question Paper</P>'),
        blk("p1_b2", 1, (60, 100, 535, 150), "text", "<P>Instructions: answer all questions. Marks are shown at the right of each question.</P>"),
        blk("p1_b3", 1, (60, 170, 535, 250), "text", "<P>1) State Ohm's law. [2]</P>\n<P>2) Define resistance and state its SI unit. [2]</P>\n<P>3) Name the instrument used to measure current. [1]</P>"),
        blk("p1_b4", 1, (60, 270, 535, 360), "table", '<TABLE border="1"><ROW header="true"><CELL>Quantity</CELL><CELL>Symbol</CELL><CELL>Unit</CELL></ROW><ROW><CELL>Current</CELL><CELL>I</CELL><CELL>Ampere</CELL></ROW><ROW><CELL>Voltage</CELL><CELL>V</CELL><CELL>Volt</CELL></ROW></TABLE>'),
        blk("p1_b5", 1, (60, 380, 280, 470), "picture", "", True, "compare_out/infinite_ocr/crops/pic.png"),
        blk("p1_b6", 1, (60, 760, 535, 800), "text", "<P>The current through a conductor is directly proportional to the</P>"),
        blk("p2_b1", 2, (60, 50, 535, 75), "text", "<P>potential difference across its ends.</P>"),
        blk("p2_b2", 2, (60, 100, 535, 140), "text", "<P>Next paragraph begins here and runs on for a little while so that it has some length.</P>"),
        blk("p2_b3", 2, (240, 800, 420, 820), "page_footer", ""),
        blk("p3_b1", 3, (60, 50, 535, 120), "text", "<P align=\"center\">మార్కులు : $5 \\times 5 = 25$</P>"),
        blk("p3_raw", 3, None, "text", "<P>Text the pipeline could not place on the page.</P>"),
    ]
    store.write_json(d / "blocks_pre.json", {"route": "single_gemini", "route_info": {"kind": "digital · non-English"}, "models": {}, "blocks": blocks})
    return jid, d


class Resp:
    status_code, headers, ok = 200, {}, True
    def __init__(self, data): self._d = data
    def json(self): return self._d
    def raise_for_status(self): pass


def fake_post(url, *a, **kw):
    """Stands in for OpenRouter and Bodhan. It formats sensibly, so the screen has realistic proposals to show."""
    body = kw.get("json") or {}
    if "bodhan" in url:
        return Resp({"blocks": [{"order": 1, "type": "Text", "text": "Scanned by CamScanner stamp text read again", "bbox_xyxy": [0, 0, 5, 5]}]})
    text = next(p["text"] for p in body["messages"][0]["content"] if p.get("type") == "text")
    if "THE PERSON'S REQUEST" in text:                       # the "Ask anything" prompt: answer in its <REPLY>/<ACTION> form
        note = re.search(r"\u00ab(.*?)\u00bb", text, re.S).group(1).lower()
        sel = re.findall(r'<BLOCK id="([^"]+)" page="\d+" type="[^"]*" bbox="[^"]*">\n(.*?)\n</BLOCK>', text.split("SELECTED BLOCKS")[1].split("PAGE OUTLINE")[0], re.S)
        ids = [i for i, _ in sel]
        if "delete" in note:
            acts = f'<ACTION type="delete" ids="{",".join(ids)}"/>'
        elif "table" in note:
            acts = f'<ACTION type="insert" after="{ids[-1]}"><TABLE border="1"><ROW header="true"><CELL>A</CELL><CELL>B</CELL></ROW><ROW><CELL>1</CELL><CELL>2</CELL></ROW></TABLE></ACTION>'
        else:
            acts = ""
            for bid, tags in sel:
                raw = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tags)).strip()
                items = [x.strip() for x in re.split(r"(?=\b\d\)\s)", raw) if x.strip()]
                if len(items) > 1 and all(re.match(r"\d\)", x) for x in items):
                    mk = '<LIST type="number">' + "".join(f'<ITEM level="0">{re.sub(r"\s*(\[\d\])$", r"<RIGHT>\1</RIGHT>", x)}</ITEM>' for x in items) + "</LIST>"
                else:
                    mk = f"<P>{raw}</P>"
                acts += f'<ACTION type="edit" id="{bid}">{mk}</ACTION>'
        out = f"<REPLY>Here is what I propose.</REPLY>{acts}"
        return Resp({"choices": [{"message": {"content": out}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}, "model": body["model"]})
    out = ""
    for bid, btype, raw in re.findall(r'<BLOCK id="([^"]+)" type="([^"]*)"[^>]*>\n(.*?)\n</BLOCK>', text, re.S):
        raw = re.sub(r"\n?<CURRENT_TAGS>.*?</CURRENT_TAGS>", "", raw, flags=re.S).strip()
        items = [x.strip() for x in re.split(r"(?=\b\d\)\s)", raw) if x.strip()]
        if len(items) > 1 and all(re.match(r"\d\)", x) for x in items):
            body_md = '<LIST type="number">' + "".join(
                f'<ITEM level="0">{re.sub(r"\s*(\[\d\])$", r"<RIGHT>\1</RIGHT>", x)}</ITEM>' for x in items) + "</LIST>"
        else:
            body_md = f"<P>{raw}</P>"
        out += f'<BLOCK id="{bid}">\n{body_md}\n</BLOCK>\n'
    return Resp({"choices": [{"message": {"content": out}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}, "model": body["model"]})


def in_process(d, op, req=None):
    try:
        return ops.OPS[op](d, req or {})
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    requests.post = fake_post
    ops.call = in_process
    config.JOBS.mkdir(parents=True, exist_ok=True)
    jid, d = seed()
    ops.rebuild(d)
    store.set_status(jid, state="done", finished=1.0)
    print("job", jid, "data", tmp, flush=True)
    uvicorn.run(api.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
