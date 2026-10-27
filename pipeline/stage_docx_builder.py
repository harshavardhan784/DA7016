# Stage file 'docx_a': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ── tag-parser prerequisites (copied from Stage 2 cell so the builder works on every route) ──
STRIP_LITERAL_BACKSLASH_N = True
_KNOWN_TAGS = "P|STYLE|BR|BLANK|LIST|ITEM|QUESTION|OPTIONS|OPTION|TABLE|ROW|CELL|IMG|RIGHT|FORMULA|SPAN"
_TAG_RE = re.compile(
    r"<(?P<close>/?)(?P<name>%s)(?P<attrs>(?:\s+[A-Za-z_][\w-]*\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s<>/\"']+))*)\s*(?P<self>/?)>"
    % _KNOWN_TAGS, re.I)
_ATTR_RE = re.compile(r"""([A-Za-z_][\w-]*)\s*=\s*("[^"]*"|'[^']*'|[^\s<>/"']+)""")
_VOID_TAGS = {"BR", "BLANK", "IMG"}
def _parse_attrs(s):
    return {k.lower(): v.strip("\"'") for k, v in _ATTR_RE.findall(s or "")}

# ─── CELL 6: Stage 3 — tag parser + DOCX render + sidecar + stats ────────

# ── tag parser ──────────────────────────────────────────────────────────
class _Node:
    __slots__ = ("tag", "attrs", "children")
    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []

_XML_BAD_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

def _parse_markup(text):
    """Tolerant tag-tree parser. Unknown '<…>' stays literal text."""
    text = re.sub(r"(?:<|&(?:amp;)*lt;)\s*/?\s*BLOCK\b[^<>]*?(?:/?\s*>|/?\s*&(?:amp;)*gt;)", "", text or "", flags=re.I)
    root = _Node("ROOT")
    stack, pos = [root], 0
    for m in _TAG_RE.finditer(text):
        if m.start() > pos:
            stack[-1].children.append(_html.unescape(text[pos:m.start()]))
        pos = m.end()
        name = m.group("name").upper()
        if m.group("close"):
            for k in range(len(stack) - 1, 0, -1):
                if stack[k].tag == name:
                    del stack[k:]
                    break
            continue
        if name == "P" and stack[-1].tag == "P":      # a <P> inside an open <P>: the writer forgot </P>, so the first paragraph ends here
            del stack[-1:]
        node = _Node(name, _parse_attrs(m.group("attrs")))
        stack[-1].children.append(node)
        if name in _VOID_TAGS or m.group("self"):
            continue
        stack.append(node)
    if pos < len(text):
        stack[-1].children.append(_html.unescape(text[pos:]))
    return root


# ── attribute helpers ───────────────────────────────────────────────────
_ALIGN = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
          "centre": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
          "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
_TBL_ALIGN = {"left": WD_TABLE_ALIGNMENT.LEFT, "center": WD_TABLE_ALIGNMENT.CENTER,
              "right": WD_TABLE_ALIGNMENT.RIGHT}
_HL = {"yellow": "YELLOW", "green": "BRIGHT_GREEN", "cyan": "TURQUOISE", "turquoise": "TURQUOISE",
       "pink": "PINK", "red": "RED", "blue": "BLUE", "gray": "GRAY_25", "grey": "GRAY_25"}

def _truthy(v): return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def _num(v):
    if v is None: return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None

def _style_from_attrs(attrs, base):
    st = dict(base)
    for k in ("bold", "italic", "underline", "strike", "superscript", "subscript"):
        if k in attrs: st[k] = _truthy(attrs[k])
    s = _num(attrs.get("size"))
    if s: st["size"] = max(5.0, min(48.0, s))
    if attrs.get("font"):      st["font"] = attrs["font"].strip()
    if attrs.get("color"):     st["color"] = attrs["color"].strip()
    if attrs.get("highlight"): st["highlight"] = attrs["highlight"].strip().lower()
    return st

def _apply_run_style(r, st):
    if "bold" in st:        r.bold = st["bold"]
    if "italic" in st:      r.italic = st["italic"]
    if "underline" in st:   r.underline = st["underline"]
    if st.get("strike"):      r.font.strike = True
    if st.get("superscript"): r.font.superscript = True
    if st.get("subscript"):   r.font.subscript = True
    if st.get("size"):  r.font.size = Pt(st["size"])
    if st.get("font"):  r.font.name = st["font"]
    c = (st.get("color") or "").lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", c):
        r.font.color.rgb = RGBColor.from_string(c.upper())
    hl = _HL.get(st.get("highlight", ""))
    if hl and hasattr(WD_COLOR_INDEX, hl):
        r.font.highlight_color = getattr(WD_COLOR_INDEX, hl)

