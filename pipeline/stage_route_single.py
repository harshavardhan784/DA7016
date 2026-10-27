# Stage file 'single': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── ROUTE 2: single Gemini call per page (OCR + order + tags) ─────────────
# Used for: digital non-English  and  scanned English/Hindi.   [review step plugs in after this later]
SINGLE_HEAD = """You are a document-reconstruction system. You get ONE page image. In a SINGLE pass: (1) transcribe every piece of text exactly as printed, in its original script(s) — never translate, transliterate, correct spelling or invent text; (2) decide the natural reading order (columns left to right, each top to bottom; full-width blocks between; a caption right after its figure; footnotes after body); (3) tag how each block looks, for a Word-document builder.

OUTPUT: ONLY a sequence of blocks in reading order, no preamble, no code fences:
<BLOCK id="b1" kind="text|title|header|list|table|caption|footnote|picture|formula" bbox="x0,y0,x1,y1">…tagged text…</BLOCK>
- bbox is normalised 0-1 from the top-left of the page image (needed for every block; essential for picture/formula).
- picture / figure / formula / signature / stamp blocks: leave the body EMPTY (<BLOCK id=… kind="picture" bbox=…></BLOCK>) — they are cropped from the image. Do not describe them.
- Escape & < > inside text as &amp; &lt; &gt;.
- PAGE FURNITURE (scanner watermarks like "Scanned by …", bare page numbers, "P.T.O.", running headers/footers repeated on every page) goes inside <DROP>…</DROP> within its own block. Mark only what you are sure is furniture.
- If the first block starts mid-sentence or the last block is cut off, keep it exactly as is: do not complete it, do not join it to anything.
- Merge lines of one paragraph into ONE block/<P>; use <BR/> only for intentional line breaks. Keep one block per paragraph / heading / list / table / question.
- FLOW CHECK (do this last): read your final order from first to last like a person reading the page. Blocks side by side on one row (e.g. "Time ..." and "Max marks ...") go left to right; a heading comes right before its content; numbered or lettered items (1, 2, 3 ... / (a), (b) ...) follow in sequence. If a block breaks this flow, fix the order (the rules above still win), then answer.

TAG GRAMMAR for the block body:
"""
SINGLE_TAIL = """
Order of tags inside a block is free but every block must contain only tags + the page's own text. Never output anything outside <BLOCK>…</BLOCK>.
EVERY element — including a <TABLE> and a table fragment at the very top of the page (a continued table starting with a repeated header row) — sits inside its own wrapper: <BLOCK id="b1" kind="table" bbox="x0,y0,x1,y1"><TABLE>…</TABLE></BLOCK>. Never start the reply with <TABLE> or <P>. bbox goes on the <BLOCK> only (normalised 0-1), never on <TABLE>."""
SINGLE_PROMPT = SINGLE_HEAD + TAG_GRAMMAR + SINGLE_TAIL

_BLOCK_RE = re.compile(r'<BLOCK\s+id="([^"]+)"([^>]*)>(.*?)</BLOCK>', re.S)
def _attr(s, k):
    m = re.search(r'(?<![\w-])' + k + r'\s*=\s*["\']([^"\']*)["\']', s or ""); return m.group(1) if m else None

