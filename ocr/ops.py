"""Operations on one job. Each runs in its own short-lived process because the notebook keeps
its state in global variables (one PDF, one set of counters) and cannot share a process with another job.

    python -m ocr.ops <rebuild|review|newbox|assist> <job_dir> <request.json> <result.json>

Everything that is pipeline logic comes from the stage files in pipeline/ (see pipeline/runtime.py); this file only wires it up.
"""
import contextlib
import copy
import time
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

from pipeline import runtime as nb
from . import config, prompts, store

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
        raise nb.PipelineError("pipeline changed: no 'classify' model to use for review")
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
            raise nb.PipelineError("pipeline changed: the formatting prompt no longer has its closing instruction")
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
            "tok_out": sum(c["tok_out"] for c in calls), "cost": round(sum((c["cost"] or 0) for c in calls), 6),
            "seconds": round(sum(c["seconds"] for c in calls), 1)}


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


_PAGE_WORDS = re.compile(r"\b(re-?read|check\w*|verif\w*|compar\w*|match\w*|correct\w*|wrong|incorrect|missing|mis-?read|mis-?spell\w*|spelling|typos?|ocr|merg\w*|combin\w*|join\w*|split\w*|insert\w*|add\w*|delet\w*|order|move|swap|translat\w*)\b", re.I)
_LAYOUT_WORDS = re.compile(r"\b(format\w*|layout|align\w*|cent(?:er|re)\w*|left|right|justif\w*|bold|italic\w*|underlin\w*|head(?:ing|er)s?|title|lists?|bullet\w*|number(?:ed|ing)|tables?|outlines?|borders?|lines?|rows?|columns?|cells?|indent\w*|spacing|gap|font|size|paragraphs?|colou?r)\b", re.I)


def _scope(note):
    """'layout' when the request is only about how blocks look (then: block crops only, and the words are locked), else 'other'.
    Only decides what is sent and what is checked; the model still decides what to do."""
    return "layout" if _LAYOUT_WORDS.search(note) and not _PAGE_WORDS.search(note) else "other"


def _chat_fast(ns, model, content, max_tokens):
    """One model call that fails fast: the notebook's helper waits up to ~85 s in back-off, too long for a person at the screen.
    First asks OpenRouter for its fastest provider; if that option is refused, or the call fails, one plain retry after 2 s."""
    import time
    import requests
    head = {"Authorization": f"Bearer {ns['OPENROUTER_KEY']}", "Content-Type": "application/json"}
    body = {"model": model, "temperature": 0.0, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
    last = None
    for k, extra in enumerate(({"provider": {"sort": "throughput"}}, {}, {})):
        try:
            r = requests.post(ns["OPENROUTER_URL"], headers=head, json={**body, **extra}, timeout=(10, 100))
            if r.status_code == 402:
                raise RuntimeError("HTTP 402: OpenRouter credits exhausted")
            if r.status_code in (400, 404, 422) and extra:               # the provider option was not accepted: go straight to a plain call
                last = RuntimeError(f"HTTP {r.status_code}")
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code} from {model}")
                time.sleep(2)
                continue
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                last = RuntimeError(str(data["error"])[:200])
                time.sleep(2)
                continue
            return data["choices"][0]["message"]["content"] or "", data["choices"][0].get("finish_reason")
        except requests.RequestException as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"the model did not answer ({last}); try again")


