# Stage file 'digital': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── ROUTE 1: digital English → Docling Heron → ordering prompt → tags ────
# DIGITAL_ORDER_PROMPT = """You get ONE page: the page image with every detected block boxed and labelled (B1, B2, ...), and a JSON list of those blocks (NOT in reading order). Return the reading order.
# Fields: id (the label on the image), type, bbox [x0,y0,x1,y1] normalised 0-1 from top-left, text (long blocks: text_start + text_end; tables/figures/formulas carry no text).

# RULES
# 1 LAYOUT. Look at the boxes. Split the page into horizontal ZONES (top to bottom) wherever a full-width block spans the page or the column count changes. Inside a multi-column zone read the LEFT column completely top to bottom, then the next column; never read across a row. Blocks side by side in one row (e.g. two images): left to right. Use bbox + image, not the JSON order.
# 2 PAIRING. A caption stays with its figure/table (figure then caption; a table caption on the side of the table where it sits). A title/section_header goes directly before the text it introduces. A formula stays where it sits in its column.
# 3 TEXT CONTINUITY (the only thing allowed to change the base order; judge from the text):
#  a) If the first body text block of the page starts mid-sentence (fragment / lowercase), it continues the previous page: put it FIRST, before any figure, table or caption; figures/tables that moved behind it follow right after it.
#  b) If the last text block of a column ends cut off and the first text block of the next column completes it, place that block right after it; figures/tables lying between go after the completed block.
#  c) A missing final period alone is NOT a cut-off. If unsure, keep the base order.
# 4 FURNITURE. Footnotes after the body, then page_header, page_footer, page_number at the very end. Exception: if the last body block is cut off mid-sentence (continues next page), put the furniture just BEFORE it so it is last.
# Every id exactly once, only ids from the list. Answer with ONLY this JSON, no explanation: {"order": ["B3","B1", ...]}"""