# def _single_page(pno):
#     img = render_page(pno); H, W = img.shape[:2]
#     txt = _or_chat(M["single_call"], [{"type": "text", "text": SINGLE_PROMPT}, {"type": "image_url", "image_url": {"url": _img_uri(img, 1800)}}],
#                    timeout=300, max_tokens=16000)
#     out = []
#     for n, (bid, attrs, body) in enumerate(_BLOCK_RE.findall(txt), 1):
#         kind = (_attr(attrs, "kind") or "text").lower()
#         bb = _attr(attrs, "bbox")
#         try:
#             bn = [max(0.0, min(1.0, float(v))) for v in bb.split(",")][:4]
#             if len(bn) != 4 or bn[2] <= bn[0] or bn[3] <= bn[1]: bn = None
#         except Exception: bn = None
#         b_id = f"p{pno+1}_{re.sub(r'[^A-Za-z0-9_]', '', bid) or n}"
#         if kind in ("picture", "figure", "formula", "image") and bn:
#             path = CROP_DIR / f"{stem}_p{pno+1}_{b_id}.png"
#             x0, y0, x1, y1 = int(bn[0]*W), int(bn[1]*H), int(bn[2]*W), int(bn[3]*H)
#             cv2.imwrite(str(path), img[y0:y1, x0:x1])
#             out.append(_mkblock(b_id, pno + 1, bn, kind, "", str(path), (W, H)))
#         else:
#             out.append(_mkblock(b_id, pno + 1, bn, kind, _strip_drop(body), None, (W, H)))
#     if not out:                                                    # model ignored the format → keep raw text, strip tags
#         raw = re.sub(r"<[^>]+>", "", txt).strip()
#         out = [_mkblock(f"p{pno+1}_raw", pno + 1, None, "text", f"<P>{_x(raw)}</P>" if raw else "", None, (W, H))]
#         print(f"  page {pno+1}: no <BLOCK> output parsed → raw text kept")
#     return out
# ── preprocessing for retries (works on the already-rendered image, no re-render) ──
def _illumination_normalize(bgr, sigma=35):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    bg = cv2.GaussianBlur(gray, (0, 0), sigma)
    norm = cv2.divide(gray, bg, scale=192).clip(0, 255).astype(np.uint8)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lab[..., 0] = norm
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

def _preprocess_img(bgr, method="original"):
    if method == "original":
        return bgr
    if method == "gray":
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if method == "clahe_mild":
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    if method == "illum":
        return _illumination_normalize(bgr)
    raise ValueError(f"Unknown preprocessing method: {method}")

RETRY_METHODS = ["gray", "clahe_mild", "illum"]      # mild -> strong; stops at the first one that parses

def _ask_page(img):
    return _or_chat(M["single_call"], [{"type": "text", "text": SINGLE_PROMPT},
                                       {"type": "image_url", "image_url": {"url": _img_uri(img, 1800)}}],
                    timeout=300, max_tokens=16000)
_BLOCK_OPEN_RE = re.compile(r'<BLOCK\b([^>]*)>', re.I)
_KIND_FIX = {"p": "text", "para": "text", "paragraph": "text", "h": "header", "h1": "title", "h2": "header",
             "li": "list", "img": "picture", "image": "picture", "figure": "picture", "fig": "picture"}

def _split_blocks(txt):
    """Tolerant <BLOCK> splitter: a block ends at its </BLOCK> or at the next <BLOCK ...> (model sometimes forgets the closing tag)."""
    ms = list(_BLOCK_OPEN_RE.finditer(txt or ""))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(txt)
        attrs = m.group(1)
        body = "" if attrs.rstrip().endswith("/") else txt[m.end():end]          # <BLOCK .../> = empty block
        c = re.search(r"</BLOCK\s*>", body, re.I)
        if c: body = body[:c.start()]
        out.append((_attr(attrs, "id") or str(i + 1), attrs, body.strip()))
    return out

_BARE_RE = re.compile(r"<(TABLE|LIST|OPTIONS|QUESTION|P)\b([^>]*)>.*?</\1\s*>", re.S | re.I)
_KIND_OF = {"TABLE": "table", "LIST": "list", "OPTIONS": "list", "QUESTION": "text", "P": "text"}

def _clean_left(s):
    s = re.sub(r"<DROP>.*?</DROP>", "", s, flags=re.S | re.I)
    return re.sub(r"<[^>]+>|```\w*", "", s).strip()

def _split_bare(txt):
    """Reply had tags but no <BLOCK> wrappers: each top-level <TABLE>/<LIST>/<P>… becomes its own block, in order."""
    out, pos, n = [], 0, 0
    for m in _BARE_RE.finditer(txt or ""):
        lo = _clean_left(txt[pos:m.start()])
        if lo: n += 1; out.append((f"s{n}", 'kind="text"', f"<P>{_x(lo)}</P>"))
        n += 1; bb = _attr(m.group(2), "bbox")
        out.append((f"s{n}", f'kind="{_KIND_OF[m.group(1).upper()]}"' + (f' bbox="{bb}"' if bb else ""), m.group(0)))
        pos = m.end()
    lo = _clean_left((txt or "")[pos:])
    if lo: n += 1; out.append((f"s{n}", 'kind="text"', f"<P>{_x(lo)}</P>"))
    return out

