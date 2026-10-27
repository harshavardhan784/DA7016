r"""Checks the real models once on your server (costs a few cents):

    python -m ocr.smoke path\to\small.pdf

Runs the whole notebook on the PDF, then asks for Fix formatting and Re-read on its first text block
and prints what came back. Nothing is applied to the document.
"""
import sys
from pathlib import Path

import pymupdf

from . import store
from . import config
from . import ops


def main(pdf):
    config.JOBS.mkdir(parents=True, exist_ok=True)
    data = Path(pdf).read_bytes()
    jid = store.new_job("smoke", "smoke", Path(pdf).name, data, len(pymupdf.open(stream=data, filetype="pdf")))
    d = config.JOBS / jid
    ops.run_pipeline(d)
    ops.call(d, "rebuild")
    ui = store.read_json(d / "ui.json")
    print(f"\nroute: {ui['route']}  blocks: {len(ui['blocks'])}  job folder: {d}")
    bid = next((i for i in ui["order"] if ui["blocks"][i]["markup"] and not ui["blocks"][i]["is_picture"]), None)
    if not bid:
        return print("no text block to review")
    for mode in ("format", "reread"):
        res = ops.call(d, "review", {"mode": mode, "blocks": [bid]})
        b = res["blocks"][0]
        print(f"\n[{mode}] {bid} model={res['model']} usage={res['usage']} plain_text_fallback={b['plain_text_fallback']}")
        print("  before:", b["before"][:200].replace("\n", " "))
        print("  after: ", b["after"][:200].replace("\n", " "))


if __name__ == "__main__":
    main(sys.argv[1])