def _blank_text(attrs):
    t = (attrs.get("type") or "underscore").lower()
    n = int(_num(attrs.get("length")) or 10)
    n = max(1, min(120, n))
    ch = {"dots": ".", "space": "\u00a0", "line": "_", "underscore": "_"}.get(t, "_")
    return ch * n


# ── inline flattening + emit ────────────────────────────────────────────
def _flatten_inline(nodes, style, out):
    for n in nodes:
        if isinstance(n, str):
            t = _XML_BAD_RE.sub("", n)
            if STRIP_LITERAL_BACKSLASH_N: t = t.replace("\\n", " ")
            t = re.sub(r"\s+", " ", t)
            if t: out.append(("text", t, style))
        elif n.tag == "STYLE":
            _flatten_inline(n.children, _style_from_attrs(n.attrs, style), out)
        elif n.tag in ("IMG", "FORMULA"):
            if n.attrs.get("bbox"): out.append(("img", n.attrs["bbox"], style))
        elif n.tag == "RIGHT":
            _t = "".join(c for c in n.children if isinstance(c, str)).strip()
            if _t: out.append(("right", _t, style))
        elif n.tag == "SPAN":
            _flatten_inline(n.children, style, out)
        elif n.tag == "BR":
            out.append(("br", None, style))
        elif n.tag == "BLANK":
            out.append(("blank", n.attrs, style))
        else:
            _flatten_inline(n.children, style, out)
    return out

def _trim_segments(segs):
    segs = list(segs)
    for i, (k, v, st) in enumerate(segs):
        if k != "text": continue
        if i == 0 or segs[i - 1][0] == "br":                 v = v.lstrip()
        if i == len(segs) - 1 or segs[i + 1][0] == "br":     v = v.rstrip()
        segs[i] = (k, v, st)
    return [s for s in segs if not (s[0] == "text" and not s[1])]

def _emit_segments(p, segs):
    for kind, val, st in segs:
        if kind == "br":
            p.add_run().add_break(); continue
        if kind == "img":
            _add_inline_img(p, val); continue
        if kind == "right":
            _add_right(p, val, st); continue
        r = p.add_run(_blank_text(val) if kind == "blank" else val)
        _apply_font(r)
        _apply_run_style(r, st)

def _apply_para_format(p, attrs, hanging=None, left_default=None):
    al = _ALIGN.get(str(attrs.get("align", "")).lower())
    if al is not None: p.alignment = al
    pf = p.paragraph_format
    li, fi = _num(attrs.get("indent_left")), _num(attrs.get("indent_first"))
    if li is None: li = left_default
    if fi is None: fi = hanging
    if li is not None: pf.left_indent = Inches(max(-3, min(8, li)))
    if fi is not None: pf.first_line_indent = Inches(max(-3, min(8, fi)))
    sb, sa, ls = _num(attrs.get("space_before")), _num(attrs.get("space_after")), _num(attrs.get("line_spacing"))
    if sb is not None: pf.space_before = Pt(max(0, min(72, sb)))
    if sa is not None: pf.space_after  = Pt(max(0, min(72, sa)))
    if ls is not None and 0.5 <= ls <= 3: pf.line_spacing = ls

def _heading_level(attrs):
    v = str(attrs.get("heading", "")).lower().lstrip("h")
    return int(v) if v in ("1", "2", "3") else None

def _spacer(docx):
    p = docx.add_paragraph("")
    pf = p.paragraph_format
    pf.space_before = Pt(0); pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(4)
    return p


# ── element renderers ──────────────────────────────────────────────────
def _render_p(docx, node, hanging=None, left_default=None):
    attrs = node.attrs
    segs = _trim_segments(_flatten_inline(node.children, _style_from_attrs(attrs, {}), []))
    if not segs: return None
    lvl = _heading_level(attrs)
    if lvl:
        p = docx.add_heading("", level=lvl)
        segs = [(k, v, {**{"color": "000000", "bold": False}, **st}) for k, v, st in segs]
    else:
        p = docx.add_paragraph()
    _apply_para_format(p, attrs, hanging, left_default)
    _emit_segments(p, segs)
    return p

