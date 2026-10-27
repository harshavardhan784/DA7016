# Stage file 'fixers': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── CELL: continuity / marks fixes shared by every route (run on the final block list) ──────────
_TAG_STRIP = re.compile(r"<[^>]+>")
def _plain(m): return re.sub(r"\s+", " ", _html.unescape(_TAG_STRIP.sub("", m or ""))).strip()
_NOT_BODY = {"footnote", "caption", "page_header", "page_footer", "side_header", "header", "running_header", "title",
             "section_header", "sectionheader", "table", "picture", "formula", "figure", "pageheader", "pagefooter"}
def _textual(b):
    m = (b.get("formatted_text") or "").strip()
    if b.get("is_picture") or m[:2].upper() != "<P": return False
    if re.search(r"<(TABLE|LIST|OPTIONS|QUESTION)", m, re.I) or re.search(r"heading\s*=", m[:90], re.I): return False
    return str(b.get("bodhan_type", "")).lower().replace("-", "_") not in _NOT_BODY
_TERM_CH = "….!?:;।॥\"'”’)]}»"
def _cut_text(t):
    t = (t or "").rstrip()
    if not t: return False
    if t[-1] in "-‐‑" and len(t) > 1 and t[-2].isalpha(): return True
    return t[-1] not in _TERM_CH and (t[-1].isalnum() or t[-1] == ",")
def _lower_start(t):
    for ch in (t or ""):
        if ch.isalpha(): return ch.islower()
        if not (ch.isspace() or ch in "“\"'(["): return False
    return False
def _bn(b):
    if b.get("bbox_norm"): return b["bbox_norm"]
    bb = b.get("bbox_xyxy")
    if not bb: return None
    W, H = doc[b["page"] - 1].rect.width * DPI / 72, doc[b["page"] - 1].rect.height * DPI / 72
    return [bb[0] / W, bb[1] / H, bb[2] / W, bb[3] / H]

def _merge_pages_text(blocks, log):
    """A page-final paragraph that is cut off + a page-first paragraph that continues it -> ONE paragraph at A's place.
    Figures/captions that sat above the continuation come after the completed paragraph; hyphenated words are re-joined."""
    pages = sorted({b["page"] for b in blocks}); byp = {p: [b for b in blocks if b["page"] == p] for p in pages}
    for p, q in zip(pages, pages[1:]):
        if q != p + 1: continue
        A = next((b for b in reversed(byp[p]) if _textual(b)), None)
        B = next((b for b in byp[q] if _textual(b)), None)
        if not A or not B: continue
        if "<RIGHT>" in A["formatted_text"]: continue                 # a marks token ends the question: not cut off
        if not (_cut_text(_plain(A["formatted_text"])) and _lower_start(_plain(B["formatted_text"]))): continue
        ma = re.match(r"(?is)(.*)</P>\s*$", A["formatted_text"].strip())
        mb = re.match(r"(?is)\s*<P[^>]*>(.*?)</P>(.*)$", B["formatted_text"].strip())
        if not ma or not mb: continue
        ai, bi = ma.group(1).rstrip(), mb.group(1).lstrip()
        joined = (ai[:-1] + bi) if re.search(r"[-‐‑]$", ai) else (ai + " " + bi)
        A["formatted_text"] = joined + "</P>"; B["formatted_text"] = mb.group(2).strip()
        log.append(f"p{p}->p{q}: joined cut-off paragraph {A['block_id']} + {B['block_id']}")

