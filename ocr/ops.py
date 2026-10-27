"""Operations on one job. Each runs in its own short-lived process because the notebook keeps
its state in global variables (one PDF, one set of counters) and cannot share a process with another job.

    python -m ocr.ops <rebuild|review|newbox|assist> <job_dir> <request.json> <result.json>

Everything that is pipeline logic comes from the notebook (see nb.py); this file only wires it up.
"""
import contextlib
import copy
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import store
from . import config
from . import nb
from . import prompts

NOTE_MARKER = "Now process the chunk below."
FORMAT_TAIL = "\n\n\nNow process the chunk below.\n\nINPUT:"
MAX_CURRENT = 6000          # longest set of current tags sent for one block


# ── spawning (called from the web server) ─────────────────────────────────
def _env():
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(config.APP_DIR)}


def run_pipeline(job_dir):
    """The whole notebook on input.pdf. Blocks until done; raises RuntimeError with a readable message."""
    d = Path(job_dir).resolve()
    script = d / "pipeline_script.py"
    script.write_text(nb.run_script(), encoding="utf-8")
    try:
        with open(d / "run.log", "w", encoding="utf-8") as log:
            proc = subprocess.run([sys.executable, "-u", str(script)], cwd=d,
                                  env={**_env(), "OCR_PDF": str(d / "input.pdf")},
                                  stdout=log, stderr=subprocess.STDOUT, timeout=config.JOB_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"pipeline took longer than {config.JOB_TIMEOUT}s")
    if proc.returncode != 0:
        raise RuntimeError("pipeline failed - see run.log")
    if not (d / "blocks_pre.json").exists():
        raise RuntimeError("pipeline finished without saving its blocks")