def _norm_bbox(bb):
    """0-1 or 0-1000 (Gemini's native scale) -> normalised 0-1, or None."""
    try: v = [float(x) for x in (bb or "").split(",")][:4]
    except Exception: return None
    if len(v) != 4: return None
    s = 1000.0 if max(v) > 1.5 else 1.0
    v = [max(0.0, min(1.0, x / s)) for x in v]
    return v if v[2] > v[0] and v[3] > v[1] else None

def _parse_page_blocks(txt, pno, orig):
    """<BLOCK> text -> blocks. Crops always come from the ORIGINAL render (bbox is normalised, so it fits any preprocessed copy)."""
    H, W = orig.shape[:2]
    out = []
    blocks = _split_blocks(txt) or _split_bare(txt)
    for n, (bid, attrs, body) in enumerate(blocks, 1):
        kind = (_attr(attrs, "kind") or "text").lower()
        kind = _KIND_FIX.get(kind, kind)
        bn = _norm_bbox(_attr(attrs, "bbox"))
        b_id = f"p{pno+1}_{re.sub(r'[^A-Za-z0-9_]', '', bid) or n}"
        if kind in ("picture", "figure", "formula", "image") and bn:
            path = CROP_DIR / f"{stem}_p{pno+1}_{b_id}.png"
            x0, y0, x1, y1 = int(bn[0]*W), int(bn[1]*H), int(bn[2]*W), int(bn[3]*H)
            cv2.imwrite(str(path), orig[y0:y1, x0:x1])
            out.append(_mkblock(b_id, pno + 1, bn, kind, "", str(path), (W, H)))
        else:
            out.append(_mkblock(b_id, pno + 1, bn, kind, _strip_drop(body), None, (W, H)))
    return out

def _single_page(pno):
    orig = render_page(pno); H, W = orig.shape[:2]
    txt = _ask_page(orig)
    out = _parse_page_blocks(txt, pno, orig)
    if not out:
        print(f"  page {pno+1}: no <BLOCK> parsed (reply starts: {txt.strip()[:120]!r}) → retrying on preprocessed copies")
        for method in RETRY_METHODS:
            try:
                prep = _preprocess_img(orig, method)
                cv2.imwrite(str(PAGE_DIR / f"{stem}_p{pno+1}_{method}.png"), prep)       # keep what the model saw
                txt2 = _ask_page(prep)
            except Exception as e:
                print(f"    page {pno+1} [{method}] failed: {type(e).__name__}: {str(e)[:100]}"); continue
            out = _parse_page_blocks(txt2, pno, orig)
            if out:
                print(f"  page {pno+1}: recovered with '{method}' ({len(out)} blocks)"); break
            print(f"    page {pno+1} [{method}]: still no <BLOCK>")
    if not out:                                                    # every attempt failed → keep raw text, strip tags (as before)
        raw = re.sub(r"<[^>]+>", "", txt).strip()
        out = [_mkblock(f"p{pno+1}_raw", pno + 1, None, "text", f"<P>{_x(raw)}</P>" if raw else "", None, (W, H))]
        print(f"  page {pno+1}: no <BLOCK> output parsed after all retries → raw text kept")
    return out
def run_single_gemini():
    set_stage("single", "page_calls")
    def safe(p):
        try: return _single_page(p)
        except Exception as e:
            print(f"  page {p+1} FAILED: {type(e).__name__}: {str(e)[:150]}")
            return [_mkblock(f"p{p+1}_failed", p + 1, None, "text", "", None)]
    with ThreadPoolExecutor(ORDER_WORKERS) as ex:
        per_page = list(ex.map(safe, page_indices))
    set_stage("-", "-")
    for p, bl in zip(page_indices, per_page): print(f"  page {p+1}: {len(bl)} blocks")
    return [b for bl in per_page for b in bl]

if ROUTE == "single_gemini":
    ordered_blocks = run_single_gemini()
    print(f"single_gemini: {len(ordered_blocks)} blocks")
else:
    print("skip single_gemini")