def _merge_tables(blocks, log):
    """Last block of page N and first block of page N+1 are tables with the same column count and a repeated header row -> one table."""
    pages = sorted({b["page"] for b in blocks}); byp = {p: [b for b in blocks if b["page"] == p] for p in pages}
    rows = lambda m: re.findall(r"(?is)<ROW[^>]*>.*?</ROW>", m)
    live = lambda b: (b.get("formatted_text") or "").strip() or b.get("is_picture")
    for p, q in zip(pages, pages[1:]):
        if q != p + 1: continue
        A = next((b for b in reversed(byp[p]) if live(b)), None); B = next((b for b in byp[q] if live(b)), None)
        if not A or not B or A.get("is_picture") or B.get("is_picture"): continue
        am, bm = A["formatted_text"].strip(), B["formatted_text"].strip()
        if not (am[:6].upper() == "<TABLE" and bm[:6].upper() == "<TABLE"): continue
        ra, rb = rows(am), rows(bm)
        if not ra or not rb or am.upper().count("<TABLE") != 1 or bm.upper().count("<TABLE") != 1: continue
        if len(re.findall(r"(?i)<CELL", ra[0])) != len(re.findall(r"(?i)<CELL", rb[0])): continue
        if _plain(ra[0]) != _plain(rb[0]): continue
        A["formatted_text"] = re.sub(r"(?i)</TABLE>\s*$", "".join(rb[1:]) + "</TABLE>", am); B["formatted_text"] = ""
        log.append(f"p{p}->p{q}: merged table {A['block_id']} + {B['block_id']} (repeated header dropped)")

_MARKS = re.compile(r"^[\[\(]?\d{1,3}(?:\s*[+x×\-–/]\s*\d{1,3})*\s*(?:[Mm]arks?|[Mm])?[\]\)]?$")
_NEWITEM = re.compile(r"^\s*(\(?\d{1,3}\s*[\.\)]|\(?[a-zA-Z]\)|\(?[ivxIVX]{1,4}[\.\)]|[•▪\-–])")
def _attach_marks(blocks, log):
    """A bare marks token at the far right margin (8, 1+7, [5]) is the END of the question beside it, not a line of text:
    it becomes <RIGHT>..</RIGHT> (flush right) at the end of that question's paragraph."""
    byp = defaultdict(list)
    for b in blocks: byp[b["page"]].append(b)
    for p, L in byp.items():
        cands = []
        for b in L:
            if not (b.get("formatted_text") or "").strip() or b.get("is_picture"): continue
            t, bn = _plain(b["formatted_text"]), _bn(b)
            if bn and _MARKS.match(t) and bn[0] > 0.72 and (bn[2] - bn[0]) < 0.15 and 0.04 < (bn[1] + bn[3]) / 2 < 0.95: cands.append((b, t, bn))
        if not cands or len(cands) > 8: continue                       # many right-margin numbers = line numbers etc.: leave alone
        ids = {id(c[0]) for c in cands}
        for m, t, bn in cands:
            yc = (bn[1] + bn[3]) / 2; best = None
            for i, h in enumerate(L):
                if id(h) in ids or not _textual(h): continue
                hb = _bn(h)
                if not hb or hb[2] > bn[0] + 0.02 or hb[0] > bn[0] - 0.1: continue
                if hb[1] - 0.012 <= yc <= hb[3] + 0.012:
                    d = abs((hb[1] + hb[3]) / 2 - yc)
                    if best is None or d < best[0]: best = (d, i)
            if not best: continue
            i = best[1]; end = i; hb = _bn(L[i]); prev_y = hb[3]
            for j in range(i + 1, len(L)):
                n = L[j]
                if id(n) in ids or not (n.get("formatted_text") or "").strip():
                    continue
                nb = _bn(n)
                if not _textual(n) or not nb or nb[0] < hb[0] - 0.03 or nb[1] - prev_y > 0.035 or _NEWITEM.match(_plain(n["formatted_text"])): break
                end = j; prev_y = nb[3]
            E = L[end]; em = E["formatted_text"].rstrip()
            if not em.upper().endswith("</P>"): continue
            E["formatted_text"] = em[:-4] + f"<RIGHT>{_x(t)}</RIGHT></P>"; m["formatted_text"] = ""
            log.append(f"p{p}: marks '{t}' attached to end of {E['block_id']}")