def call(job_dir, op, req=None):
    """Run one operation in a child process and return its result."""
    d = Path(job_dir).resolve()
    (d / "ops").mkdir(exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    rq, out = d / "ops" / f"{tag}.req.json", d / "ops" / f"{tag}.out.json"
    rq.write_text(json.dumps(req or {}), encoding="utf-8")
    try:
        with open(d / "ops.log", "ab") as log:
            subprocess.run([sys.executable, "-u", "-m", "ocr.ops", op, str(d), str(rq), str(out)], cwd=d,
                           env=_env(), stdout=log, stderr=subprocess.STDOUT, timeout=config.OP_TIMEOUT)
        if not out.exists():
            raise RuntimeError("operation crashed - see ops.log")
        res = json.loads(out.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{op} took longer than {config.OP_TIMEOUT}s")
    finally:
        rq.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    if not res["ok"]:
        raise RuntimeError(res["error"])
    return res["result"]


# ── inside the child process ──────────────────────────────────────────────
def _prepare(job_dir):
    d = Path(job_dir).resolve()
    os.chdir(d)
    os.environ["OCR_PDF"] = str(d / "input.pdf")
    return d


def rebuild(job_dir, req=None):
    """Replay the edits, run the notebook's final cell (fix_blocks + DOCX), and write what the screen needs."""
    d = _prepare(job_dir)
    job_id = d.name
    pre = store.read_json(d / "blocks_pre.json")
    hist = store.history(job_id)
    blocks = store.current_blocks(job_id)

    ns = nb.load()
    ns["ROUTE"], ns["ROUTE_INFO"] = pre["route"], pre["route_info"]
    ns["ordered_blocks"] = copy.deepcopy(blocks)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(nb.source("final"), "<notebook:final>", "exec"), ns)
    print(buf.getvalue())
    log = [l.split("fix:", 1)[1].strip() for l in buf.getvalue().splitlines() if l.startswith("  fix:")]

    # absorbed blocks: the two merges that name both blocks in the notebook's own log lines
    absorbed_by = {}
    for line in log:
        m = (re.match(r"p\d+->p\d+: joined cut-off paragraph (\S+) \+ (\S+)$", line)
             or re.match(r"p\d+->p\d+: merged table (\S+) \+ (\S+) \(", line))
        if m:
            absorbed_by[m.group(2)] = m.group(1)

    post = {b["block_id"]: b for b in ns["ordered_blocks"]}
    ui_blocks = {}
    for b in blocks:
        i = b["block_id"]
        meta = ns["block_meta"].get(i, {})
        had = bool((b.get("formatted_text") or "").strip())
        emptied = had and not (post[i].get("formatted_text") or "").strip()
        ui_blocks[i] = {
            "id": i, "page": b["page"], "type": b.get("bodhan_type"), "is_picture": bool(b.get("is_picture")),
            "has_image": bool(b.get("is_picture") and b.get("image_path")),
            "bbox_norm": ns["_bn"](b), "markup": b.get("formatted_text") or "",
            "rendered": bool(meta.get("rendered")), "reason": meta.get("reason"),
            "absorbed": emptied, "absorbed_by": absorbed_by.get(i) if emptied else None,
        }
    ui = {
        "route": pre["route"], "route_info": pre["route_info"],
        "rev": hist["rev"], "cursor": hist["cursor"], "length": len(hist["events"]),
        "pages": [{"n": p + 1, "aspect": ns["_PAGE_WH"][p][0] / ns["_PAGE_WH"][p][1]} for p in ns["page_indices"]],
        "order": [b["block_id"] for b in blocks], "blocks": ui_blocks, "log": log,
    }
    old = store.read_json(d / "ui.json", {})
    ui["dir"] = {**old.get("dir", old.get("blocks", {})), **ui_blocks}        # every block ever seen, so undo can bring one back instantly
    store.write_json(d / "ui.json", ui)

    shutil.copy(ns["final_docx"], d / "output.docx")
    pages = d / "pages"
    pages.mkdir(exist_ok=True)
    for p in ns["page_indices"]:
        f = pages / f"{p + 1}.jpg"
        if not f.exists():
            f.write_bytes(ns["doc"][p].get_pixmap(dpi=config.DISPLAY_DPI, alpha=False).tobytes("jpeg", jpg_quality=80))
    store.set_status(job_id, built_rev=hist["rev"])
    return {"rev": hist["rev"]}


def _crop_text(ns, block, bn):
    """Read one region again with the notebook's own OCR: Bodhan first, then its vision-model fallback.
    A small crop can come back empty from Bodhan (nothing for its layout step to find); the fallback then reads it."""
    page = ns["preprocess_page"](block["page"] - 1)
    if bn:
        H, W = page.shape[:2]
        pad = config.CROP_PAD
        x0, y0, x1, y1 = max(0, bn[0] - pad), max(0, bn[1] - pad), min(1, bn[2] + pad), min(1, bn[3] + pad)
        page = page[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)]
    join = lambda resp: "\n".join((r.get("text") or "").strip() for r in resp["blocks"] if (r.get("text") or "").strip())
    text = join(ns["ocr_with_fallback"](page))
    if not text and ns.get("OPENROUTER_KEY"):
        text = join(ns["_call_openrouter_ocr"](page, ns["OPENROUTER_KEY"], ns["OCR_FALLBACK_MODEL"]))
    if not text:
        raise ValueError(f"nothing could be read from {block['block_id']}")
    return text


_BREAKS = re.compile(r"</?(?:P|ITEM|OPTION|OPTIONS|CELL|ROW|TABLE|LIST|QUESTION|RIGHT)\b[^>]*>|<(?:BR|BLANK)\b[^>]*>", re.I)


