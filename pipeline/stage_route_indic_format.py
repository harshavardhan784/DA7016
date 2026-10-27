# Stage file 'indic_fmt': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
if ROUTE == "scanned_indic":
    # ─── CELL 5: Stage 2 — one combined call per chunk ────────────────────────

    import json
    (METHOD_DIR / f"{stem}__combined_cache.json").unlink(missing_ok=True)   # fresh run each time

    # ── knobs ──────────────────────────────────────────────────────────────
    HTTP_ATTEMPTS             = 5       # per API call, exponential backoff on network/429/5xx
    CALL_DEADLINE             = 300     # 120: too tight if calls take 2-4 min. Lower it only after measuring.
    MAX_SPLIT_DEPTH           = 3       # on chunk reject: halve, halve, single, single@temp0.3, RAW
    NET_FAIL_BREAKER          = 3       # stop after N consecutive network-failed chunks
    ABORT_IF_NO_DNS           = True
    SHOW_REJECT_DEBUG         = True
    STRIP_LITERAL_BACKSLASH_N = True
    INSERT_PAGE_BREAKS        = False
    USE_CACHE                 = True

    # Fidelity (chunk-level, multiset / character-count because output is reordered).
    # Drop budget is intentionally larger here than in the previous notebook — the
    # whole point of this stage is to allow controlled word/character-level dedup.
    FID_DROP_TOL       = 0.10    # up to 10% of chunk chars may be dropped ...
    FID_DROP_MIN_CHARS = 15      # ... but always allow this many
    FID_ADD_TOL        = 0.05    # up to 5% may be added (bumped from 3% so one
                                 # mis-cased letter or tag-attribute spelling
                                 # doesn't cause 4 cascading retries)
    FID_ADD_MIN_CHARS  = 25      # ... with a floor of 25 chars so very small
                                 # chunks (short page headers etc.) don't trip
                                 # on a single stray character
    FID_IGNORE_CHARS   = "_$"    # ignored in comparison: blank runs, LaTeX $ delimiters


    # ═════════════════════════════════════════════════════════════════════════
    # THE PROMPT — generalisable; no domain examples, no exam/physics content
    # ═════════════════════════════════════════════════════════════════════════
    COMBINED_PROMPT = r"""
You are a document-reconstruction system. Your input is a chunk of a scanned document after an OCR step, together with the image(s) of the page(s) the chunk came from. In ONE pass you must do THREE jobs, in this order:

(A) WITHIN-BLOCK CLEANUP — verified against the image.
(B) ORDER — already decided and verified upstream. Emit the blocks in EXACTLY the order given.
(C) FORMATTING TAGS — tags that describe how each block looks on the page.

A later program consumes your tagged output and builds a Word document. Never say anything outside the <BLOCK>…</BLOCK> wrappers: no preamble, no prose, no code fences.


================================================================
INPUT
================================================================
Each block arrives as

  <BLOCK id="…" type="…" page="N" bbox="x0,y0,x1,y1">
  …OCR text of this block…
  </BLOCK>

- id    is a stable identifier; preserve it exactly in your output.
- type  is a hint from an upstream layout model and is often wrong; trust what you SEE in the image when they disagree.
- page  is the 1-indexed page the block was detected on.
- bbox  is [x0, y0, x1, y1] normalised to [0,1] over the page image. Use it to locate each block's region.
- The text is the OCR of this block. The characters & < > inside text appear as &amp; &lt; &gt;; keep them that way in your output.

Blocks arrive in their FINAL reading order. That order was decided upstream from the layout and checked against the page image. Emit the <BLOCK> wrappers in exactly the order you received them. Never reorder, never move a block above or below its neighbours, never remove a block. Ordering is NOT your job.

Along with the blocks you receive the image of pages the chunk spans. Use the image for verification and layout only — do NOT read any text from the image into your output.


================================================================
(A) WITHIN-BLOCK CLEANUP
================================================================
DEFAULT: keep every character of every block exactly as it is in the OCR.

You MAY remove characters or words from inside a block ONLY when ALL of the following hold:

  (a) The removed text is REDUNDANT in one of these two patterns, and only these two:
        - echo / stutter: the same short run appears twice back-to-back inside the block (e.g. a word, number or short phrase repeated with no intervening content);
        - overlap duplicate: another block in the SAME chunk has an overlapping bbox and already contains the same words at that visual position, so one of the two is a fragment duplicated by the upstream layout model.

  (b) The IMAGE, inspected at this block's bbox, visibly shows the surviving text ONCE and does not show the removed copy as a separate second occurrence. Count the occurrences in the image region before removing anything.

  (c) After removal, every meaningful word that the image shows for this block is still present. You never shorten or summarise content that the image genuinely contains.

If any of (a)(b)(c) is uncertain, KEEP every character. When in doubt — KEEP. Deletion is not easily allowed until it verifies with surrounding blocks and the image.

A word is not redundant just because it is short, sits on its own line, or doesn't fit the pattern of its neighbours. The overlap-duplicate rule requires the same word to appear VISIBLY TWICE in the image at bbox-overlapping regions. If you cannot see the word twice, keep it, even if it looks orphaned.

You MAY use a block's neighbours in the same chunk as context for your dedup decisions. Before deciding whether a word in block X is a redundant echo of block Y, read every block in this chunk. That is why the chunk is given to you together.

WHOLE-BLOCK EMPTYING (returning <BLOCK id="…"></BLOCK> with no content) is extremely rare and allowed ONLY when the entire block's words are a verified bbox-overlapping duplicate of another block in the SAME chunk, confirmed from the image. 

You MAY NEVER:
  - Rewrite, paraphrase, correct spelling, translate, transliterate, or complete truncated text.
  - Add any word, number, label, caption, title, heading or row that is not in the OCR of that block.
  - Transcribe text from the image into an empty block. If a block's OCR text is empty, your output for it is <BLOCK id="…"></BLOCK> — even when the image at that bbox shows real content. The image is for formatting and ordering decisions only; words in the OCR are the only source of content, in every block.
  - Move text from one block to another. Each block's output may contain only words from that block.
  - Change a block's id, add a new id, or omit a block (except for the rare verified emptying above).

Never remove text just because it is repeated. Always verify suspected duplicates against the page image and their bounding boxes first. Remove a repeated occurrence only when there is sufficient evidence that it is a duplicate — for example, when the bounding boxes overlap or are identical and the text represents the same location in the image. If the bounding boxes are identical or cannot reliably distinguish the occurrences, also consider the distance between the current and previous occurrences. Preserve legitimate repetitions, even when the same text appears multiple times in the document.

What you CAN add freely is TAGS. Tags are not content. Alignment, size, bold / italic / underline, heading levels, lists, options, tables, blanks, line breaks — all of these are instructions to a Word-document builder about how the OCR text should look. Add every tag the image evidence supports; don't add any character of text that isn't already in the block.


================================================================
(B) ORDER + PAGE FURNITURE
================================================================
ORDER: blocks arrive already in final reading order. Emit exactly one wrapper per input block, in exactly that order. Never omit a wrapper.

PAGE FURNITURE: scanner watermarks ("Scanned by …", "Scanned with …"), bare page numbers, page-turn hints ("[P.T.O.]", "Please Turn Over", "Continued on next page"), and a header/footer repeated identically on every page are NOT body content. Decide this from the block's TEXT and from the IMAGE at its bbox — the layout `type` label is only a weak hint and is often wrong. A block may be entirely furniture, entirely body, or MIXED: body text with a furniture fragment glued to it (at the start, end, or middle).
Wrap each furniture fragment in <DROP>…</DROP>, inside its own block, leaving the body text outside it exactly as in the OCR. Syntax examples:
  mixed block   : <P>real body text …</P><DROP>Scanned by …</DROP>
  all furniture : <DROP>754</DROP>
The wrapper is still emitted; only the fragment is marked. The downstream program deletes everything inside <DROP> and keeps a log, so mark only text you are sure is furniture. Being short, orphaned, a number, or on its own line is NOT enough — a number that is part of the content (a list label, a year, a marks/hours value) is body. If unsure, do NOT mark it.

Picture / Figure / Chart / Diagram blocks have no OCR text; emit <BLOCK id="…"></BLOCK> for them.


================================================================
(C) FORMATTING TAGS
================================================================
Return each block as <BLOCK id="…"> …tags… </BLOCK> (copy the id exactly; type and page are no longer needed on the wrapper). Exactly one wrapper per input block. No merging, splitting, or moving text between blocks. Output ONLY the wrappers: no preamble, no comments, no code fences.

Units: font size in points; indents in inches; spacing in points. Leave an attribute out when it is the default or when you cannot judge it.

----------------------------------------------------------------
<P …>text</P>
----------------------------------------------------------------
Every paragraph is a P. Attributes:
  align         left | center | right | justify   (default left). ALWAYS set align when visibly centered, right-aligned or justified.
  size          font size in points. Ordinary body text ≈ 11. Estimate by comparing letter height with ordinary body text on the SAME page (main title ≈ 16–22, section header ≈ 13–15, body ≈ 11, small print / notes ≈ 8–10). Leave out for body size.
  bold | italic | underline   "true" only when the WHOLE paragraph is visibly so.
  font          only if clearly identifiable (e.g. "Times New Roman", "Arial"); never for Indic scripts.
  color         "#RRGGBB" only for clearly coloured text.
  heading       1 | 2 | 3 for the document / institution title, section headers, and small sub-labels: a line that is visibly larger, bolder or centered and stands apart from body text.
  indent_left, indent_first (negative = hanging), space_before, space_after, line_spacing (1.0 = single): only when clearly different from normal.

----------------------------------------------------------------
<STYLE bold= italic= underline= strike= size= font= color="#RRGGBB" highlight= superscript= subscript=>text</STYLE>
----------------------------------------------------------------
Inline character formatting for PART of a paragraph. Use the smallest span.

----------------------------------------------------------------
<BR/>
----------------------------------------------------------------
Intentional line break inside a paragraph.

----------------------------------------------------------------
<BLANK type="underscore|dots|line|space" length="N"/>
----------------------------------------------------------------
A fill-in-the-blank run, at exactly the same spot. length = the number of characters in the OCR run (count them). Use ONLY where the OCR contains such a run (3+ underscores / dots / dashes). Never add a blank the OCR text does not have; never fill a blank with an answer.

----------------------------------------------------------------
<LIST type="bullet|number|alpha|roman|none"><ITEM level="0">…</ITEM></LIST>
----------------------------------------------------------------
Use <LIST> only when the OCR text shows a vertical column of items with a leading marker on each item.

CRITICAL — label preservation: inside each <ITEM>, the first characters of the OCR line are the item's label, and they are CONTENT, not formatting. You MUST copy them verbatim into the <ITEM> text, together with the punctuation and the single space that follows. The downstream builder does NOT auto-number list items; whatever label is missing from your <ITEM> text is missing from the final document.

What counts as a label (script-independent): any short run of 1–3 characters at the start of the OCR line, followed by a punctuation mark (one of ".", ")", "(", "-", ":", "/", "]", "}") and whitespace, AND the same shape repeats on the next sibling items in the list. Script does not matter — the label can be a digit, a Latin letter, a roman numeral, a single letter or ligature of any Indic / CJK / Cyrillic / Arabic script, a circled number, or a pure bullet ("•", "·", "-", "*", "–", "—", "◦", "▪", "➤"). Keep the entire label — its character(s), its punctuation and the space after it — verbatim in the <ITEM> text.

If a line in the OCR has no leading marker and you cannot be certain the group is a list, emit <P> for each line instead of <LIST>. Exception (soft wrap): a line that only continues the previous line's unfinished sentence (no label or bullet of its own, and the previous line does not end in . ? ! : । ॥) stays in the SAME <P>/<ITEM>, joined with a single space — not a new paragraph, not <BR/>. This applies only to lines inside one block; blocks are never joined. Faithful content always beats a prettier layout.

----------------------------------------------------------------
<QUESTION> … </QUESTION>   optional wrapper around a numbered item; a P for the stem, then
<OPTIONS layout="vertical|grid" columns="2"><OPTION>(a) …</OPTION> …</OPTIONS>
----------------------------------------------------------------
Same label-preservation rule as <LIST>: the option marker is CONTENT, whatever script it is in. Copy it verbatim into the <OPTION> text with its following punctuation and space. Keep every option in the original order. vertical = one option per line. grid = options arranged in N columns on the page.

----------------------------------------------------------------
<TABLE border="1" align="left|center|right"><ROW header="true"><CELL align= bold= size= colspan="2">text</CELL></ROW></TABLE>
----------------------------------------------------------------
CRITICAL TABLE RULE: You MUST convert all tabular data (whether in Markdown format with `|` pipes or HTML markup) into this exact XML structure. 
- NEVER output raw Markdown pipe (`|`) characters in the final text.
- Always include border="1" so the downstream document builder applies proper grid lining.
- Keep cell text exactly as in the OCR. Ensure every row covers the same number of columns by using the colspan attribute where necessary.

----------------------------------------------------------------
Symbols
----------------------------------------------------------------
Keep all symbols, maths and Unicode exactly. Use <STYLE superscript=…> and <STYLE subscript=…> for raised / lowered characters. Never replace a formula by words.

----------------------------------------------------------------
Decision rules
----------------------------------------------------------------
  - Compare each block's text with its region of the image. Ordinary body text stays a plain <P> with no attributes.
  - A short or capitalised line is not automatically a heading; a question-style number does not make a line bold. Treat similar elements consistently within the chunk.
  - If a sentence appears to continue from one block or page into the next, leave it as is: do not join blocks and do not complete the sentence.
  - If you cannot judge a property, leave it out. Faithful content and faithful order always beat a prettier layout.


================================================================
BEFORE YOU EMIT — SELF-CHECK
================================================================
Do these four checks silently; emit the output only after all four pass.

  (1) Does every input id appear exactly once in your output? (Rare verified empties under rule (A) are still present as empty wrappers.)
  
  (2) Is the output order IDENTICAL to the input order? If not, restore the input order.

  (3) Did any deletion you performed meet rules A(a), A(b), A(c)? If any was uncertain w.r.to image, roll it back and keep the text.

  (4) Did you add any word, number or label that is not in the OCR of that block? If yes, remove it, i.e No additions.

  (5) For every <ITEM> and <OPTION> you emitted: does its text begin with the exact leading label (marker + punctuation + space) that the OCR line had, in whatever script? If the <ITEM> or <OPTION> text starts a word later than the OCR line did, the label was stripped — put it back.

  (6) You don't have to deal with spelling mistakes, just use ocr text as it is. do not correct spelling mistakes.
  
================================================================
EXAMPLE (dummy content — shows the output SYNTAX only, not domain content)
================================================================
Input (detection order; three dummy blocks):

<BLOCK id="x2" type="Text" page="1" bbox="0.08,0.42,0.92,0.52">option alpha  option beta  option gamma  option delta</BLOCK>
<BLOCK id="x0" type="Text" page="1" bbox="0.08,0.04,0.92,0.11">LOREM IPSUM CENTRE</BLOCK>
<BLOCK id="x1" type="SectionHeader" page="1" bbox="0.08,0.30,0.92,0.36">Section One</BLOCK>

Output (reading order; label before its content; nothing invented):

<BLOCK id="x0">
<P align="center" heading="1" size="18" bold="true">LOREM IPSUM CENTRE</P>
</BLOCK>
<BLOCK id="x1">
<P align="left" heading="2" size="13" bold="true">Section One</P>
</BLOCK>
<BLOCK id="x2">
<OPTIONS layout="grid" columns="2">
<OPTION>option alpha</OPTION>
<OPTION>option beta</OPTION>
<OPTION>option gamma</OPTION>
<OPTION>option delta</OPTION>
</OPTIONS>
</BLOCK>


Now process the chunk below.

INPUT:"""

    _PROMPT_HASH = hashlib.sha1(COMBINED_PROMPT.encode("utf-8")).hexdigest()[:12]


    # ═════════════════════════════════════════════════════════════════════════
    # PAGE ORDER — one compact call per page + downstream validation
    # ═════════════════════════════════════════════════════════════════════════
    ORDER_MODEL        = (lambda m: m if isinstance(m, str) else m[0])(globals().get("order_model", COMBINED_MODELS))
    ORDER_BAD_FRAC     = 0.20   # reject the answer if >20% of ids had to be repaired
    ORDER_TAIL_IGNORE  = 3      # upward jumps among the last N positions are expected (furniture goes last)

    PLAN_PROMPT = """
You are a deterministic, language-agnostic document layout and reading-order parser.
You are given:
1. A scanned page image with labeled bounding boxes (matching the block IDs).
2. OCR block metadata lines under `Regions:`:
   `id | x0,y0,x1,y1 (% of page, top-left origin) | type | text snippet`
   (Note: input detection order is NOT the reading order).

Execute this 4-step algorithm in exact sequence:

======================================================================
STEP 1: DETECT LAYOUT & GROUP ATOMIC PAIRS
======================================================================
1. Check horizontal X-coordinates (`x0, x1` in %) of body text blocks:
   - "2col": Two parallel vertical columns of body text sit side-by-side (Left column `x1 <= 55`, Right column `x0 >= 45`).
   - "1col": Single vertical flow of full-width text, centered headers, or lists.
   - TRAP WARNING: If a page has full-width paragraphs (`x0 < 20` and `x1 > 80`) and only wraps around an inset side Figure+Caption on part of the page, classify as "1col" (NOT "2col").
2. Group Atomic Pairs & Horizontal Rows:
   - Pair every `[picture]` / Figure / Table with its adjacent Caption (`[Figure, Caption]` or `[Table_Caption, Table]`). Never separate them.
   - HORIZONTAL BANDS (Forms/Exams/Syllabi): If multiple small blocks sit on roughly the same horizontal row (`|y0_A - y0_B| <= 3`), order them strictly Left-to-Right (`x0` ascending) before moving down.
   - OVERLAPPING/SPLIT HEADINGS: If a small heading/label box overlaps or sits at the immediate top-left of a content box (e.g., a unit label split from its text), always place the heading box immediately BEFORE the content box.

======================================================================
STEP 2: CHECK TOP-OF-PAGE CONTINUATION (FLOAT BYPASS RULE)
======================================================================
1. Identify `FIRST_BODY_BLOCK`:
   - In "1col": The topmost body `Text` block (skipping top Figures, Pictures, Tables, Captions, and Page Headers).
   - In "2col": The topmost body `Text` block in the LEFT column (skipping top full-width Figures, Captions, Title, and Author blocks).
2. Inspect the very start of `FIRST_BODY_BLOCK`'s text snippet:
   - Bicameral scripts (English/Latin): Starts with a lowercase letter (`a-z`) or hyphenated word fragment.
   - Unicameral/Indic scripts (Tamil, Telugu, etc.): Starts mid-sentence, or starts with a continued numbered/lettered list item (e.g., starts at `4.` when `1-3` were on the previous page, or finishes a previous syllabus unit above a new main title).
3. IF `FIRST_BODY_BLOCK` is a continuation from the previous page AND there is a floating `[Figure, Caption]` or `[Table_Caption, Table]` physically above it (`y0_float < y0_FIRST_BODY_BLOCK`):
   - ACTION: Move `FIRST_BODY_BLOCK` to the VERY FRONT of the reading order (index 0), jumping ahead of the top float(s). Immediately after `FIRST_BODY_BLOCK`, emit the bypassed top float(s) in their original order, and then resume the rest of the column/page.
   - ELSE: Keep standard top-to-bottom zone order.

======================================================================
STEP 3: RESOLVE 2-COLUMN FLOW, WRAPPED FIGURES & CUT-OFFS
======================================================================
1. IF `layout_type == "2col"`:
   - Read full-width top headers/floats first (unless bypassed in Step 2).
   - Read the entire LEFT column top-to-bottom (`y0` ascending).
   - Then read the entire RIGHT column top-to-bottom (`y0` ascending).
   - TRAP WARNING: If the bottom block of the LEFT column ends mid-sentence/hyphenated AND the top block of the RIGHT column starts with a lowercase word, that is a normal intra-page transition (`Left_Bottom -> Right_Top`). NEVER move `Right_Top` to the start of the page.
   - FOOTNOTE / ABSTRACT RULE: If a column has a footnote at the bottom, but the last body paragraph of that column is cut off mid-sentence, place the footnote BEFORE the cut-off paragraph (or after the completion of an Abstract split).
2. IF `layout_type == "1col"` with an Inline Wrapped Figure:
   - Never insert a `[Figure, Caption]` pair inside an unfinished sentence. Place the `[Figure, Caption]` pair immediately after the complete paragraph that references it (or between two complete paragraphs).
3. Footer & Margin Anchors:
   - Place bare page numbers, `[P.T.O.]`, and scanner watermarks (`Scanned by CamScanner`) at the very end of the reading order (unless the final body block is cut off mid-sentence at page end, in which case furniture goes just before the cut-off block).
4. FLOW CHECK (do this last, before you answer): read your final order from first to last like a person reading the page. Blocks side by side on one row (e.g. "Time ..." and "Max marks ...") go left to right; a heading comes right before its content; numbered or lettered items (1, 2, 3 ... / (a), (b) ...) follow in sequence. If a block breaks this flow, fix the order (the rules above still win), then answer.

======================================================================
STEP 4: OUTPUT FORMAT (STRICT JSON ONLY — NO MARKDOWN FENCES)
======================================================================
Every input `id` MUST appear in `"order"` exactly once using the exact string ID given (e.g., `"p1_B0"`). Fill the JSON keys in this exact order:
{
  "layout_type": "1col or 2col",
  "step1_zones_and_pairs": "List top floats/headers, atomic [Figure, Caption] pairs, horizontal bands, or overlapping split boxes.",
  "step2_first_body_check": "State the ID of FIRST_BODY_BLOCK, quote its first 3 words, state if it continues from the previous page, and state if it jumps ahead of any top floats.",
  "step3_column_and_cutoff_check": "For 2col: check Left-Bottom to Right-Top transition. For 1col: check wrapped figures or split OCR lines. List any blocks ending mid-sentence.",
  "first_text_block_starts_mid_flow": true,
  "starts_mid": ["ids of blocks starting mid-sentence or mid-list"],
  "cut_off": ["ids of blocks ending mid-sentence or split across boxes"],
  "moves": "1-sentence summary of reorders made",
  "order": ["id", "id", "..."]
}
"""

    BBOX_NOTE = (
        "\nEach line under Regions has format: `id | x0,y0,x1,y1 | type | text snippet` "
        "where `x0,y0,x1,y1` are percentages (0 to 100) from the top-left corner.\n"
    )

    REGIONS_HEADER = "\nRegions:\n"


    def _pct(b):
        bb, wh = b.get("bbox_xyxy"), b.get("page_wh")
        if not bb or not wh: return "?"
        return ",".join(str(round(100 * v / d)) for v, d in zip(bb, (wh[0], wh[1], wh[0], wh[1])))

    def _brief(b, n=50):
        t = " ".join((b.get("text") or "").split())
        if b.get("is_picture"): return "[picture]"
        return t if len(t) <= 2 * n + 3 else f"{t[:n]} … {t[-n:]}"

    def _labeled_page_image_data_uri(page_1based, blocks, max_side=1024, jpeg_quality=85):
        img = render_page(page_1based - 1)
        if img is None:
            raise RuntimeError(f"render_page({page_1based - 1}) returned None")
        img = img.copy()
        h, w = img.shape[:2]
        for b in blocks:
            bb = b.get("bbox_xyxy")
            if not bb: continue
            x0, y0, x1, y1 = [int(round(v)) for v in bb]
            btype = (b.get("bodhan_type") or "Text").lower()
            if b.get("is_picture") or "pic" in btype or "fig" in btype:
                color = (40, 160, 40)
            elif "tab" in btype or "head" in btype or "title" in btype:
                color = (200, 90, 20)
            elif "cap" in btype:
                color = (160, 30, 120)
            else:
                color = (30, 110, 220)
            cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
            short_id = b["block_id"].split("_", 1)[-1]
            (tw, th), _ = cv2.getTextSize(short_id, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            ly0 = max(0, y0 - th - 6)
            cv2.rectangle(img, (x0, ly0), (x0 + tw + 8, ly0 + th + 6), color, -1)
            cv2.putText(img, short_id, (x0 + 4, ly0 + th + 1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)

        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            raise RuntimeError(f"cv2.imencode failed for labeled page {page_1based}")
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")

    def _repair_order(got, want):
        """Make the model's list a valid permutation. Returns (order, n_unknown_or_dup, missing)."""
        short_to_full = {w.split("_", 1)[-1]: w for w in want}
        ws, seen, clean = set(want), set(), []
        for raw_i in got:
            i = raw_i if raw_i in ws else short_to_full.get(raw_i, raw_i)
            if i in ws and i not in seen:
                clean.append(i); seen.add(i)
        n_bad   = len(got) - len(clean)
        missing = [i for i in want if i not in seen]
        for m in missing:                       # put it after its nearest detect-order predecessor
            k, pos = want.index(m), 0
            for j in range(k - 1, -1, -1):
                if want[j] in clean: pos = clean.index(want[j]) + 1; break
            clean.insert(pos, m)
        return clean, n_bad, missing

    def _h_frac(a, b):
        ov = min(a[2], b[2]) - max(a[0], b[0])
        return 0.0 if ov <= 0 else ov / max(1.0, min(a[2] - a[0], b[2] - b[0]))

    def _order_flags(ordered, starts_mid_ids=()):
        """Report consecutive blocks in one column where the next block lies entirely ABOVE the previous,
    IGNORING intentional Step 2 float-bypass moves (where `a` is in starts_mid_ids)."""
        starts_mid_set = set(starts_mid_ids or ())
        flags = []
        for k, (a, b) in enumerate(zip(ordered, ordered[1:])):
            if a["block_id"] in starts_mid_set or a["block_id"].split("_", 1)[-1] in starts_mid_set:
                continue
            A, B = a.get("bbox_xyxy"), b.get("bbox_xyxy")
            if A and B and _h_frac(A, B) >= 0.5 and B[3] < A[1] and k + 1 < len(ordered) - ORDER_TAIL_IGNORE:
                flags.append((a["block_id"], b["block_id"]))
        return flags

    def _ask_order(messages):
        body = {
            "model": ORDER_MODEL,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        data = _post_with_retry(body, 240)
        if "error" in data: raise RuntimeError(f"openrouter: {data['error']}")
        txt = data["choices"][0]["message"]["content"] or ""
        parsed_json = _parse_json_object(txt)
        order_list = [str(x) for x in (parsed_json.get("order") or [])]
        starts_mid = [str(x) for x in (parsed_json.get("starts_mid") or [])]
        return txt, order_list, starts_mid

    def _validated(got, blocks):
        want = [b["block_id"] for b in blocks]
        order, n_bad, missing = _repair_order(got, want)
        if n_bad + len(missing) > max(2, ORDER_BAD_FRAC * len(want)):
            raise ValueError(f"too broken to repair (unknown/dup={n_bad}, missing={missing})")
        by_id = {b["block_id"]: b for b in blocks}
        return [by_id[i] for i in order], n_bad, missing

    def _llm_page_order(page_no, blocks, include_bbox=True):
        lines = "\n".join(
            f'{b["block_id"]} | {_pct(b)} | {b.get("bodhan_type", "Text")} | {_brief(b)}'
            for b in blocks
        )
        full_prompt = (
            PLAN_PROMPT
            + (BBOX_NOTE if include_bbox else "")
            + REGIONS_HEADER
            + lines
        )
        labeled_uri = _labeled_page_image_data_uri(page_no, blocks)
        first = [{"role": "user", "content": [
                    {"type": "text", "text": full_prompt},
                    {"type": "image_url", "image_url": {"url": labeled_uri}}]}]
        txt, got, starts_mid = _ask_order(first)
        final, n_bad, missing = _validated(got, blocks)
        notes = []
        if n_bad or missing: notes.append(f"repaired (unknown/dup={n_bad}, missing={missing})")

        flags = _order_flags(final, starts_mid_ids=starts_mid)
        if flags:
            pairs = "; ".join(f"{a} then {b}" for a, b in flags)
            ask = ("Check your order. In these consecutive pairs the second block lies entirely above the first, inside the same column: "
                   f"{pairs}. For each pair, keep it only if intended (continuation jump, caption/figure pairing, overlapping split header, or furniture last); "
                   "otherwise fix it. Reply with the full JSON object again.")
            try:
                _, got2, _ = _ask_order(first + [{"role": "assistant", "content": txt}, {"role": "user", "content": ask}])
                final2, b2, m2 = _validated(got2, blocks)
                left = _order_flags(final2, starts_mid_ids=starts_mid)
                notes.append(f"flags {flags} → re-check " + ("changed order" if final2 != final else "kept order") +
                             (f", still flagged {left}" if left else ""))
                final = final2
            except Exception as e:
                notes.append(f"flags {flags}; re-check failed ({type(e).__name__}) → kept first answer")
        return final, "; ".join(notes) or "ok"

    def _order_all_pages(sendable):
        by_page = defaultdict(list)
        for b in sendable: by_page[b["page"]].append(b)          # detect order preserved
        short = lambda L: [b["block_id"].split("_", 1)[1] for b in L]
        def _one(pno):
            det = by_page[pno]; final, note = det, "single block"
            if len(det) > 1:
                try: final, note = _llm_page_order(pno, det)
                except Exception as e:
                    final, note = det, f"model order rejected ({type(e).__name__}: {str(e)[:120]}) -> detect order"
            return pno, det, final, note
        with ThreadPoolExecutor(ORDER_WORKERS) as _ex:             # all pages at once
            res = list(_ex.map(_one, sorted(by_page)))
        out = []
        for pno, det, final, note in res:
            moved = sum(1 for x, y in zip(det, final) if x is not y)
            print(f"  page {pno}: {note}\n     detect: {short(det)}\n     final : {short(final)}   ({moved} position(s) changed)")
            out.extend(final)
        return out

    # ── called after the main chunk loop (formatted_by_id complete) ─────────
    def _validate_final_order(expected, formatted_by_id):
        exp_ids = [b["block_id"] for b in expected]
        got     = [i for i in formatted_by_id if i in set(exp_ids)]
        if got == exp_ids:
            print(f"✓ final order check: all {len(exp_ids)} sent blocks are in the planned order")
            return True
        miss = [i for i in exp_ids if i not in formatted_by_id]
        first = next((k for k, (x, y) in enumerate(zip(exp_ids, got)) if x != y), None)
        print(f"✗ final order check FAILED: missing={miss}, first mismatch at position {first}: planned {exp_ids[first] if first is not None else '-'} vs got {got[first] if first is not None else '-'}")
        return False


    # ── collect blocks flat (text + picture + empty-text) for chunk assembly ─
    def _collect_blocks_from_page_dicts(page_dicts):
        out = []
        for page in page_dicts:
            for region in page["regions"]:
                is_pic = region.get("type") == "picture"
                out.append({
                    "block_id":    region["block_id"],
                    "page":        region["page"],
                    "bodhan_type": region.get("bodhan_type", "Picture" if is_pic else "Text"),
                    "bbox_xyxy":   region.get("bbox_xyxy"),
                    "page_wh":     region.get("page_wh"),
                    "image_path":  region.get("image_path"),
                    "text":        "" if is_pic else (region.get("text", "") or ""),
                    "is_picture":  is_pic,
                })
        return out


    # ── serialize chunk → one <BLOCK id="..." ...> envelope per block ───────
    _HTML_TABLE_RE = re.compile(r"<\s*table\b", re.I)

    def _esc(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _bbox_norm_str(b):
        bb, wh = b.get("bbox_xyxy"), b.get("page_wh")
        if not bb or not wh: return "null"
        W, H = wh
        try:
            return f"{bb[0]/W:.3f},{bb[1]/H:.3f},{bb[2]/W:.3f},{bb[3]/H:.3f}"
        except Exception:
            return "null"

    def _serialize_blocks_for_combined(blocks):
        parts = []
        for b in blocks:
            body = "" if b.get("is_picture") else (b.get("text") or "").strip()
            if body and not _HTML_TABLE_RE.search(body):
                body = _esc(body)
            bbox_s = _bbox_norm_str(b)
            parts.append(
                f'<BLOCK id="{b["block_id"]}" type="{b["bodhan_type"]}" '
                f'page="{b["page"]}" bbox="{bbox_s}">\n{body}\n</BLOCK>'
            )
        return "\n\n".join(parts)


    # ── lenient deserializer: preserves EMIT ORDER (= reading order) ─────────
    _BLOCK_OPEN_RE  = re.compile(r"<BLOCK\b[^>]*?\bid\s*=\s*[\"']?(?P<id>[^\s\"'>]+)[\"']?[^>]*>", re.I)
    _BLOCK_CLOSE_RE = re.compile(r"</BLOCK\s*>", re.I)

    def _deserialize_combined_output(text):
        """Returns an insertion-ordered dict {block_id: markup}.
    Insertion order = the order the model emitted the wrappers = reading order."""
        text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
        text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        opens = list(_BLOCK_OPEN_RE.finditer(text))
        out = {}
        for i, m in enumerate(opens):
            end = opens[i + 1].start() if i + 1 < len(opens) else len(text)
            body = text[m.end():end]
            cm = _BLOCK_CLOSE_RE.search(body)
            if cm: body = body[:cm.start()]
            bid = m.group("id")
            if bid not in out:                 # keep first (= earliest) emit position
                out[bid] = body.strip()
        return out


    # ── tag grammar used by the fidelity skeleton (DOCX builder reuses it) ──
    _KNOWN_TAGS = "P|STYLE|BR|BLANK|LIST|ITEM|QUESTION|OPTIONS|OPTION|TABLE|ROW|CELL|IMG|RIGHT|FORMULA|SPAN"
    _TAG_RE = re.compile(
        r"<(?P<close>/?)(?P<name>%s)(?P<attrs>(?:\s+[A-Za-z_][\w-]*\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s<>/\"']+))*)\s*(?P<self>/?)>"
        % _KNOWN_TAGS, re.I)
    _INLINE_STYLE_RE = re.compile(r"</?STYLE(?:\s[^<>]*)?/?>", re.I)
    _ATTR_RE = re.compile(r"""([A-Za-z_][\w-]*)\s*=\s*("[^"]*"|'[^']*'|[^\s<>/"']+)""")
    _VOID_TAGS = {"BR", "BLANK", "IMG"}

    def _parse_attrs(s):
        return {k.lower(): v.strip("\"'") for k, v in _ATTR_RE.findall(s or "")}


    # ── fidelity: multiset character comparison (reorder-insensitive) ───────
    def _skeleton(s):
        s = s.replace("\\n", "")                                  # literal backslash-n artefacts
        s = re.sub(r"\s+", "", s)                                 # whitespace edits allowed
        s = re.sub(r"([._\-–—…])\1{2,}", "", s)                   # blank runs
        for ch in FID_IGNORE_CHARS: s = s.replace(ch, "")
        return s

    def _skeleton_raw(raw):
        raw = raw or ""
        if _HTML_TABLE_RE.search(raw):
            s = re.sub(r"<br\s*/?>", " ", raw, flags=re.I)
            s = re.sub(r"</?[A-Za-z][^<>]*>", " ", s)
            s = _html.unescape(s)
        else:
            s = raw
        return _skeleton(s)

    _DROP_RE = re.compile(r"<DROP>(.*?)</DROP>", re.S | re.I)
    def _strip_drop(m):
        return _DROP_RE.sub("", m or "")

    
    # ── DROP guard: honour <DROP> only for things that are clearly furniture ──
    _WATERMARK_RE = re.compile(r"scann(?:ed|er)\b|camscanner|cam\s*scanner|adobe\s*scan|tapscanner|genius\s*scan|office\s*lens|microsoft\s*lens", re.I)
    _PTO_RE       = re.compile(r"^\W*(?:p\.?\s*t\.?\s*o\.?|please\s+turn\s+over|turn\s+over|continued(?:\s+on\s+next\s+page|\s+overleaf)?|contd\.?)\W*$", re.I)
    _PAGENUM_RE   = re.compile(r"^\W*(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\W*$", re.I)
    MARGIN_FRAC   = 0.04                # a bare page number is only dropped in the top / bottom 4% of the page

    def _norm_txt(t): return re.sub(r"\s+", " ", _html.unescape(t or "")).strip().casefold()

    def _ypos(b):
        bb, wh = b.get("bbox_xyxy"), b.get("page_wh")
        return None if (not bb or not wh) else (bb[1] + bb[3]) / 2 / wh[1]

    def _in_margin(b):
        cy = _ypos(b)
        return cy is not None and (cy <= MARGIN_FRAC or cy >= 1 - MARGIN_FRAC)

    def _drop_ok(frag, blk):
        t = _norm_txt(re.sub(r"<[^>]+>", "", frag))
        if not t: return True                                   # empty wrapper: nothing to lose
        if _WATERMARK_RE.search(t) or _PTO_RE.match(t): return True
        if _in_margin(blk) and _PAGENUM_RE.match(t): return True
        return False

    def _apply_drop_guard(markup, blk, ind=""):
        def _sub(m):
            frag = m.group(1)
            if _drop_ok(frag, blk):
                print(f"    {ind}✂ {blk['block_id']}: removed furniture {frag.strip()[:50]!r}")
                return ""
            y = _ypos(blk)
            print(f"    {ind}⚑ {blk['block_id']}: kept (not furniture): {frag.strip()[:50]!r}" + (f"  [y={y:.0%} of page]" if y is not None else ""))
            return frag
        return _DROP_RE.sub(_sub, markup or "").strip()
    

    def _skeleton_out(out):
        out = re.sub(r"</?DROP>", "", out or "", flags=re.I)   # dropped text still counts as present
        s = _INLINE_STYLE_RE.sub("", out or "")                   # m<STYLE>2</STYLE> → "m2"
        s = _TAG_RE.sub(" ", s)
        return _skeleton(_html.unescape(s))

    def _diff_multiset(a, b):
        """Reorder-insensitive diff: count chars dropped / added as multiset deltas."""
        if a == b: return 0, 0, []
        A, B = Counter(a), Counter(b)
        dropped = sum((A - B).values())
        added   = sum((B - A).values())
        sample  = []
        # show a handful of chars that disappeared / appeared, for debugging
        disappeared = [c for c in (A - B).elements()][:12]
        appeared    = [c for c in (B - A).elements()][:12]
        if disappeared: sample.append("dropped chars: " + "".join(disappeared))
        if appeared:    sample.append("added chars: "   + "".join(appeared))
        return dropped, added, sample

    def _chunk_faithful(blocks, parsed_ordered):
        """Chunk-level fidelity on multisets of characters.
    Only text blocks count (pictures have no OCR text)."""
        text_blocks = [b for b in blocks if not b.get("is_picture")]
        a = "".join(_skeleton_raw(b.get("text")) for b in text_blocks)
        b = "".join(_skeleton_out(parsed_ordered.get(x["block_id"], "")) for x in text_blocks)
        dropped, added, ex = _diff_multiset(a, b)
        # len(a) == 0 edge case: a chunk whose text input is empty has
        # 3% × 0 = 0, so even one added char would trip the check. The
        # min-char floor already handles that; keep this comment to make it
        # explicit for future readers.
        if dropped > max(FID_DROP_MIN_CHARS, FID_DROP_TOL * len(a)):
            return False, f"dropped {dropped}/{len(a)} chars: " + " | ".join(ex)
        if added > max(FID_ADD_MIN_CHARS, FID_ADD_TOL * len(a)):
            return False, f"added {added} chars (input {len(a)}): " + " | ".join(ex)
        return True, f"-{dropped}/+{added} chars vs {len(a)}"


    # ── raw-text fallback (if the chunk is unrecoverable) → tag markup ──────
    _HTML_TABLE_FULL_RE = re.compile(r"<table\b.*?</table>", re.S | re.I)

    def _html_table_to_markup(table_html):
        out = ['<TABLE border="1">']
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, flags=re.S | re.I):
            out.append("<ROW>")
            for tag, attrs, cell in re.findall(r"<(t[dh])\b([^>]*)>(.*?)</\1>", row, flags=re.S | re.I):
                cell = re.sub(r"<br\s*/?>", " ", cell, flags=re.I)
                cell = _html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                cs = re.search(r"colspan\s*=\s*[\"']?(\d+)", attrs, re.I)
                extra = (' colspan="%s"' % cs.group(1)) if cs else ""
                if tag.lower() == "th": extra += ' bold="true"'
                out.append("<CELL%s>%s</CELL>" % (extra, _html.escape(cell, quote=False)))
            out.append("</ROW>")
        out.append("</TABLE>")
        return "\n".join(out)

    def _raw_to_markup(raw):
        raw = (raw or "").strip()
        if not _HTML_TABLE_RE.search(raw):
            return _html.escape(raw, quote=False)
        parts, pos = [], 0
        for m in _HTML_TABLE_FULL_RE.finditer(raw):
            if m.start() > pos: parts.append(_html.escape(raw[pos:m.start()], quote=False))
            parts.append(_html_table_to_markup(m.group(0)))
            pos = m.end()
        if pos < len(raw): parts.append(_html.escape(raw[pos:], quote=False))
        return "\n".join(parts)

    _RAW_LABEL = re.compile(r"^\s*(?:\(?\d{1,3}\s*[\.\)]|\(?[a-zA-Z]\)|\(?[ivxIVX]{1,4}[\.\)])\s+\S")
    def _raw_wrap_tags(markup):
        """bare RAW lines -> <P> per line; consecutive numbered/lettered lines -> one <LIST type="number"> (hanging indent). Tables untouched."""
        out = []
        for piece in re.split(r"(?is)(<TABLE.*?</TABLE>)", markup or ""):
            if piece[:6].upper() == "<TABLE": out.append(piece); continue
            items = []
            def flush():
                if items: out.append('<LIST type="number">' + "".join(f'<ITEM level="0">{t}</ITEM>' for t in items) + "</LIST>"); items.clear()
            for line in piece.split("\n"):
                line = line.strip()
                if not line: continue
                if _RAW_LABEL.match(line): items.append(line)
                else: flush(); out.append(f"<P>{line}</P>")
            flush()
        return "\n".join(out)

    def _raw_fallback(b):
        return "" if b.get("is_picture") else _raw_wrap_tags(_raw_to_markup(b.get("text") or ""))


    # ── page image data URI (cached) ────────────────────────────────────────
    _PAGE_IMG_CACHE = {}

    def _page_image_data_uri(page_1based, max_side=800, jpeg_quality=85):
        if page_1based in _PAGE_IMG_CACHE:
            return _PAGE_IMG_CACHE[page_1based]
        img = render_page(page_1based - 1)
        if img is None:
            raise RuntimeError(f"render_page({page_1based - 1}) returned None")
        h, w = img.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            raise RuntimeError(f"cv2.imencode failed for page {page_1based}")
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
        _PAGE_IMG_CACHE[page_1based] = uri
        return uri

    def _pages_in_chunk(blocks_chunk):
        return sorted({b["page"] for b in blocks_chunk})


    # ── HTTP with exponential backoff ───────────────────────────────────────
    class CombinedNetworkError(RuntimeError):
        pass

    def _post_with_retry(body, timeout):
        """One API call with a HARD wall-clock limit per attempt (requests' timeout is per socket read)."""
        last = None
        for attempt in range(1, HTTP_ATTEMPTS + 1):
            box = {}
            def _run(box=box):                  # bind this attempt's box so an abandoned thread can't write into a later attempt
                try:
                    box["r"] = requests.post(OPENROUTER_URL,
                                             headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                                             json=body, timeout=min(timeout, CALL_DEADLINE))
                except BaseException as e:
                    box["e"] = e
            _t0 = time.time()
            th = threading.Thread(target=_run, daemon=True); th.start(); th.join(CALL_DEADLINE)
            try:
                if th.is_alive():
                    raise requests.exceptions.Timeout(f"no complete response within {CALL_DEADLINE}s")
                if "e" in box: raise box["e"]
                r = box["r"]
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                else:
                    r.raise_for_status()
                    _dt = time.time() - _t0
                    if _dt > 45: print(f"      ⏱ slow API call: {_dt:.0f}s ({body.get('model')})")
                    return r.json()
            except requests.exceptions.HTTPError:
                raise
            except requests.exceptions.RequestException as e:
                last = f"{type(e).__name__}: {str(e)[:100]}"
            if attempt < HTTP_ATTEMPTS:
                wait = min(30, 2 ** attempt)
                print(f"      ⚠ API call failed ({last}); retry {attempt}/{HTTP_ATTEMPTS - 1} in {wait}s")
                time.sleep(wait)
        raise CombinedNetworkError(f"gave up after {HTTP_ATTEMPTS} attempts: {last}")


    # ── one combined call → ordered {block_id: markup}, finish_reason ───────
    def _combined_once(blocks_chunk, model, temperature=0.0, timeout=240):
        tagged = _serialize_blocks_for_combined(blocks_chunk)
        pages = _pages_in_chunk(blocks_chunk)
        text_part = (
            COMBINED_PROMPT + tagged
            + f"\n\n[The {len(pages)} page image(s) attached below show page(s) {pages} "
              f"of the original scan. Use them for (A) verifying every removal and (C) formatting only — "
              f"the block order is fixed and must be kept. Do NOT read, transcribe or add any text from the "
              f"image(s). Each block's output may contain ONLY the words of that block.]"
        )
        content = [{"type": "text", "text": text_part}]
        for pno in pages:
            try:
                content.append({"type": "image_url", "image_url": {"url": _page_image_data_uri(pno)}})
            except Exception as e:
                print(f"      ⚠ page image render failed for page {pno}: {type(e).__name__}: {e} — text-only for it")

        body = {"model": model, "temperature": temperature,
                "messages": [{"role": "user", "content": content}]}
        data = _post_with_retry(body, timeout)
        if "error" in data: raise RuntimeError(f"openrouter: {data['error']}")
        raw    = data["choices"][0]["message"]["content"] or ""
        finish = data["choices"][0].get("finish_reason", "?")
        parsed = _deserialize_combined_output(raw)

        # single-block call where the model returned the body without a wrapper
        if len(blocks_chunk) == 1 and not parsed and raw.strip():
            body_txt = re.sub(r"^```[a-zA-Z]*\s*\n|\n```\s*$", "", raw.strip())
            parsed = {blocks_chunk[0]["block_id"]: body_txt.strip()}

        expected = {b["block_id"] for b in blocks_chunk}
        for extra in list(parsed):
            if extra not in expected: parsed.pop(extra, None)
        return parsed, finish


    # ── combined call with model fallback (tries COMBINED_MODELS in order) ──
    def _combined_with_model_fallback(blocks_chunk, temperature=0.0):
        last_err = None
        for model in COMBINED_MODELS:
            try:
                print(f"      trying {model} …")
                parsed, finish = _combined_once(blocks_chunk, model, temperature)
                return parsed, finish, model
            except CombinedNetworkError:
                raise
            except Exception as e:
                print(f"      {model} failed: {type(e).__name__}: {str(e)[:160]}")
                last_err = e
        raise RuntimeError(f"all combined models failed: {last_err}")


    # ── chunk-level fidelity + split-on-reject (same shape as the previous
    #    formatter, but using the multiset diff and the combined prompt) ────
    def _format_chunk(blocks_chunk, depth=0, temperature=0.0):
        ind = "  " * depth
        ids = [b["block_id"] for b in blocks_chunk]
        parsed, finish, used_model = _combined_with_model_fallback(blocks_chunk, temperature)

        missing = [i for i in ids if i not in parsed]
        if missing:
            ok, why = False, f"no <BLOCK> returned for {missing} (finish_reason={finish!r})"
        else:
            ok, why = _chunk_faithful(blocks_chunk, parsed)

        if ok:
            # keep the model's emit order; return the list of (id, markup) pairs
            emit = [bid for bid in parsed if bid in set(ids)]
            if emit != ids:
                print(f"    {ind}ℹ formatter changed block order {emit} — ignored, keeping {ids}")
            _by = {b["block_id"]: b for b in blocks_chunk}
            ordered = [(bid, _apply_drop_guard(parsed[bid], _by[bid], ind)) for bid in ids]   # order fixed upstream; only real furniture removed
            return ordered, used_model, []

        print(f"    {ind}⚠ chunk {ids} rejected: {why}")
        if SHOW_REJECT_DEBUG:
            for b in blocks_chunk:
                print(f"    {ind}    {b['block_id']} raw: {(b.get('text') or '')[:70]!r}")
                print(f"    {ind}    {b['block_id']} out: {parsed.get(b['block_id'], '<missing>')[:140]!r}")

        if depth >= MAX_SPLIT_DEPTH:
            print(f"    {ind}✗ giving up on {ids} — using RAW text (detection order kept)")
            return [(b["block_id"], _raw_fallback(b)) for b in blocks_chunk], used_model, list(ids)

        if len(blocks_chunk) > 1:
            mid = len(blocks_chunk) // 2
            groups, temp = [blocks_chunk[:mid], blocks_chunk[mid:]], temperature
        else:
            groups, temp = [blocks_chunk], 0.3
        print(f"    {ind}↻ retrying as {len(groups)} group(s) (depth {depth + 1})")

        ordered_out, raw_ids = [], []
        model_used = used_model
        for g in groups:
            g_ordered, model_used, g_raw = _format_chunk(g, depth + 1, temp)
            ordered_out.extend(g_ordered)
            raw_ids.extend(g_raw)
        return ordered_out, model_used, raw_ids


    # ── build chunks from Stage 1 output ────────────────────────────────────
    blocks_A = _collect_blocks_from_page_dicts(page_dicts)
    print(f"total blocks: {len(blocks_A)} "
          f"({sum(1 for b in blocks_A if not b['is_picture'])} text, "
          f"{sum(1 for b in blocks_A if b['is_picture'])} picture)")

    # Skip empty-text, non-picture blocks — Bodhan sometimes returns a bbox with
    # text="" (typically a PageHeader box that wrapped no OCR content). Sending
    # these to the model is wasted work AND it will transcribe the image region
    # into the empty wrapper, which the fidelity check then (correctly) rejects.
    # These blocks pass straight through to Stage 3 as empty.
    skipped_empty = [b for b in blocks_A if (not b["is_picture"]) and not (b.get("text") or "").strip()]
    sendable      = [b for b in blocks_A if b["is_picture"] or (b.get("text") or "").strip()]
    sendable = _order_all_pages(sendable)

    if skipped_empty:
        print(f"skipping {len(skipped_empty)} empty-text non-picture block(s) "
              f"(bodhan returned a bbox but no OCR text — "
              f"will render as empty in the DOCX):")
        for b in skipped_empty:
            bb = b.get("bbox_xyxy")
            bb_s = "null" if not bb else f"[{int(bb[0])},{int(bb[1])},{int(bb[2])},{int(bb[3])}]"
            print(f"  {b['block_id']:<14} page {b['page']}  "
                  f"bodhan_type={b['bodhan_type']:<14} bbox={bb_s}")

    # chunk mode A — fixed CHUNK_BLOCKS per call. Pictures are included so the
    # model orders them alongside text blocks. Empty-text blocks are NOT sent.
    chunks = [sendable[i:i + CHUNK_BLOCKS] for i in range(0, len(sendable), CHUNK_BLOCKS)]
    print(f"combined chunks: {len(chunks)} ({CHUNK_BLOCKS} block(s) each, pictures included)")

    # ── preflight: can we even reach the API host? ──────────────────────────
    try:
        socket.gethostbyname(re.sub(r"^https?://([^/]+).*$", r"\1", OPENROUTER_URL))
    except OSError as e:
        msg = f"DNS lookup for the API host failed ({e}). Check your internet/VPN/DNS and re-run."
        if ABORT_IF_NO_DNS:
            raise RuntimeError(msg)
        print("⚠ " + msg)


    # ── per-chunk disk cache (keyed by prompt + model + chunk composition) ──
    # Keying includes every block_id and text in the chunk, so re-chunking
    # (changing CHUNK_BLOCKS) safely invalidates stale entries.
    _cache_path = METHOD_DIR / f"{stem}__combined_cache.json"
    def _chunk_key(chunk):
        sig = "|".join(f"{b['block_id']}:{(b.get('text') or '')[:400]}" for b in chunk)
        primary_model = COMBINED_MODELS[0]
        h = hashlib.sha1(f"{_PROMPT_HASH}|{primary_model}|{len(chunk)}|{sig}".encode("utf-8")).hexdigest()
        return h[:24]
    _cache = {}
    if USE_CACHE and _cache_path.exists():
        try: _cache = json.loads(_cache_path.read_text(encoding="utf-8"))
        except Exception: _cache = {}
        print(f"cache: {len(_cache)} chunk(s) in {_cache_path.name} (stale keys ignored)")


    # ── main loop (parallel chunks; results merged in chunk order = reading order) ──
    set_stage("A_combined", "qwen_chunks")
    formatted_by_id = {}        # insertion order = overall reading order
    raw_fallback_ids = []
    combined_failures = []
    model_used_log = {}
    import threading
    _plock = threading.Lock()
    _net = {"streak": 0, "tripped": False}

    def _do_chunk(item):
        ci, chunk = item
        ck = _chunk_key(chunk)
        if USE_CACHE and isinstance(_cache.get(ck), dict) and "ordered" in _cache[ck]:
            print(f"  chunk {ci}/{len(chunks)}: cached")
            return ci, chunk, _cache[ck]["ordered"], None, [], True
        if _net["tripped"]:
            return ci, chunk, None, "breaker", [], False
        try:
            print(f"  chunk {ci}/{len(chunks)} ({len(chunk)} blocks, pages={_pages_in_chunk(chunk)}) ...")
            ordered, used_model, raw_ids = _format_chunk(chunk)
            with _plock:
                _net["streak"] = 0; model_used_log[ci] = used_model
            return ci, chunk, ordered, None, raw_ids, False
        except Exception as e:
            print(f"    chunk {ci} failed: {type(e).__name__}: {str(e)[:200]} -> keeping RAW text for its blocks")
            with _plock:
                if isinstance(e, CombinedNetworkError):
                    _net["streak"] += 1
                    if _net["streak"] >= NET_FAIL_BREAKER: _net["tripped"] = True
                else:
                    _net["streak"] = 0
            return ci, chunk, None, e, [], False

    with ThreadPoolExecutor(CHUNK_WORKERS) as _ex:
        _results = list(_ex.map(_do_chunk, enumerate(chunks, 1)))
    for ci, chunk, ordered, err, raw_ids, was_cached in _results:
        if ordered is not None:
            for bid, mk in ordered:
                if bid not in formatted_by_id: formatted_by_id[bid] = mk
            raw_fallback_ids.extend(raw_ids)
            if USE_CACHE and not was_cached: _cache[_chunk_key(chunk)] = {"ordered": ordered}
        else:
            for b in chunk:
                formatted_by_id[b["block_id"]] = _raw_fallback(b)
                raw_fallback_ids.append(b["block_id"])
            combined_failures.append(ci)
    breaker_tripped = _net["tripped"]
    if breaker_tripped:
        print(f"\n  STOP: {NET_FAIL_BREAKER}+ consecutive network failures - remaining chunks used RAW text. Fix the connection and re-run; cached chunks are reused.")
    if USE_CACHE:
        _cache_path.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")

    # ── order validation: the formatter output must follow the planned order ──
    _validate_final_order(sendable, formatted_by_id)

    # Any block the model never emitted (shouldn't happen, but defensively):
    # Blocks the model never saw: empty-text non-picture blocks skipped above,
    # plus any residual the model forgot. Give them an empty markup — the
    # downstream builder will record them as "empty_text_or_block" and skip them.
    # Importantly these are NOT counted as raw-fallback (nothing was sent).
    for b in blocks_A:
        if b["block_id"] not in formatted_by_id:
            formatted_by_id[b["block_id"]] = ""   # renders as empty block

    # Attach formatted_text + build reading-order-sorted blocks list
    _block_by_id = {b["block_id"]: b for b in blocks_A}
    for bid, mk in formatted_by_id.items():
        if bid in _block_by_id:
            _block_by_id[bid]["formatted_text"] = mk

    ordered_blocks = [_block_by_id[bid] for bid in formatted_by_id if bid in _block_by_id]
    # any residual block missing from the model's output goes at the end in detection order
    for b in blocks_A:
        if b["block_id"] not in formatted_by_id:
            ordered_blocks.append(b)

    set_stage("-", "-")

    print(f"\n── Stage 2 summary ──")
    print(f"chunks: {len(chunks)}  ·  blocks sent: {sum(len(c) for c in chunks)}  "
          f"·  formatted: {len(formatted_by_id)}  ·  raw-fallback: {len(set(raw_fallback_ids))}")
    if combined_failures:
        print(f"  failed chunks (used RAW): {combined_failures}")