class _CellSink:
    """Lets the block renderers write into a table cell with the calls they make on a Document (a heading becomes a plain paragraph)."""
    def __init__(self, cell): self._c = cell
    @property
    def paragraphs(self): return self._c.paragraphs
    def add_paragraph(self, text="", style=None): return self._c.add_paragraph(text, style)
    def add_table(self, rows, cols): return self._c.add_table(rows, cols)
    def add_heading(self, text="", level=1): return self._c.add_paragraph(text)

def _cell_is_complex(node, paras):
    """A cell with only paragraphs (or only inline text) keeps the exact original path. Options, a list, a table, a question wrapper,
    or text/tags beside paragraphs used to be silently dropped; those cells go through the general block renderer instead."""
    if any(isinstance(c, _Node) and c.tag in ("OPTIONS", "LIST", "TABLE", "QUESTION") for c in node.children):
        return True
    return bool(paras) and any((c.strip() if isinstance(c, str) else c.tag not in ("P", "ITEM", "OPTION")) for c in node.children)

def _cell_nodes(nodes):
    """Option lists inside a cell are written one option per paragraph (no table inside a cell), whatever their layout."""
    out = []
    for n in nodes:
        if isinstance(n, _Node) and n.tag == "OPTIONS":
            m = _Node("OPTIONS", {**n.attrs, "layout": "vertical"}); m.children = n.children; out.append(m)
        elif isinstance(n, _Node) and n.tag == "QUESTION":
            m = _Node("QUESTION", n.attrs); m.children = _cell_nodes(n.children); out.append(m)
        else:
            out.append(n)
    return out

def _fill_cell_blocks(cell, node):
    sink = _CellSink(cell)
    _render_nodes(sink, _cell_nodes(node.children), [])
    first = cell.paragraphs[0]
    if len(cell.paragraphs) > 1 and not first.text and not first._p.xpath(".//w:drawing | .//m:oMath"):
        first._element.getparent().remove(first._element)        # a new cell starts with one empty paragraph

def _fill_cell(cell, node, header=False):
    base = _style_from_attrs(node.attrs, {"bold": True} if header else {})
    paras = [c for c in node.children if isinstance(c, _Node) and c.tag in ("P", "ITEM", "OPTION")]
    if _cell_is_complex(node, paras):
        _fill_cell_blocks(cell, node)
        return
    if paras:
        for i, pn in enumerate(paras):
            p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
            segs = _trim_segments(_flatten_inline(pn.children, _style_from_attrs(pn.attrs, base), []))
            _apply_para_format(p, pn.attrs)
            _emit_segments(p, segs)
    else:
        p = cell.paragraphs[0]
        segs = _trim_segments(_flatten_inline(node.children, base, []))
        _apply_para_format(p, node.attrs)
        _emit_segments(p, segs)

def _render_table(docx, node, produced):
    rows = [c for c in node.children if isinstance(c, _Node) and c.tag == "ROW"]
    if not rows:
        _render_nodes(docx, node.children, produced); return
    grid = []
    for r in rows:
        cells = [c for c in r.children if isinstance(c, _Node) and c.tag == "CELL"]
        stray = "".join(c for c in r.children if isinstance(c, str)).strip()
        if stray:
            pseudo = _Node("CELL"); pseudo.children = [stray]; cells.append(pseudo)
        grid.append((r, cells))
    spans = [[max(1, min(12, int(_num(c.attrs.get("colspan")) or 1))) for c in cells] for _, cells in grid]
    ncols = max(1, max(sum(s) for s in spans))
    tbl = docx.add_table(rows=len(grid), cols=ncols)
    if str(node.attrs.get("border", "1")).lower() not in ("0", "false", "none", "no"):
        tbl.style = "Table Grid"
    ta = _TBL_ALIGN.get(str(node.attrs.get("align", "")).lower())
    if ta is not None: tbl.alignment = ta
    for ri, (row, cells) in enumerate(grid):
        header = _truthy(row.attrs.get("header", "false"))
        ci = 0
        for cn, span in zip(cells, spans[ri]):
            if ci >= ncols: break
            cell = tbl.cell(ri, ci)
            if span > 1:
                cell = cell.merge(tbl.cell(ri, min(ncols - 1, ci + span - 1)))
            _fill_cell(cell, cn, header)
            ci += span
    _spacer(docx); produced.append(len(docx.paragraphs) - 1)