def _words(markup):
    """The block's words as a person reads them: cells, rows, list items and marks separated by a space, inline styling not."""
    text = re.sub(r"<[^>]+>", "", _BREAKS.sub(" ", markup or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _item(ns, b, mode):
    """A block in the shape the notebook's formatter expects. Positions are kept as ratios only."""
    bn = ns["_bn"](b)
    current = b.get("formatted_text") or ""
    if mode == "format":
        if b.get("is_picture") or not current.strip():
            raise ValueError("nothing to format here (a picture or an empty block) - use Re-read")
        text = _words(current)
    else:
        text = _crop_text(ns, b, bn)
    return {"block_id": b["block_id"], "page": b["page"], "text": text, "is_picture": False, "current": current,
            "bodhan_type": "Text" if b.get("is_picture") else (b.get("bodhan_type") or "Text"),
            "bbox_xyxy": [round(v * 1000, 1) for v in bn] if bn else None,
            "page_wh": (1000, 1000) if bn else None, "image_path": b.get("image_path")}


def _show_current_tags(ns, items):
    """Adds each block's present tags to what the model sees, so it starts from them instead of guessing the layout again.
    Only the message changes; the text check still compares the answer with the block's words."""
    current = {i["block_id"]: i["current"] for i in items if 0 < len(i["current"]) <= MAX_CURRENT}
    original = ns["_serialize_blocks_for_combined"]

    def serialize(blocks):
        out = original(blocks)
        for b in blocks:
            tags = current.get(b["block_id"])
            if tags:
                out = re.sub(rf'(<BLOCK id="{re.escape(b["block_id"])}"[^>]*>\n.*?)(\n</BLOCK>)',
                             lambda m: f"{m.group(1)}\n<CURRENT_TAGS>{tags}</CURRENT_TAGS>{m.group(2)}", out, count=1, flags=re.S)
        return out
    ns["_serialize_blocks_for_combined"] = serialize


def _need_keys(ns):
    if not ns.get("OPENROUTER_KEY"):
        raise ValueError("The server has no OPENROUTER_API_KEY, so it cannot ask the model. "
                         "Set it (setx OPENROUTER_API_KEY \"...\" /M), close the app window and start the app again.")


def review_model(ns):
    """The model for every review call: the Qwen model the notebook already names (stage 'classify')."""
    m = ns["M"].get("classify")
    if not m:
        raise nb.NotebookError("notebook changed: no 'classify' model to use for review")
    return m


def _configure(ns, note, model, mode):
    model = model or review_model(ns)
    if model not in {m for opts in ns["MODEL_OPTIONS"].values() for m in opts}:
        raise ValueError(f"model not allowed: {model}")
    ns["COMBINED_MODELS"] = [model]
    ns["OCR_FALLBACK_MODEL"] = model                      # Re-read's backup OCR: same model, no Gemini
    note = (note or "").strip()[:1000]
    if mode == "format":
        ns["COMBINED_PROMPT"] = (prompts.load("fix_formatting.txt").replace("{tags}", ns["TAG_GRAMMAR"])
                                 .replace("{note}", note or prompts.load("default_instruction.txt").strip()) + FORMAT_TAIL)
    elif note:                                            # Re-read keeps the notebook's own prompt; the note is only a hint
        p = ns["COMBINED_PROMPT"]
        if p.count(NOTE_MARKER) != 1:
            raise nb.NotebookError("notebook changed: the formatting prompt no longer has its closing instruction")
        ns["COMBINED_PROMPT"] = p.replace(NOTE_MARKER, prompts.load("reread_hint.txt").replace("{note}", note) + "\n\n" + NOTE_MARKER)


def _format_all(ns, items):
    """The notebook's _format_chunk on every chunk, in parallel as the notebook does.
    Returns {id: (markup, fell_back_to_plain_text)}, the model used, and {id: reason} for blocks whose chunk failed."""
    step = ns["CHUNK_BLOCKS"]
    chunks = [items[k:k + step] for k in range(0, len(items), step)]
    out, failed, used = {}, {}, None

    def one(chunk):
        try:
            return chunk, ns["_format_chunk"](chunk), None
        except Exception as e:                      # one chunk failing (network, quota) must not lose the others
            return chunk, None, f"{type(e).__name__}: {str(e)[:200]}"
    with ThreadPoolExecutor(max(1, min(len(chunks), ns["CHUNK_WORKERS"]))) as pool:
        for chunk, res, err in pool.map(one, chunks):
            if err:
                failed.update({c["block_id"]: err for c in chunk})
                continue
            ordered, used, raw_ids = res
            for bid, markup in ordered:
                out[bid] = (re.sub(r"</?CURRENT_TAGS>", "", markup), bid in raw_ids)
    return out, used, failed


def _usage(ns):
    calls = [c for c in ns["CALLS"] if c["engine"] != "_wallclock_"]
    return {"calls": len(calls), "tok_in": sum(c["tok_in"] for c in calls),
            "tok_out": sum(c["tok_out"] for c in calls), "cost": round(sum((c["cost"] or 0) for c in calls), 6)}


def review(job_dir, req):
    """Fix formatting (text locked) or Re-read from image, for the blocks the person selected."""
    d = _prepare(job_dir)
    mode = req["mode"]
    if mode not in ("format", "reread"):
        raise ValueError("mode must be 'format' or 'reread'")
    blocks = store.current_blocks(d.name)
    by, order = {b["block_id"]: b for b in blocks}, [b["block_id"] for b in blocks]
    unknown = [i for i in req["blocks"] if i not in by]
    if unknown or not req["blocks"]:
        raise ValueError(f"unknown or empty selection: {unknown}")
    ids = sorted(set(req["blocks"]), key=order.index)

    ns = nb.load(lift=("indic_ocr", "indic_fmt"))
    _need_keys(ns)
    _configure(ns, req.get("note"), req.get("model"), mode)
    items, skipped = [], []
    for i in ids:                                     # a block that cannot be handled is reported; the others still go ahead
        try:
            items.append(_item(ns, by[i], mode))
        except (ValueError, RuntimeError) as e:
            skipped.append({"id": i, "reason": str(e)})
    if not items:
        raise ValueError("; ".join(f"{s_['id']}: {s_['reason']}" for s_ in skipped))
    if mode == "format":
        _show_current_tags(ns, items)
    results, used, failed = _format_all(ns, items)
    skipped += [{"id": i, "reason": f"the model call failed ({why})"} for i, why in failed.items()]
    out = []
    for i in ids:
        if i not in results:
            continue
        markup, fell_back = results[i]
        fields = {"formatted_text": markup}
        if mode == "reread":
            fields.update(is_picture=False, bodhan_type=by[i].get("bodhan_type") if not by[i].get("is_picture") else "Text")
        out.append({"id": i, "before": by[i].get("formatted_text") or "", "after": markup, "fields": fields,
                    "plain_text_fallback": fell_back})
    if not out:
        raise ValueError("; ".join(f"{s_['id']}: {s_['reason']}" for s_ in skipped))
    return {"mode": mode, "note": req.get("note") or "", "model": used, "blocks": out, "skipped": skipped, "usage": _usage(ns)}


def _insert_index(ns, blocks, page, y0):
    """Where a new block goes in reading order: before the first block on its page that starts lower."""
    same = [(i, b) for i, b in enumerate(blocks) if b["page"] == page]
    if not same:
        return next((i for i, b in enumerate(blocks) if b["page"] > page), len(blocks))
    for i, b in same:
        bn = ns["_bn"](b)
        if bn and bn[1] > y0:
            return i
    return same[-1][0] + 1


def newbox(job_dir, req):
    """Text the OCR missed: read a box drawn on the page and propose it as a new block."""
    d = _prepare(job_dir)
    page, (x0, y0, x1, y1) = int(req["page"]), req["bbox_norm"]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("box must lie inside the page")
    blocks = store.current_blocks(d.name)
    ns = nb.load(lift=("indic_ocr", "indic_fmt"))
    if not 1 <= page <= len(ns["doc"]):
        raise ValueError("no such page")
    n = 1 + max([int(m.group(1)) for b in blocks if (m := re.fullmatch(rf"p{page}_u(\d+)", b["block_id"]))] or [0])
    W, H = (ns["doc"][page - 1].rect.width * ns["DPI"] / 72, ns["doc"][page - 1].rect.height * ns["DPI"] / 72)
    blk = {"block_id": f"p{page}_u{n}", "page": page, "bbox_xyxy": [x0 * W, y0 * H, x1 * W, y1 * H],
           "bbox_norm": [x0, y0, x1, y1], "bodhan_type": "Text", "is_picture": False, "image_path": None,
           "formatted_text": ""}
    _need_keys(ns)
    _configure(ns, req.get("note"), req.get("model"), "reread")
    results, used, failed = _format_all(ns, [_item(ns, blk, "reread")])
    if failed:
        raise ValueError(f"the model call failed ({next(iter(failed.values()))})")
    markup, fell_back = results[blk["block_id"]]
    blk["formatted_text"] = markup
    return {"mode": "newbox", "note": req.get("note") or "", "model": used,
            "blocks": [{"id": blk["block_id"], "before": "", "after": markup,
                        "fields": {"formatted_text": markup}, "plain_text_fallback": fell_back}],
            "new_block": blk, "index": _insert_index(ns, blocks, page, y0), "skipped": [], "usage": _usage(ns)}


# ── the assistant: any request about the selected blocks ──────────────────
ASSIST_BLOCK_MAX = 12000      # longest tags sent for one selected block
ASSIST_TOTAL_MAX = 40000      # longest tags sent for the whole selection
ASSIST_PAGES_MAX = 3          # page images sent
ASSIST_CROPS_MAX = 8          # block crops sent
_PAIRED = ("P", "LIST", "ITEM", "OPTIONS", "OPTION", "TABLE", "ROW", "CELL", "STYLE", "QUESTION")
_ACTION_RE = re.compile(r"<ACTION\b([^>]*?)\s*/>|<ACTION\b([^>]*)>(.*?)</ACTION>", re.S | re.I)


def _lab(block_id):
    """'p4_b10' -> 'p4 · B10' (the way the screen names a block)."""
    return re.sub(r"^p(\d+)_(.*)$", lambda m: f"p{m.group(1)} \u00b7 {m.group(2).upper()}", block_id)


def _clean_markup(raw):
    """The model's tags for one block, checked. Raises ValueError with a reason a person can read."""
    m = re.sub(r"</?BLOCK\b[^>]*>|</?CURRENT_TAGS>", "", str(raw or ""))
    m = re.sub(r"^```[A-Za-z]*\s*|\s*```$", "", m.strip()).strip()
    if not m:
        raise ValueError("the model sent no tags")
    if not re.search(r"<[A-Za-z]", m):
        m = f"<P>{html.escape(m, quote=False)}</P>"
    for t in _PAIRED:
        opened = len(re.findall(rf"<{t}(?=[\s>])[^>]*(?<!/)>", m))
        closed = len(re.findall(rf"</{t}>", m))
        if opened != closed:
            raise ValueError(f"the tags are not balanced (<{t}>)")
    return m


_ATOM_KEYS = ("block_id", "page", "bbox_xyxy", "bbox_norm", "bodhan_type", "is_picture", "image_path", "formatted_text", "text", "page_wh")


def _atoms(b):
    """The original pieces behind a block: itself, or what earlier merges joined into it (kept flat, so a split always restores the originals)."""
    return copy.deepcopy(b["merged_from"]) if b.get("merged_from") else [{k: b[k] for k in _ATOM_KEYS if k in b}]


def _same_bag(a, b):
    """True when two tag strings hold the same words, in any order (a merge may put the right-hand piece last)."""
    f = lambda m: sorted(re.sub(r"\$", "", _words(m)).split())
    return f(a) == f(b)


def _history_text(d, limit=6):
    """What was asked and decided earlier in this document, from the saved proposals (so it follows undo/accept/reject)."""
    rows = [p for p in (store.read_json(f) for f in (d / "proposals").glob("*.json")) if p]
    out = []
    for p in sorted(rows, key=lambda p: p.get("created", 0))[-limit:]:
        note = (p.get("note") or "").strip() or ("drew a new box" if p.get("mode") == "newbox" else "")
        cards = "; ".join(f"{c.get('title') or _lab(c['id'])}: {c.get('resolved') or 'pending'}" for c in p.get("blocks", []))
        out.append(f"- asked \u00ab{note[:160]}\u00bb; answer: {(p.get('message') or '')[:200]}" + (f" [{cards[:300]}]" if cards else ""))
    return out


def _same_words(a, b):
    """True when two tag strings hold the same words in the same order (spacing and $ signs ignored)."""
    f = lambda m: re.sub(r"[\s$]+", "", _words(m))
    return f(a) == f(b)


def _parse_assist(raw):
    """(reply, [{'type', 'attrs', 'body'}]) from the model's answer. Raises ValueError when nothing can be read."""
    text = raw or ""
    r = re.search(r"<REPLY>(.*?)</REPLY>", text, re.S | re.I)
    reply = re.sub(r"\s+", " ", html.unescape(r.group(1))).strip() if r else ""
    actions = []
    for m in _ACTION_RE.finditer(text):
        attrs = dict((k.lower(), v) for k, v in re.findall(r'([A-Za-z_]+)\s*=\s*"([^"]*)"', m.group(1) if m.group(1) is not None else m.group(2)))
        actions.append({"type": (attrs.get("type") or "").lower(), "attrs": attrs, "body": m.group(3) or ""})
    if not reply and not actions:
        raise ValueError("The model's answer could not be read. Try again, or say it in a different way.")
    return reply, actions


def _union_bbox(blocks, ns):
    """One box around blocks that are all on one page, else None."""
    if len({b["page"] for b in blocks}) != 1:
        return None
    boxes = [ns["_bn"](b) for b in blocks]
    if any(bx is None for bx in boxes):
        return None
    return [min(bx[0] for bx in boxes), min(bx[1] for bx in boxes), max(bx[2] for bx in boxes), max(bx[3] for bx in boxes)]


def _assist_pages(by, ids):
    """Every page the selection touches, including the pages of parts that were merged in."""
    return sorted({by[i]["page"] for i in ids} | {a["page"] for i in ids for a in by[i].get("merged_from") or []})


def _assist_content(ns, prompt_text, by, ids, outline_ids, history=()):
    """The message for the model: the prompt, the blocks, a page outline, then the page images and block crops."""
    sel = set(ids)
    parts = ["\n\nSELECTED BLOCKS (the only ones you may edit, merge or delete), in reading order:"]
    for i in ids:
        b = by[i]
        bn = ns["_bn"](b)
        box = ",".join(f"{v:.3f}" for v in bn) if bn else "none"
        body = "(a picture, no text)" if b.get("is_picture") else (b.get("formatted_text") or "")
        made = ""
        if b.get("merged_from"):
            made = ' parts="' + "; ".join(f'{a["block_id"]} [page {a["page"]}, bbox {",".join(f"{v:.3f}" for v in (ns["_bn"](a) or []))}] {_words(a.get("formatted_text"))[:50]}'.replace('"', "'") for a in b["merged_from"]) + '"'
        parts.append(f'<BLOCK id="{i}" page="{b["page"]}" type="{b.get("bodhan_type") or "Text"}" bbox="{box}"{made}>\n{body}\n</BLOCK>')
    parts.append("\nPAGE OUTLINE (every block on those pages in reading order; * marks the selected ones; you may insert after any of them):")
    for i in outline_ids:
        b = by[i]
        preview = "[picture]" if b.get("is_picture") else _words(b.get("formatted_text"))[:60]
        parts.append(f'{i} {"*" if i in sel else "-"} [{b.get("bodhan_type") or "Text"}] "{preview}"')
    if history:
        parts.append("\nEARLIER IN THIS SESSION (oldest first):\n" + "\n".join(history))
    content = [{"type": "text", "text": prompt_text + "\n".join(parts)}]
    pages = _assist_pages(by, ids)
    imgs = {p: ns["preprocess_page"](p - 1) for p in pages}
    for p in sorted({by[i]["page"] for i in ids}):
        content += [{"type": "text", "text": f"Page {p}, the whole page:"},
                    {"type": "image_url", "image_url": {"url": ns["_img_uri"](imgs[p], max_side=1100)}}]
    pad, n = config.CROP_PAD, 0
    for i in ids:                                             # a merged block: its own (union) box, plus any part that sits on another page
        shots = [(f"block {i}", by[i]["page"], ns["_bn"](by[i]))]
        shots += [(f"part {a['block_id']} of {i}", a["page"], ns["_bn"](a)) for a in by[i].get("merged_from") or [] if a["page"] != by[i]["page"]]
        for name, page, bn in shots:
            if not bn or n >= ASSIST_CROPS_MAX:
                continue
            img = imgs[page]
            H, W = img.shape[:2]
            crop = img[int(max(0, bn[1] - pad) * H):int(min(1, bn[3] + pad) * H), int(max(0, bn[0] - pad) * W):int(min(1, bn[2] + pad) * W)]
            if crop.size:
                n += 1
                content += [{"type": "text", "text": f"Crop of {name}:"},
                            {"type": "image_url", "image_url": {"url": ns["_img_uri"](crop, max_side=1100)}}]
    return content


def assist(job_dir, req):
    """Any request about the selected blocks: edit, merge, insert (for example a table), delete, or just an answer.
    Every action becomes a card the person accepts or rejects; nothing is applied here."""
    d = _prepare(job_dir)
    blocks = store.current_blocks(d.name)
    by, order = {b["block_id"]: b for b in blocks}, [b["block_id"] for b in blocks]
    unknown = [i for i in req.get("blocks") or [] if i not in by]
    if unknown or not req.get("blocks"):
        raise ValueError(f"unknown or empty selection: {unknown}")
    ids = sorted(set(req["blocks"]), key=order.index)
    pages = _assist_pages(by, ids)
    if len(pages) > ASSIST_PAGES_MAX:
        raise ValueError(f"The selection reaches over {len(pages)} pages; select blocks from at most {ASSIST_PAGES_MAX} pages.")
    for i in ids:
        if len(by[i].get("formatted_text") or "") > ASSIST_BLOCK_MAX:
            raise ValueError(f"{_lab(i)} is too long to send; select smaller blocks.")
    if sum(len(by[i].get("formatted_text") or "") for i in ids) > ASSIST_TOTAL_MAX:
        raise ValueError("The selected blocks are too long to send together; select fewer blocks.")
    note = (req.get("note") or "").strip()[:1000] or prompts.load("default_instruction.txt").strip()

    ns = nb.load()
    _need_keys(ns)
    model = req.get("model") or review_model(ns)
    if model not in {m for opts in ns["MODEL_OPTIONS"].values() for m in opts}:
        raise ValueError(f"model not allowed: {model}")
    outline_ids = [i for i in order if by[i]["page"] in {by[x]["page"] for x in ids}]
    prompt = prompts.load("assist.txt").replace("{tags}", ns["TAG_GRAMMAR"]).replace("{note}", note)
    ns["set_stage"]("review", "assist")
    raw = ns["_or_chat"](model, _assist_content(ns, prompt, by, ids, outline_ids, _history_text(d)), max_tokens=6000)
    print(f"assist {d.name} {ids}: {raw[:1500]!r}")
    reply, actions = _parse_assist(raw)

    sel, outline = set(ids), set(outline_ids)
    cards, skipped, used, removed = [], [], set(), set()
    pending_inserts = []

    def skip(what, why):
        skipped.append({"id": what, "reason": why})

    def card(kind, title, involved, before, after, events, warn=None, label=None, ref=None):
        first = [by[i] for i in involved]
        cards.append({"id": f"a{len(cards) + 1}", "kind": kind, "title": title, "ref": ref or involved[0], "ids": involved,
                      "page": first[0]["page"], "bbox": _union_bbox(first, ns) if kind != "insert" else None,
                      "before": before, "after": after, "fields": {"formatted_text": after} if kind == "edit" else {},
                      "plain_text_fallback": False, "warn": warn, "label": label,
                      "expect": {i: by[i].get("formatted_text") or "" for i in involved if kind != "insert"}, "events": events})

    for a in actions:
        t, at = a["type"], a["attrs"]
        try:
            if t == "edit":
                i = (at.get("id") or "").strip()
                if i not in sel: raise ValueError(f"{i or 'an id'} is not one of the selected blocks")
                if i in used: raise ValueError("already changed by another action")
                if by[i].get("is_picture"): raise ValueError("a picture has no text to edit; use Re-read")
                new, old = _clean_markup(a["body"]), by[i].get("formatted_text") or ""
                if new.strip() == old.strip(): raise ValueError("the model found nothing to change")
                used.add(i)
                card("edit", _lab(i), [i], old, new, [{"op": "set_fields", "block": i, "fields": {"formatted_text": new}}],
                     warn=None if _same_words(new, old) else "The words changed. Check Compare before accepting.", label="changed")
            elif t == "merge":
                mids = sorted({x.strip() for x in (at.get("ids") or "").split(",") if x.strip()}, key=lambda x: order.index(x) if x in order else 10 ** 9)
                bad = [x for x in mids if x not in sel]
                if len(mids) < 2: raise ValueError("a merge needs at least two blocks")
                if bad: raise ValueError(f"{', '.join(bad)} is not one of the selected blocks")
                if used & set(mids): raise ValueError("a block is already used by another action")
                if any(by[x].get("is_picture") for x in mids): raise ValueError("a picture cannot be merged into text")
                new = _clean_markup(a["body"])
                old = "\n".join(by[x].get("formatted_text") or "" for x in mids)
                used |= set(mids); removed |= set(mids[1:])
                fields = {"formatted_text": new, "merged_from": [at_ for x in mids for at_ in _atoms(by[x])]}
                here = [by[x] for x in mids if by[x]["page"] == by[mids[0]]["page"]]      # the box grows to cover every piece on the first block's page
                boxes = [ns["_bn"](b_) for b_ in here]
                if all(boxes):
                    u = [min(b_[0] for b_ in boxes), min(b_[1] for b_ in boxes), max(b_[2] for b_ in boxes), max(b_[3] for b_ in boxes)]
                    pg = ns["doc"][by[mids[0]]["page"] - 1].rect
                    W, H = pg.width * ns["DPI"] / 72, pg.height * ns["DPI"] / 72
                    fields.update(bbox_norm=[round(v, 6) for v in u], bbox_xyxy=[u[0] * W, u[1] * H, u[2] * W, u[3] * H])
                card("merge", "Merge " + " + ".join(_lab(x) for x in mids), mids, old, new,
                     [{"op": "set_fields", "block": mids[0], "fields": fields}, {"op": "delete", "blocks": mids[1:]}],
                     warn=None if _same_bag(new, old) else "The merged words differ from the original blocks. Check Compare before accepting.", label="merge")
            elif t == "split":
                i = (at.get("id") or "").strip()
                if i not in sel: raise ValueError(f"{i or 'an id'} is not one of the selected blocks")
                if i in used: raise ValueError("already changed by another action")
                atoms = by[i].get("merged_from") or []
                if len(atoms) < 2 or atoms[0]["block_id"] != i: raise ValueError(f"{_lab(i)} was not made by merging, so there is nothing to split")
                taken = [x["block_id"] for x in atoms[1:] if x["block_id"] in by]
                if taken: raise ValueError(f"{', '.join(_lab(x) for x in taken)} already exists")
                used.add(i)
                back = {k: atoms[0].get(k) for k in ("formatted_text", "bbox_norm", "bbox_xyxy", "bodhan_type")}
                card("split", f"Split {_lab(i)} back into {len(atoms)} blocks", [i], by[i].get("formatted_text") or "",
                     "\n".join(x.get("formatted_text") or "" for x in atoms),
                     [{"op": "set_fields", "block": i, "fields": {**back, "merged_from": None}}, {"op": "add_atoms", "after": i, "atoms": atoms[1:]}], label="split")
            elif t == "insert":
                after = (at.get("after") or "").strip()
                if after not in outline: raise ValueError(f"{after or 'an id'} is not a block on these pages")
                pending_inserts.append((after, _clean_markup(a["body"])))
            elif t == "delete":
                dids = [x.strip() for x in (at.get("ids") or at.get("id") or "").split(",") if x.strip()]
                if not dids: raise ValueError("no block named")
                for x in dids:
                    if x not in sel: raise ValueError(f"{x} is not one of the selected blocks")
                    if x in used: raise ValueError(f"{_lab(x)} is already used by another action")
                for x in dids:
                    used.add(x); removed.add(x)
                    card("delete", _lab(x), [x], by[x].get("formatted_text") or "", "", [{"op": "delete", "blocks": [x]}], label="to delete")
            else:
                raise ValueError(f"unknown action {t!r}")
        except ValueError as e:
            skip(at.get("id") or at.get("ids") or at.get("after") or t or "action", str(e))
    for after, markup in pending_inserts:                      # last, so we know which anchors survive
        if after in removed:
            skip(after, "the new block was to go after a block that is removed")
            continue
        kind = "Table" if markup.lstrip().startswith("<TABLE") else "Text"
        card("insert", f"New {kind.lower()} after {_lab(after)}", [after], "", markup,
             [{"op": "insert_after", "after": after, "markup": markup, "type": kind}], label="new block", ref=after)
    if not reply:
        reply = "Here is what I propose." if cards else "I did not find anything to change."
    return {"mode": "assist", "note": req.get("note") or "", "model": model, "message": reply, "blocks": cards,
            "skipped": skipped, "usage": _usage(ns)}


OPS = {"rebuild": rebuild, "review": review, "newbox": newbox, "assist": assist}


def main(argv):
    op, job_dir, req_path, out_path = argv[1:5]
    try:
        req = json.loads(Path(req_path).read_text(encoding="utf-8"))
        out = {"ok": True, "result": OPS[op](job_dir, req)}
    except Exception as e:                      # reported to the web server as a readable message
        traceback.print_exc()
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
