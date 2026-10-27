# Stage file 'grammar': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── CELL: tag grammar shared by every route (sliced from the combined prompt) ───
TAG_GRAMMAR = r"""Units: font size in points; indents in inches; spacing in points. Leave an attribute out when it is the default or when you cannot judge it.

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
"""