def _render_options(docx, node, produced):
    ok_tags = ("OPTION", "P", "ITEM")
    clean = all((isinstance(c, _Node) and c.tag in ok_tags) or (isinstance(c, str) and not c.strip())
                for c in node.children)
    opts = [c for c in node.children if isinstance(c, _Node)]
    if not clean or not opts:
        _render_nodes(docx, node.children, produced); return
    layout = str(node.attrs.get("layout", "vertical")).lower()
    if layout in ("grid", "horizontal", "inline") and len(opts) > 1:
        default_cols = 2 if layout == "grid" else len(opts)
        ncols = max(1, min(6, int(_num(node.attrs.get("columns")) or default_cols)))
        nrows = math.ceil(len(opts) / ncols)
        tbl = docx.add_table(rows=nrows, cols=ncols)
        for i, o in enumerate(opts):
            _fill_cell(tbl.cell(i // ncols, i % ncols), o)
        _spacer(docx); produced.append(len(docx.paragraphs) - 1)
    else:
        for o in opts:
            if _render_p(docx, o, None, 0.4) is not None:
                produced.append(len(docx.paragraphs) - 1)

def _render_nodes(docx, nodes, produced):
    buf = []
    def flush():
        if not buf: return
        pseudo = _Node("P"); pseudo.children = list(buf); buf.clear()
        if _render_p(docx, pseudo) is not None:
            produced.append(len(docx.paragraphs) - 1)
    for n in nodes:
        if isinstance(n, str):
            flush()
            t = n.replace("\\n", "\n") if STRIP_LITERAL_BACKSLASH_N else n
            for line in t.split("\n"):
                if line.strip():
                    pseudo = _Node("P"); pseudo.children = [line]
                    if _render_p(docx, pseudo) is not None:
                        produced.append(len(docx.paragraphs) - 1)
            continue
        if n.tag in ("STYLE", "BR", "BLANK", "IMG", "FORMULA", "RIGHT", "SPAN"):   # inline tags at block top level (no <P>) must not be dropped
            buf.append(n); continue
        flush()
        if n.tag in ("P", "ITEM", "OPTION"):
            if _render_p(docx, n) is not None: produced.append(len(docx.paragraphs) - 1)
        elif n.tag == "LIST":
            ltype = str(n.attrs.get("type", "none")).lower()
            for ch in n.children:
                if isinstance(ch, _Node) and ch.tag in ("ITEM", "P"):
                    lvl = max(0, min(4, int(_num(ch.attrs.get("level")) or 0)))
                    ld, hg = (0.3 * lvl, None) if ltype == "none" else (0.3 * (lvl + 1), -0.25)
                    if _render_p(docx, ch, hg, ld) is not None:
                        produced.append(len(docx.paragraphs) - 1)
                else:
                    _render_nodes(docx, [ch], produced)
        elif n.tag == "OPTIONS":
            _render_options(docx, n, produced)
        elif n.tag == "TABLE":
            _render_table(docx, n, produced)
        else:
            _render_nodes(docx, n.children, produced)
    flush()

def _render_formatted_text(docx, text):
    produced = []
    try:
        _render_nodes(docx, _parse_markup(text).children, produced)
    except Exception as e:
        print(f"    ⚠ markup render failed ({type(e).__name__}: {str(e)[:120]}) — writing plain text")
        plain = _html.unescape(_TAG_RE.sub(" ", text))
        for line in plain.split("\n"):
            if line.strip():
                docx.add_paragraph(_XML_BAD_RE.sub("", line.strip()))
                produced.append(len(docx.paragraphs) - 1)
    return produced


# ── page break when a new paper starts: a heading block whose text repeats an earlier heading (e.g. the college title) ──
BREAK_ON_REPEATED_TITLE = True
def _title_key(markup):
    m = (markup or "").strip()
    if m[:2].upper() != "<P" or not re.search(r"heading\s*=", m[:90], re.I): return None
    t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", m))).strip().lower()
    t = "".join(ch for ch in t if ch.isalnum() or ch == " ")
    return t if len(t) >= 12 else None

# ── build the DOCX in reading order (= order of ordered_blocks) ─────────
def build_docx_with_blocks(blocks, out_path):
    docx = Document()
    docx.styles["Normal"].font.name = _FONT_NAME
    docx.styles["Normal"].font.size = Pt(11)

    block_meta, last_page, _seen_titles = {}, None, []
    for b in blocks:
        bid = b["block_id"]
        base = {"page": b["page"], "bbox_xyxy": b.get("bbox_xyxy"), "bodhan_type": b["bodhan_type"]}

        _broke = False
        if INSERT_PAGE_BREAKS and last_page is not None and b["page"] != last_page:
            docx.add_page_break(); _broke = True
        if BREAK_ON_REPEATED_TITLE and not b.get("is_picture"):
            _k = _title_key(b.get("formatted_text"))
            if _k:
                if any(difflib.SequenceMatcher(None, _k, s).ratio() >= 0.9 for s in _seen_titles):
                    if len(docx.paragraphs) > 0 and not _broke: docx.add_page_break()
                else: _seen_titles.append(_k)
        last_page = b["page"]; _CTXP["page"] = b["page"]

        if b.get("is_picture") and b.get("image_path"):
            try:
                docx.add_picture(b["image_path"], width=Inches(_pic_width_in(b)))
                idx = len(docx.paragraphs) - 1
                block_meta[bid] = {**base, "rendered": True, "docx_paragraph_range": [idx, idx]}
            except Exception as e:
                docx.add_paragraph(f"[picture failed: {e}]")
                block_meta[bid] = {**base, "rendered": False, "reason": f"picture_error: {e}",
                                   "docx_paragraph_range": None}
            continue

        text = (b.get("formatted_text") or "").strip()
        if not text:
            block_meta[bid] = {**base, "rendered": False, "reason": "empty_text_or_block",
                               "docx_paragraph_range": None}
            continue

        produced = _render_formatted_text(docx, text)
        if produced:
            block_meta[bid] = {**base, "rendered": True,
                               "docx_paragraph_range": [produced[0], produced[-1]]}
        else:
            block_meta[bid] = {**base, "rendered": False, "reason": "no_paragraphs_emitted",
                               "docx_paragraph_range": None}

    docx.save(str(out_path))
    return str(out_path), block_meta


# ── extras: inline images (<IMG bbox>), right-flush marks (<RIGHT>), pictures sized from their bbox ──
_CTXP = {"page": 1}
_USABLE_IN = 6.0                       # usable text width of the default python-docx page
_PAGE_IMG = {}
def _page_img(pno):
    if pno not in _PAGE_IMG: _PAGE_IMG.clear(); _PAGE_IMG[pno] = render_page(pno)   # keep only the current page (was: every page, unbounded RAM)
    return _PAGE_IMG[pno]

def _pic_width_in(b):
    bn = _bn(b)
    if not bn: return 5.5
    pw, ph = doc[b["page"] - 1].rect.width / 72, doc[b["page"] - 1].rect.height / 72
    w_phys, h_phys = max(1e-3, (bn[2] - bn[0]) * pw), max(1e-3, (bn[3] - bn[1]) * ph)       # physical size on the source page
    w = max(0.3, min(_USABLE_IN, w_phys))
    if w * (h_phys / w_phys) > 8.5: w = 8.5 * w_phys / h_phys                              # never taller than a page
    return max(0.3, w)

def _add_inline_img(p, bbox):
    try:
        x0, y0, x1, y1 = [float(v) for v in str(bbox).split(",")][:4]
        pno = _CTXP["page"] - 1; img = _page_img(pno); H, W = img.shape[:2]
        pad = 0.003; x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(1, x1 + pad), min(1, y1 + pad)
        if x1 <= x0 or y1 <= y0: return
        path = CROP_DIR / f"{stem}_p{pno+1}_inl_{hashlib.md5(str(bbox).encode()).hexdigest()[:8]}.png"
        cv2.imwrite(str(path), img[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)])
        p.add_run().add_picture(str(path), width=Inches(max(0.15, min(_USABLE_IN, (x1 - x0) * doc[pno].rect.width / 72))))
    except Exception:
        p.add_run("[formula]")

def _add_right(p, text, st):
    from docx.enum.text import WD_TAB_ALIGNMENT
    p.paragraph_format.tab_stops.add_tab_stop(Inches(_USABLE_IN), WD_TAB_ALIGNMENT.RIGHT)
    r = p.add_run("\t" + text); _apply_font(r); _apply_run_style(r, st)