# ── removing a table's outline: an exact operation, done in code (a model retyping a long table drops words and takes half a minute) ──
_TABLE_TAGS = re.compile(r"<(/?)(TABLE|ROW|CELL)\b([^<>]*?)(/?)>", re.I)
_BLOCKISH = re.compile(r"<(P|ITEM|OPTIONS|OPTION|LIST|TABLE|QUESTION)\b", re.I)
_KEEP_ON_P = ("align", "bold", "italic", "underline", "size", "font", "color")
_TABLE_WORD = re.compile(r"\btables?\b", re.I)
_OUTLINE_WORD = re.compile(r"\b(outlines?|borders?|grid|gridlines?|frame|boxe?s?)\b", re.I)
_REMOVE_WORD = re.compile(r"\b(remov\w*|delet\w*|drop|hide|without|get\s+rid|take\s+(?:off|away)|clear|no\s+need|not\s+(?:required|requried|needed|necessary))\b", re.I)
_ALL_TABLES = re.compile(r"\b(?:all|every)\s+(?:the\s+)?(?:table|outline|border)s?\b|\bnested\b|\binner\b", re.I)


def _wants_unwrap(note):
    """The request is to take the outline off a table and keep what is inside."""
    return bool(_TABLE_WORD.search(note) and _OUTLINE_WORD.search(note) and _REMOVE_WORD.search(note))


def _cell_blocks(content, attrs, header):
    """What one cell becomes once its table is gone: its own paragraphs, or its plain text as one paragraph."""
    inner = content.strip()
    if not inner:
        return ""
    if _BLOCKISH.search(inner):
        if header:
            inner = re.sub(r"<P\b(?![^>]*\bbold=)([^>]*)>", r'<P bold="true"\1>', inner)       # a header row is bold in the Word file
        return inner + "\n"
    keep = [f'{k}="{v}"' for k, v in re.findall(r'([A-Za-z_]+)\s*=\s*"([^"]*)"', attrs) if k.lower() in _KEEP_ON_P]
    if header and not any(k.startswith("bold=") for k in keep):
        keep.append('bold="true"')
    return f"<P{' ' + ' '.join(keep) if keep else ''}>{inner}</P>\n"


def _unwrap_outer(markup):
    """Take the outline off the outer table(s): rows and cells disappear, each cell's content stays, in reading order.
    A table nested inside a cell is kept as it is."""
    out, cell, pos, depth, header, cell_attrs = [], None, 0, 0, False, ""

    def put(s):
        (cell if cell is not None else out).append(s)

    for m in _TABLE_TAGS.finditer(markup):
        put(markup[pos:m.start()])
        pos = m.end()
        close, name, attrs, selfclose = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
        if name == "TABLE":
            if not close:
                depth += 1
                if depth > 1:
                    put(m.group(0))
            else:
                if depth > 1:
                    put(m.group(0))
                depth = max(0, depth - 1)
        elif depth == 1:                                         # the outer table's own rows and cells: dropped
            if name == "ROW" and not close:
                header = bool(re.search(r'header\s*=\s*"?(true|1|yes)', attrs, re.I))
            elif name == "CELL" and not selfclose:
                if not close:
                    cell, cell_attrs = [], attrs
                elif cell is not None:
                    out.append(_cell_blocks("".join(cell), cell_attrs, header))
                    cell = None
        else:
            put(m.group(0))
    put(markup[pos:])
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def _unwrap_all(markup):
    """The same, until no table is left (tables nested in cells included)."""
    for _ in range(6):
        if not re.search(r"<TABLE\b", markup, re.I):
            break
        markup = _unwrap_outer(markup)
    return markup


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


_ATOM_KEYS = ("block_id", "page", "bbox_xyxy", "bbox_norm", "bodhan_type", "is_picture", "image_path", "formatted_text", "text", "page_wh", "context")
CTX_KEEP = 12       # entries kept on a block
CTX_SHOW = 8        # entries shown to the model per block


def _atom_ids(b):
    """Ids of the original pieces behind a block: itself, or what earlier merges joined into it."""
    return [a["block_id"] for a in b["merged_from"]] if b.get("merged_from") else [b["block_id"]]


def _flat(x, n=200):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def _entry(eid, asked, said, did):
    """One line of a block's context: what was asked, what the assistant said, what was done. `eid` is unique, so the same entry is never listed twice."""
    return {"eid": eid, "t": round(time.time(), 3), "asked": _flat(asked), "said": _flat(said), "did": _flat(did)}