DIGITAL_ORDER_PROMPT = """You are given ONE document page: the page image, where every detected block is boxed and labelled (B1, B2, ...), and a JSON list of those blocks. Output the reading order of the blocks.
JSON fields: id (the label drawn on the image), type (title, section_header, text, list_item, caption, footnote, table, picture, formula, page_header, side_header, page_footer), bbox, text (long texts are given as text_start + text_end, the middle is omitted). Pictures, tables and formulas carry no text. The JSON list is NOT in reading order. Find every block on the image by its id.

GOAL. The order must be the natural reading order (top to bottom, left column then right column). The ONLY allowed deviations are the continuity fixes in STEP 3 and STEP 4: they exist so that a sentence that is cut off is never separated from its completion, on this page or across the page break.

Plan things from the image, as no of tokens are very limited, should understand complete quickly

STEP 1 - LAYOUT. Look at the image first and decide the layout from the boxes: single column; two or more columns; full-width blocks above, below or between columns; images/tables side by side; image beside text; or a mix that changes down the page. Split the page into horizontal ZONES, top to bottom, wherever a full-width block spans the page or the column structure changes. Reading rules inside a zone:
- single column: top to bottom.
- 2 columns: read the LEFT column completely, top to bottom, then the next column to its right. NEVER read across a row of a 2-column zone.
- blocks side by side in one row (e.g. two images): left to right.

STEP 2 - BASE ORDER. Zones top to bottom; once inside a 2-column zone, left column (top to bottom) ONLY then right column (top to bottom). A caption stays with its figure/picture (object, then caption) and with its table (caption on the side of the table where it sits). A formula stays where it sits in its column. A title or section_header stays directly before the text it introduces.
NOT BODY TEXT: page_header stays where it sits (top of the page, normal order) and is never moved. side_header (a narrow block in the left/right margin: rotated stamps, margin ids, line numbers), page_footer, page numbers and footnotes are placed by STEP 4, not by position.

STEP 3 - CONTINUITY INSIDE THE PAGE (MUST CHECK). Decide from the text in the JSON whether a block is cut off or a continuation.
A. Page start. Take the first body text block in base order (ignore page_header, side_header, picture, caption, table). If it starts mid-sentence (word fragment or lowercase) it continues the previous page: put it FIRST, before any figure, picture, table or caption, even if those are higher on the page. The figures/tables (with their captions) that were above it follow right after it (after the block that completes it, if that block is itself cut off). If it starts a fresh sentence or is a heading, change nothing.
   Example: picture, caption, then a text block that continues the previous page -> text block, picture, caption.
B. Column break. Only the LAST body block of a column can continue into the FIRST body block of the next column. If it ends cut off (mid-word, hyphen, no end punctuation, dangling clause) and the first block of the next column reads on from it, place that block immediately after it. Nothing may sit between them: a footnote at the bottom of the left column, or a figure/table lying between, goes AFTER the completed block. Verify: end of the first block + start of the second must read as one sentence. A block with another body block below it in its own column never continues into another column.
C. A missing final period alone is NOT a cut-off (it is often dropped): if the next block starts a new sentence or paragraph, the block is complete. On weak or ambiguous evidence keep the base order.

STEP 4 - PAGE END. Order the end of the page as: remaining body, footnotes, then side_header, page_footer, page number. page_header is not part of this: it stays at the top. Exception: if the LAST body text block of the page (last in reading order, e.g. bottom of the right column) is cut off, it continues on the NEXT page, so it must be the very last item: put the footnotes, side_header, page_footer and page number immediately BEFORE it. Do not look for its continuation on this page.

FLOW CHECK (do this last, before you answer): read your final order from first to last like a person reading the page. Blocks side by side on one row (e.g. "Time ..." and "Max marks ...") go left to right; a heading / block comes right before its content; numbered or lettered items (1, 2, 3 ... / (a), (b) ...) follow in sequence. If a block breaks this flow, fix the order (the continuity and page-end rules above still win), then answer.

RULES: every id appears exactly once; use only ids from the list.
Return ONLY this JSON, no prose, no code fences. Fill the keys in this order and keep every text value to one short sentence:
{
  "layout": "<ASCII sketch of the zones, top to bottom: / between zones, | between columns>",
  "zones": [{"columns": [[ids in column order], ...]}],
  "evaluation": {
    "page_start_check": "<first body text block: starts mid-sentence or lowercase? if yes it goes first>",
    "column_break_check": "<last body block of each column: cut off? does the next column complete it? then nothing may sit between them>",
    "page_end_check": "<last body block in reading order: cut off (hyphen, no full stop, dangling)? if yes, footnotes / side_header / footer / page number go before it so it is last>"
  },
  "order": [ids]
}"""

DIGITAL_BBOX_NOTE = (
    "\nEach region also has bbox [x0,y0,x1,y1], normalized to [0,1] with the "
    "origin at the top-left. Use it together with the image to assign blocks "
    "to zones and columns.\n"
)

_HERON_MAP = {"text": "text", "paragraph": "text", "title": "title", "section_header": "section_header", "list_item": "list_item",
              "caption": "caption", "footnote": "footnote", "table": "table", "document_index": "table", "picture": "picture",
              "formula": "formula", "page_header": "page_header", "page_footer": "page_footer", "code": "text"}
_FURNITURE = {"page_header", "page_footer", "side_header"}
_FIGURE    = {"picture", "formula"}