# ─── added: dedup of overlapping OCR blocks + "n x m = k" marks lines ─────────────────────────────
_DEDUP_MIN = 8
_MARKS_EQ = re.compile(r"^\(?\s*\d{1,3}\s*[x*]\s*\d{1,3}\s*=\s*\d{1,3}\s*\)?\.?$")
_ANSWER_RE = re.compile(r"(?i)\b(answer|attempt)\b")
def _is_marks_eq(t):
    s = re.sub(r"\\(?:times|cdot)|×", "x", t or "")
    return bool(_MARKS_EQ.match(s.replace("$", "").replace("{", "").replace("}", "").strip().lower()))
def _dd_norm(m):
    t = _html.unescape(_TAG_STRIP.sub("", m or "")); t = re.sub(r"\\[A-Za-z]+", "", t)
    return "".join(ch for ch in t.lower() if ch.isalnum())
def _ov_frac(a, b):                                   # share of the smaller box covered by the other
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0: return 0.0
    sm = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (x1 - x0) * (y1 - y0) / sm if sm > 0 else 0.0
def _alive(b): return bool((b.get("formatted_text") or "").strip()) and not b.get("is_picture")

def _dedup_blocks(blocks, log):
    """Two overlapping blocks on one page where one text is contained in the other (heading + the same line incl. its marks,
    a truncated fragment + the full text, exact repeats) -> one block. A marks-like tail ('$8 \\times 2 = 16$') becomes <RIGHT> on the shorter block."""
    byp = defaultdict(list)
    for b in blocks:
        if _alive(b) and not re.search(r"(?i)<TABLE", b["formatted_text"]): byp[b["page"]].append(b)
    for p, L in byp.items():
        for i, A in enumerate(L):
            for B in L[i + 1:i + 4]:
                if not (_alive(A) and _alive(B)): continue
                na, nb_ = _dd_norm(A["formatted_text"]), _dd_norm(B["formatted_text"])
                if len(na) < _DEDUP_MIN or len(nb_) < _DEDUP_MIN: continue
                S, G, ns, ng = (A, B, na, nb_) if len(na) <= len(nb_) else (B, A, nb_, na)
                if ns not in ng: continue
                bs, bg = _bn(S), _bn(G)
                if not bs or not bg or _ov_frac(bs, bg) < 0.5: continue
                if ns == ng:                                                     # exact repeat: keep the earlier one
                    B["formatted_text"] = ""; log.append(f"p{p}: duplicate {B['block_id']} dropped (same as {A['block_id']})"); continue
                words = _plain(S["formatted_text"]).split(); gt = _plain(G["formatted_text"])
                m = re.match(r"\s*" + r"\s*".join(re.escape(w) for w in words), gt) if words else None
                tail = gt[m.end():].strip() if m else ""
                tl = tail.replace("$", "").strip()
                se = S["formatted_text"].rstrip()
                if tail and len(tl) <= 24 and (_is_marks_eq(tail) or _MARKS.match(tl)) and se.upper().endswith("</P>") and "<RIGHT>" not in se.upper():
                    S["formatted_text"] = se[:-4] + f"<RIGHT>{_x(tail)}</RIGHT></P>"; G["formatted_text"] = ""
                    log.append(f"p{p}: {G['block_id']} was {S['block_id']} + marks '{tail}' -> merged")
                else:
                    S["formatted_text"] = ""; log.append(f"p{p}: {S['block_id']} is a fragment of {G['block_id']} -> dropped")