def _ctx_add(ctx, entry):
    return ([e for e in (ctx or []) if e.get("eid") != entry["eid"]] + [entry])[-CTX_KEEP:]


def _ctx_join(*lists):
    """The contexts of several blocks as one, oldest first, each entry once."""
    seen, out = set(), []
    for lst in lists:
        for e in lst or []:
            if e.get("eid") not in seen:
                seen.add(e.get("eid"))
                out.append(e)
    out.sort(key=lambda e: e.get("t", 0))
    return out[-CTX_KEEP:]


def _atoms_with_context(b, entry):
    """The original pieces behind a block, as they were when merged (flat, so a split always restores the originals), each with its own
    context plus `entry`. A block that was merged before and has since got more context passes that on to every piece."""
    ctx = b.get("context") or []
    if b.get("merged_from"):
        atoms = copy.deepcopy(b["merged_from"])
        known = {e.get("eid") for a in atoms for e in (a.get("context") or [])}
        extra = [e for e in ctx if e.get("eid") not in known]
        for a in atoms:
            a["context"] = _ctx_join(a.get("context"), extra, [entry])
        return atoms
    atom = {k: b[k] for k in _ATOM_KEYS if k in b}
    atom["context"] = _ctx_join(ctx, [entry])
    return [atom]


def _merged_id(existing, page, atom_ids):
    """The id of a merged block: its pieces' ids joined (p1_B1_B2), made unique. It cannot be mistaken for another block's id."""
    tails = [re.sub(r"^p\d+_", "", a) for a in atom_ids]
    name = "_".join(tails)
    if len(name) > 24:
        name = "_".join(tails[:3]) + f"_plus{len(tails) - 3}"
    base, taken = f"p{page}_{name}", set(existing) | {_key(i) for i in existing}
    cand, n = base, 2
    while cand in taken or _key(cand) in taken:
        cand, n = f"{base}_{n}", n + 1
    return cand


def _same_bag(a, b):
    """True when two tag strings hold the same words, in any order (a merge may put the right-hand piece last)."""
    f = lambda m: sorted(re.sub(r"\$", "", _words(m)).split())
    return f(a) == f(b)


def _card_said(c, new_id=None):
    """One card in plain words for a block's context (a merge names the new block and says the pieces no longer exist)."""
    ids = c.get("ids") or []
    kind = c.get("kind")
    if kind == "merge" and len(ids) > 1:
        return (f"merged {', '.join(_lab(x) for x in ids)} into the new block {_lab(new_id)}" if new_id
                else f"merge {', '.join(_lab(x) for x in ids)} into one new block") + " (the pieces no longer exist on their own)"
    if kind == "split" and ids:
        return f"split {_lab(ids[0])} back into its parts"
    if kind == "insert" and ids:
        return f"new block after {_lab(ids[0])}"
    if kind in ("edit", "delete") and ids:
        return f"{kind} {_lab(ids[0])}"
    return c.get("title") or _lab(c["id"])


def _key(x):
    """Block ids compare without case or punctuation: p1_B6, P1_b6 and 'p1 · B6' are the same block."""
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


def _context_text(d, by, ids):
    """The context of each selected block, oldest first. Accepted requests are kept on the block itself (so Undo takes them away, and a
    merged block carries the context of every block it was made from, in order). Rejected ones come from the saved proposals."""
    rows = [p for p in (store.read_json(f) for f in (d / "proposals").glob("*.json")) if p]
    out, any_ = [], False
    for i in ids:
        mine = set(_atom_ids(by[i]))
        lines = [(e.get("t", 0), f"- asked \u00ab{e.get('asked', '')}\u00bb; answer: {e.get('said', '')}; done: {e.get('did', '')}") for e in by[i].get("context") or []]
        for p in rows:
            for c in p.get("blocks", []):
                if c.get("resolved") == "rejected" and mine & set(c.get("ids") or []):
                    lines.append((p.get("created", 0), f"- asked \u00ab{_flat(p.get('note'))}\u00bb; proposed: {_card_said(c)}; the person REJECTED it"))
        lines = [l for _, l in sorted(lines, key=lambda x: x[0])][-CTX_SHOW:]
        any_ = any_ or bool(lines)
        out.append(f"{i}:" + ("" if lines else " (nothing yet)"))
        out.extend(lines)
    return out if any_ else []