def heron_layout():
    """Docling (Heron layout model) -> {page_idx: [{'type','rect'(pt, top-left)}]}. OCR + table-structure off: text comes from the PDF text layer."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    opts = PdfPipelineOptions(); opts.do_ocr = False; opts.do_table_structure = False
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    lo, hi = min(page_indices), max(page_indices)
    res = conv.convert(str(PDF_PATH), page_range=(lo + 1, hi + 1))
    out = {}
    for k, pg in enumerate(res.pages):
        pno = lo + k
        if pno not in page_indices: continue
        H = doc[pno].rect.height
        items = []
        for c in pg.predictions.layout.clusters:
            bb = c.bbox
            if "BOTTOM" in str(getattr(bb, "coord_origin", "")).upper(): bb = bb.to_top_left_origin(H)
            lab = getattr(c.label, "value", str(c.label)).lower()
            rect = pymupdf.Rect(bb.l, bb.t, bb.r, bb.b) & doc[pno].rect
            typ = _HERON_MAP.get(lab, "text")
            if typ != "picture" and rect.width < 0.06 * doc[pno].rect.width and rect.height > 2.5 * rect.width:
                typ = "side_header"
            items.append({"type": typ, "rect": rect})
        items = [i for i in items if not i["rect"].is_empty]
        items.sort(key=lambda i: (round(i["rect"].y0), i["rect"].x0))       # neutral order — the LLM decides reading order
        out[pno] = items
    return out

def _hyphen_join(prev, nxt):
    return prev[:-1] + nxt if prev.endswith("-") and nxt[:1].islower() and len(prev) > 1 and prev[-2].isalpha() else prev + " " + nxt

def _read_region(page, rect):
    """-> (segments[(text,bold,italic,sup)], plain_text, dominant_size, nchars)"""
    segs, sizes, plain = [], [], ""
    for b in page.get_text("dict", clip=rect)["blocks"]:
        if b.get("type") != 0: continue
        for ln in b["lines"]:
            lt = "".join(s["text"] for s in ln["spans"])
            if not lt.strip(): continue
            first = True
            for s in ln["spans"]:
                t = s["text"]
                if not t: continue
                fl, fn = s["flags"], s["font"].lower()
                st = (bool(fl & 16) or "bold" in fn or "black" in fn, bool(fl & 2) or "italic" in fn or "oblique" in fn, bool(fl & 1))
                if first and segs:                                   # line join
                    if plain.endswith("-") and t[:1].islower() and len(plain) > 1 and plain[-2].isalpha():
                        plain = plain[:-1]; segs[-1] = (segs[-1][0][:-1],) + segs[-1][1:]
                    else:
                        t = " " + t.lstrip(); 
                first = False
                plain += t; sizes += [s["size"]] * len(t.strip())
                if segs and segs[-1][1:] == st: segs[-1] = (segs[-1][0] + t,) + st
                else: segs.append((t,) + st)
    return segs, plain.strip(), (statistics.median(sizes) if sizes else 0), len(sizes)

def _markup(btype, segs, size, body_size, rect, pw):
    nb = sum(len(t) for t, b, i, s in segs if b) / max(1, sum(len(t) for t, *_ in segs))
    ni = sum(len(t) for t, b, i, s in segs if i) / max(1, sum(len(t) for t, *_ in segs))
    pb, pi = nb >= 0.9, ni >= 0.9
    inner = ""
    for t, b, i, s in segs:
        a = []
        if b and not pb: a.append('bold="true"')
        if i and not pi: a.append('italic="true"')
        if s: a.append('superscript="true"')
        inner += f'<STYLE {" ".join(a)}>{_x(t)}</STYLE>' if a else _x(t)
    inner = inner.strip()
    if btype == "list_item":
        return f'<LIST type="none"><ITEM level="0">{inner}</ITEM></LIST>'
    at = []
    if btype in ("title", "section_header", "caption"):
        cx = (rect.x0 + rect.x1) / 2
        if abs(cx - pw / 2) < 0.04 * pw and rect.width < 0.8 * pw: at.append('align="center"')
    if btype == "title": at.append('heading="1"')
    if btype == "section_header": at.append('heading="2"')
    if size and abs(size - body_size) >= 1.0: at.append(f'size="{round(size * 2) / 2:g}"')
    if pb: at.append('bold="true"')
    if pi: at.append('italic="true"')
    return f'<P {" ".join(at)}>{inner}</P>' if at else f"<P>{inner}</P>"

def _table_markup(page, rect):
    try:
        tabs = page.find_tables(clip=rect).tables
        if not tabs: return None
        rows = tabs[0].extract()
        out = '<TABLE border="1">'
        for ri, r in enumerate(rows):
            out += f'<ROW{" header=" + chr(34) + "true" + chr(34) if ri == 0 else ""}>' + "".join(f"<CELL>{_x((c or '').replace(chr(10), ' ').strip())}</CELL>" for c in r) + "</ROW>"
        return out + "</TABLE>"
    except Exception:
        return None

# def _label_image(pno, regs):
#     img = render_page(pno).copy(); k = DPI / 72
#     for r in regs:
#         x0, y0, x1, y1 = [int(v * k) for v in (r["rect"].x0, r["rect"].y0, r["rect"].x1, r["rect"].y1)]
#         cv2.rectangle(img, (x0, y0), (x1, y1), (30, 110, 220), 2)
#         cv2.putText(img, r["id"], (x0 + 3, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2, cv2.LINE_AA)
#     return img

def _label_image(pno, regs):
    img = render_page(pno).copy(); k = DPI / 72
    H, W = img.shape[:2]
    color, font, fs, th_ = (30, 110, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2
    for r in regs:
        x0, y0, x1, y1 = [int(v * k) for v in (r["rect"].x0, r["rect"].y0, r["rect"].x1, r["rect"].y1)]
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        # label sits OUTSIDE the box, on its top-left corner (tag bottom edge = box top edge)
        (tw, th), base = cv2.getTextSize(r["id"], font, fs, th_)
        pad = 4
        bw, bh = tw + 2 * pad, th + base + 2 * pad
        lx0 = min(max(0, x0), max(0, W - bw))            # keep the tag inside the page horizontally
        ly0 = y0 - bh                                    # above the box
        if ly0 < 0: ly0 = 0                              # box touches the page top: clamp (tag may overlap a few px)
        cv2.rectangle(img, (lx0, ly0), (lx0 + bw, ly0 + bh), color, -1)
        cv2.putText(img, r["id"], (lx0 + pad, ly0 + pad + th), font, fs, (255, 255, 255), th_, cv2.LINE_AA)
    try:                                                 # keep a copy of what the model sees
        cv2.imwrite(str(PAGE_DIR / f"{stem}_p{pno+1}_labeled.png"), img)
    except Exception as e:
        print(f"    ⚠ could not save labeled page {pno+1}: {str(e)[:80]}")
    return img

def _order_page(pno, regs):
    pw, ph = _PAGE_WH[pno]
    lst = []
    for r in regs:
        e = {"id": r["id"], "type": r["type"], "bbox": [round(r["rect"].x0 / pw, 3), round(r["rect"].y0 / ph, 3), round(r["rect"].x1 / pw, 3), round(r["rect"].y1 / ph, 3)]}
        if r["type"] not in ("table", "picture", "formula"):
            t = r["plain"]
            if len(t) <= 300: e["text"] = t
            else: e["text_start"], e["text_end"] = t[:150], t[-150:]
        lst.append(e)
    ids = [r["id"] for r in regs]
    if len(ids) < 2: return ids, "single"
    content = [{"type": "text", "text": DIGITAL_ORDER_PROMPT + DIGITAL_BBOX_NOTE + "\n\nRegions:\n" + json.dumps(lst, ensure_ascii=False)},
               {"type": "image_url", "image_url": {"url": _img_uri(_label_image(pno, regs), max_side=1400)}}]
    try:
        resp = _json_from(_or_chat(M["digital_order"], content, json_mode=True, max_tokens=5000))
        got = [str(i) for i in (resp.get("order") or [])]
        ev = resp.get("evaluation") or {}
        seen = set(); order = []
        for i in got:
            if i in ids and i not in seen: seen.add(i); order.append(i)
        miss = [i for i in ids if i not in seen]
        note = "ok" if not miss else f"repaired, appended {miss}"
        return order + miss, note + f" | start: {str(ev.get('page_start_check'))[:120]} | end: {str(ev.get('page_end_check'))[:120]}"
    except Exception as e:
        return ids, f"LLM order failed ({type(e).__name__}: {str(e)[:80]}) → geometric order"
_TERM = re.compile(r"[.!?:;\"'”’)\]]\s*$")
_HOP_START = {"picture", "table", "caption", "page_header", "side_header"}
_HOP_END   = {"footnote", "page_footer", "side_header"}
def _is_body(r): return r["type"] in ("text", "list_item") and r["plain"].strip()
def _cut(r):
    t = r["plain"].strip(); return bool(t) and (t.endswith("-") or not _TERM.search(t))
def _starts_mid(r):
    t = r["plain"].strip().lstrip("([“\"'‘ "); return bool(t) and t[0].islower()

def _enforce_boundaries(pages, orders, page_indices):
    """Deterministic page-start / page-end continuity. Only acts on lowercase-start + cut-off evidence."""
    by = {p: {r["id"]: r for r in pages[p]} for p in pages}
    body = lambda p: [i for i in orders[p] if _is_body(by[p][i])]
    fixed = 0
    for p in page_indices:                                   # start: continuation text goes first
        b = body(p)
        if b and _starts_mid(by[p][b[0]]):
            head = orders[p][:orders[p].index(b[0])]
            if head and all(by[p][i]["type"] in _HOP_START for i in head):
                orders[p].remove(b[0]); orders[p].insert(0, b[0]); fixed += 1
    for p, q in zip(page_indices, page_indices[1:]):         # end: cut-off block goes last
        if q != p + 1: continue
        bp, bq = body(p), body(q)
        if bp and bq and _cut(by[p][bp[-1]]) and _starts_mid(by[q][bq[0]]):
            tail = orders[p][orders[p].index(bp[-1]) + 1:]
            if tail and all(by[p][i]["type"] in _HOP_END for i in tail):
                orders[p].remove(bp[-1]); orders[p].append(bp[-1]); fixed += 1
    print(f"  boundary enforcement: {fixed} move(s)")

def run_digital_english():
    set_stage("digital", "heron_layout")
    layout = heron_layout()
    set_stage("digital", "extract")
    pages = {}
    allsz = []
    for pno in page_indices:
        regs = []
        for n, it in enumerate(layout.get(pno, []), 1):
            r = {"id": f"B{n}", "type": it["type"], "rect": it["rect"]}
            if it["type"] not in _FIGURE and it["type"] != "table":
                r["segs"], r["plain"], r["size"], nch = _read_region(doc[pno], it["rect"])
                if it["type"] == "text": allsz += [r["size"]] * nch
            else:
                r["plain"] = ""
            regs.append(r)
        pages[pno] = regs
    body_size = statistics.median(allsz) if allsz else 11.0
    set_stage("digital", "order")
    with ThreadPoolExecutor(ORDER_WORKERS) as ex:
        res = list(ex.map(lambda p: _order_page(p, pages[p]), page_indices))
    blocks = []
    orders = {pno: list(order) for pno, (order, _) in zip(page_indices, res)}
    _enforce_boundaries(pages, orders, page_indices)
    for pno, (_, note) in zip(page_indices, res):
        order = orders[pno]

        print(f"  page {pno+1}: {len(pages[pno])} blocks · order {note}")
        pw, ph = doc[pno].rect.width, doc[pno].rect.height; k = DPI / 72
        by = {r["id"]: r for r in pages[pno]}
        for rid in order:
            r = by[rid]; t = r["type"]; rect = r["rect"]
            bn = [rect.x0 / pw, rect.y0 / ph, rect.x1 / pw, rect.y1 / ph]
            bid, wh = f"p{pno+1}_{rid}", (pw * k, ph * k)
            if t in _FURNITURE:
                blocks.append(_mkblock(bid, pno + 1, bn, t, "", None, wh)); continue
            tm = _table_markup(doc[pno], rect) if t == "table" else None
            if t in _FIGURE or (t == "table" and tm is None):
                path = CROP_DIR / f"{stem}_p{pno+1}_{rid}.png"
                doc[pno].get_pixmap(clip=rect, dpi=DPI).save(str(path))
                blocks.append(_mkblock(bid, pno + 1, bn, t, "", str(path), wh)); continue
            mk = tm if tm else (_markup(t, r["segs"], r["size"], body_size, rect, pw) if r["plain"] else "")
            blocks.append(_mkblock(bid, pno + 1, bn, t, mk, None, wh))
    set_stage("-", "-")
    return blocks

if ROUTE == "digital_english":
    ordered_blocks = run_digital_english()
    print(f"digital_english: {len(ordered_blocks)} blocks")
else:
    print("skip digital_english")