def _attach_score_lines(blocks, log):
    """A lone '4 X 2 = 8' / '$$2\\times5=10$$' line is the marks of the 'Answer any …' line beside it: <RIGHT> at its end."""
    byp = defaultdict(list)
    for b in blocks: byp[b["page"]].append(b)
    for p, L in byp.items():
        for k, m in enumerate(L):
            if not _alive(m): continue
            t = _plain(m["formatted_text"])
            if not _is_marks_eq(t): continue
            tgt, mb = None, _bn(m)
            if mb:
                yc, best = (mb[1] + mb[3]) / 2, None
                for h in L:
                    if h is m or not _alive(h): continue
                    hb = _bn(h)
                    if not hb or hb[0] > mb[0] - 0.05: continue                  # must start left of the marks
                    if hb[1] - 0.012 <= yc <= hb[3] + 0.012:
                        d = abs((hb[1] + hb[3]) / 2 - yc)
                        if best is None or d < best[0]: best = (d, h)
                tgt = best[1] if best else None
            if tgt is None:                                                      # neighbour fallback: next, then previous block
                nxt = next((h for h in L[k + 1:] if _alive(h)), None); prv = next((h for h in reversed(L[:k]) if _alive(h)), None)
                tgt = next((h for h in (nxt, prv) if h is not None and _ANSWER_RE.search(_plain(h["formatted_text"])[:140])), None)
            if tgt is None: continue
            em = tgt["formatted_text"].rstrip()
            if not em.upper().endswith("</P>") or "<RIGHT>" in em.upper(): continue
            tgt["formatted_text"] = em[:-4] + f"<RIGHT>{_x(t)}</RIGHT></P>"; m["formatted_text"] = ""
            log.append(f"p{p}: marks line '{t}' attached to {tgt['block_id']}")

# def fix_blocks(blocks):
#     log = []
#     _dedup_blocks(blocks, log); _attach_score_lines(blocks, log)
#     _attach_marks(blocks, log); _merge_pages_text(blocks, log); _merge_tables(blocks, log)
#     for l in log: print("  fix:", l)
#     return blocks

# ── soft-wrap joiner: OCR line breaks that only wrap ONE sentence become a space (deterministic, all routes) ──
_BR_SPLIT = re.compile(r"\s*<BR\s*/?>\s*", re.I)
_PARA_RE  = re.compile(r"(<(P|ITEM|OPTION)\b[^>]*>)(.*?)(</\2\s*>)", re.S | re.I)
_SENT_END = re.compile(r"[.?!:;।॥”\"]\s*$")
_LABEL_AT = re.compile(r"^\s*(?:\(\S{1,2}\)|\S{1,3}[.)](?=\s)|\d{1,3}[.)](?=\D)|[•·◦▪➤*–—-]\s)")
def _soft_join(prev, nxt):
    p, n = _plain(prev), _plain(nxt)
    if not p or not n or _SENT_END.search(p) or _LABEL_AT.match(n) or p.isupper(): return False
    return not (n[0].isupper() and len(p) < 40)          # a capital after a SHORT line = probably a new line of its own
def _join_soft_wraps(markup):
    def _para(m):
        parts = _BR_SPLIT.split(m.group(3))
        if len(parts) < 2: return m.group(0)
        out = parts[0]
        for prev, nxt in zip(parts, parts[1:]):
            if _soft_join(prev, nxt): out += ("" if _plain(prev).endswith("-") else " ") + nxt
            else:                     out += "<BR/>" + nxt
        return m.group(1) + out + m.group(4)
    return "".join(piece if piece[:6].upper() == "<TABLE" else _PARA_RE.sub(_para, piece)
                   for piece in re.split(r"(?is)(<TABLE.*?</TABLE>)", markup or ""))
def fix_blocks(blocks):
    log = []
    _attach_marks(blocks, log); _merge_pages_text(blocks, log); _merge_tables(blocks, log)
    n = 0
    for b in blocks:
        mk = b.get("formatted_text") or ""
        if "<BR" in mk.upper():
            new = _join_soft_wraps(mk)
            if new != mk: b["formatted_text"] = new; n += 1
    if n: log.append(f"joined soft-wrapped lines in {n} block(s)")
    for l in log: print("  fix:", l)
    return blocks