def _same_words(a, b):
    """True when two tag strings hold the same words in the same order (spacing and $ signs ignored)."""
    f = lambda m: re.sub(r"[\s$]+", "", _words(m))
    return f(a) == f(b)


def _parse_assist(raw):
    """(reply, [{'type', 'attrs', 'body'}]) from the model's answer. Raises ValueError when nothing can be read."""
    text = raw or ""
    r = re.search(r"<REPLY>(.*?)(?:</REPLY>|<ACTION\b|\Z)", text, re.S | re.I)
    reply = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html.unescape(r.group(1)))).strip() if r else ""      # the model sometimes puts an action inside the reply
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


def _assist_content(ns, prompt_text, by, ids, outline_ids, history=(), with_page=True):
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
        parts.append("\nCONTEXT OF EACH SELECTED BLOCK (what was asked and decided about it, oldest first; a merged block carries the context of the blocks it was made from, in order):\n" + "\n".join(history))
    parts.append("\nIMAGES ATTACHED: " + ("the whole page and a crop of each selected block." if with_page else "a crop of each selected block only."))
    content = [{"type": "text", "text": prompt_text + "\n".join(parts)}]
    pages = _assist_pages(by, ids)
    imgs = {p: ns["preprocess_page"](p - 1) for p in pages}
    for p in sorted({by[i]["page"] for i in ids}) if with_page else []:
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
    if _wants_unwrap(note):                                                       # exact and instant: no model, no words can change
        every = bool(_ALL_TABLES.search(note))
        cards, skipped = [], []
        for i in ids:
            old = by[i].get("formatted_text") or ""
            if by[i].get("is_picture") or not re.search(r"<TABLE\b", old, re.I):
                continue
            new = _unwrap_all(old) if every else _unwrap_outer(old)
            if not new or not _same_words(new, old):
                skipped.append({"id": i, "reason": "the outline could not be removed without touching the words"})
                continue
            cards.append({"id": f"a{len(cards) + 1}", "kind": "edit", "title": _lab(i), "ref": i, "ids": [i], "page": by[i]["page"],
                          "bbox": _union_bbox([by[i]], ns), "before": old, "after": new, "fields": {"formatted_text": new},
                          "plain_text_fallback": False, "warn": None, "label": "table outline removed", "expect": {i: old},
                          "events": [{"op": "set_fields", "block": i, "fields": {"formatted_text": new}}]})
        if cards:
            kept = any(re.search(r"<TABLE\b", c["after"], re.I) for c in cards)
            msg = "Removed the table outline; the text and its structure are unchanged."
            if kept:
                msg += ' A table inside a cell was kept; say "remove all table outlines" to remove those too.'
            print(f"assist {d.name} {ids}: table outline removed in code, no model call")
            return {"mode": "assist", "note": req.get("note") or "", "model": "built in", "message": msg, "blocks": cards,
                    "skipped": skipped, "usage": _usage(ns)}
    _need_keys(ns)
    model = req.get("model") or config.ASSIST_MODEL or review_model(ns)             # the person's request, then the server setting, then the notebook
    if req.get("model") and model not in {m for opts in ns["MODEL_OPTIONS"].values() for m in opts}:
        raise ValueError(f"model not allowed: {model}")
    outline_ids = [i for i in order if by[i]["page"] in {by[x]["page"] for x in ids}]
    prompt = prompts.load("assist.txt").replace("{tags}", ns["TAG_GRAMMAR"]).replace("{note}", note)
    ns["set_stage"]("review", "assist")
    scope = _scope(note)
    with_page = scope == "other" or any(not ns["_bn"](by[i]) for i in ids)      # a block without a box has no crop: send the page
    t0 = time.time()
    content = _assist_content(ns, prompt, by, ids, outline_ids, _context_text(d, by, ids), with_page)
    size = sum(len(by[i].get("formatted_text") or "") for i in ids)
    max_tokens = config.ASSIST_MAX_TOKENS or min(8000, 1500 + 2 * size)             # room to write the blocks back: Hindi and other scripts need about a token per character
    t1 = time.time()
    raw, finish = _chat_fast(ns, model, content, max_tokens)
    t2 = time.time()
    print(f"assist {d.name} {ids}: scope={scope} page={with_page} images={sum(c.get('type') == 'image_url' for c in content)} "
          f"prompt_chars={len(content[0]['text'])} max_tokens={max_tokens} finish={finish} prepare={t1 - t0:.1f}s model={t2 - t1:.1f}s answer={raw[:1500]!r}")
    if finish == "length":
        raise ValueError("The model's answer was cut off because the blocks are long. Select fewer blocks (or a smaller part of the page) and try again.")
    reply, actions = _parse_assist(raw)

    sel, outline = set(ids), set(outline_ids)
    cards, skipped, used, removed = [], [], set(), set()
    pending_inserts = []

    keymap = {_key(i): i for i in order}
    merged_into = {_key(a["block_id"]): host for host in order for a in (by[host].get("merged_from") or []) if a["block_id"] != host}

    def pick(x):
        k = _key(x)
        if k in keymap:
            return keymap[k]
        if k in merged_into:
            raise ValueError(f"{x} no longer exists: it was merged into {_lab(merged_into[k])}")
        raise ValueError(f"{x or 'an id'} is not a block of this document")

    def skip(what, why):
        skipped.append({"id": what, "reason": why})

    # a replacement is an edit: "delete this block and add a new one" would lose the block's place on the page
    dels = [a for a in actions if a["type"] == "delete"]
    ins = [a for a in actions if a["type"] == "insert"]
    if len(ins) == 1 and dels:
        try:
            gone = [pick(x) for a in dels for x in re.split(r"[,\s]+", (a["attrs"].get("ids") or a["attrs"].get("id") or "").strip()) if x]
        except ValueError:
            gone = []
        if len(gone) == 1 and gone[0] in sel and not by[gone[0]].get("is_picture"):
            actions = [a for a in actions if a not in dels and a is not ins[0]] + [{"type": "edit", "attrs": {"id": gone[0]}, "body": ins[0]["body"]}]

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
                i = pick(at.get("id"))
                if i not in sel: raise ValueError(f"{_lab(i)} is not selected; select it first")
                if i in used: raise ValueError("already changed by another action")
                new, old = _clean_markup(a["body"]), by[i].get("formatted_text") or ""
                fresh = bool(by[i].get("is_picture")) or not old.strip()           # a picture or an empty block read from its crop: the words are new by design
                if new.strip() == old.strip(): raise ValueError("the model found nothing to change")
                if scope == "layout" and not fresh and not _same_words(new, old): raise ValueError("this was a layout request but the model changed the words, so it was not offered")
                used.add(i)
                fields = {"formatted_text": new}
                if by[i].get("is_picture"):
                    fields.update(is_picture=False, bodhan_type="Text")
                card("edit", _lab(i), [i], old, new, [{"op": "set_fields", "block": i, "fields": fields}],
                     warn=None if fresh or _same_words(new, old) else "The words changed. Check Compare before accepting.", label="read from image" if fresh else "changed")
                cards[-1]["fields"] = fields
            elif t == "merge":
                mids = sorted({pick(x) for x in re.split(r"[,\s]+", (at.get("ids") or "").strip()) if x}, key=order.index)
                bad = [x for x in mids if x not in sel]
                if len(mids) < 2: raise ValueError("a merge needs at least two different blocks (a block that shows parts= is already merged)")
                if bad: raise ValueError(f"{', '.join(_lab(x) for x in bad)} is not selected; select it first")
                if used & set(mids): raise ValueError("a block is already used by another action")
                if any(by[x].get("is_picture") for x in mids): raise ValueError("a picture cannot be merged into text")
                new = _clean_markup(a["body"])
                old = "\n".join(by[x].get("formatted_text") or "" for x in mids)
                used |= set(mids); removed |= set(mids[1:])
                fields = {"formatted_text": new}
                here = [by[x] for x in mids if by[x]["page"] == by[mids[0]]["page"]]      # the box grows to cover every piece on the first block's page
                boxes = [ns["_bn"](b_) for b_ in here]
                if all(boxes):
                    u = [min(b_[0] for b_ in boxes), min(b_[1] for b_ in boxes), max(b_[2] for b_ in boxes), max(b_[3] for b_ in boxes)]
                    pg = ns["doc"][by[mids[0]]["page"] - 1].rect
                    W, H = pg.width * ns["DPI"] / 72, pg.height * ns["DPI"] / 72
                    fields.update(bbox_norm=[round(v, 6) for v in u], bbox_xyxy=[u[0] * W, u[1] * H, u[2] * W, u[3] * H])
                card("merge", "Merge " + " + ".join(_lab(x) for x in mids), mids, old, new,
                     [{"op": "merge_blocks", "ids": mids, "fields": fields}],
                     warn=None if _same_bag(new, old) else "The merged words differ from the original blocks. Check Compare before accepting.", label="merge")
            elif t == "unwrap":
                i = pick(at.get("id"))
                if i not in sel: raise ValueError(f"{_lab(i)} is not selected; select it first")
                if i in used: raise ValueError("already changed by another action")
                old = by[i].get("formatted_text") or ""
                if by[i].get("is_picture") or not re.search(r"<TABLE\b", old, re.I): raise ValueError(f"{_lab(i)} has no table")
                new = _unwrap_all(old) if str(at.get("all", "")).lower() in ("1", "true", "yes") else _unwrap_outer(old)
                if not new or not _same_words(new, old): raise ValueError("the outline could not be removed without touching the words")
                used.add(i)
                card("edit", _lab(i), [i], old, new, [{"op": "set_fields", "block": i, "fields": {"formatted_text": new}}], label="table outline removed")
                cards[-1]["fields"] = {"formatted_text": new}
            elif t == "split":
                i = pick(at.get("id"))
                if i not in sel: raise ValueError(f"{_lab(i)} is not selected; select it first")
                if i in used: raise ValueError("already changed by another action")
                atoms = by[i].get("merged_from") or []
                if len(atoms) < 2: raise ValueError(f"{_lab(i)} was not made by merging, so there is nothing to split")
                taken = [x["block_id"] for x in atoms if x["block_id"] in by]
                if taken: raise ValueError(f"{', '.join(_lab(x) for x in taken)} already exists")
                used.add(i)
                card("split", f"Split {_lab(i)} back into {len(atoms)} blocks", [i], by[i].get("formatted_text") or "",
                     "\n".join(x.get("formatted_text") or "" for x in atoms), [{"op": "split_block", "id": i}], label="split")
            elif t == "insert":
                after = pick(at.get("after"))
                if after not in outline: raise ValueError(f"{_lab(after)} is not on the selected pages")
                pending_inserts.append((after, _clean_markup(a["body"])))
            elif t == "delete":
                dids = [pick(x) for x in re.split(r"[,\s]+", (at.get("ids") or at.get("id") or "").strip()) if x]
                if not dids: raise ValueError("no block named")
                for x in dids:
                    if x not in sel: raise ValueError(f"{_lab(x)} is not selected; select it first")
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
