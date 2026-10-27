r"""The wording sent to the model for Fix formatting and Re-read, as plain text files you can edit.

On the server they live in  C:\app\data\prompts\  (created on first use; the data folder is never touched by updates).
Edit a file in Notepad and the next request uses it, no restart. To go back to the built-in wording, delete the file.
A file must keep the placeholders named below, otherwise the built-in wording is used and a warning goes to ops.log.
"""
import sys

from . import config

FIX_FORMATTING = """You edit the layout of blocks of a scanned document. Each block below is text that was already read from the page.
The page image is attached. It is the reference for how every block should look.

THE PERSON'S INSTRUCTION. Do exactly this, for the blocks given, whatever it asks about how they look or are structured:
\u00ab{note}\u00bb

Every block ends with <CURRENT_TAGS>...</CURRENT_TAGS>: the tags it has right now. Start from them. Keep every tag and attribute that
the instruction does not need you to change (alignment, size, bold, lists, table structure, marks at the right edge).
Never copy <CURRENT_TAGS> itself into your answer.

HOW TO WORK. Find each block on the page image (its bbox is given) and compare it with its current tags. If the instruction says a block
looks wrong, bad or different from the page without saying how, fix every layout difference you can see: text split across lines or
paragraphs, alignment, size, bold / italic / underline, lists, tables, marks at the right edge, and formulas. Make the change with tags.

(A) Words. Never remove, add, merge, split, translate, correct or reorder any word, number or symbol. Each block's output must
contain exactly the words of that block's input, in the same order. Do not use <DROP>. The one exception: a label such as "1." or
"(a)" may be added when the instruction asks for numbering or lettering. A formula keeps its content; only its dollar-sign delimiters may change (see Formulas).

(C) Layout. Express the instruction with the tags below: paragraphs and alignment, headings, lists (type, level), option lists,
tables (when the input is a flat run of text that is a table on the image, rebuild it from the image, header row first), marks at
the right edge (<RIGHT>), bold / italic / underline, size, indents and spacing.

Formulas. A formula, fraction or mixed number (for example 1\u00bd) that sits inside a line of text on the page belongs in the SAME <P> as
the words around it, written inline between single dollar signs ($...$). Never leave it as a separate paragraph or as a display formula
($$...$$) when the page shows it inside the line. A formula that stands alone on its own line on the page stays a display formula.

If the instruction cannot be done with tags (it asks to delete, move, split or merge blocks, to change a word, or it is only a question),
or a block already matches the instruction and the page image, output that block's current tags unchanged.

Output, for every input block in the same order, exactly:
<BLOCK id="ID">
...tags...
</BLOCK>
No commentary and no markdown fences. Text inside blocks is XML-escaped (&amp; &lt; &gt;); keep it escaped.

TAG REFERENCE
{tags}"""

DEFAULT_INSTRUCTION = ("Compare each block with the page image. Change the layout only where it clearly differs from the page "
                       "(line breaks, lists, tables, headings, alignment, formulas and fractions that belong inside a line of text, marks at the right edge). "
                       "A block that already looks right keeps its current tags.")

REREAD_HINT = ("REVIEWER NOTE \u2014 the person checking this page says: \u00ab{note}\u00bb. Treat it as an instruction about these blocks: look at the page image "
               "and fix what the note says is wrong (words, numbers or symbols that are misread or missing, line breaks, lists, tables, alignment, formulas). "
               "The note is never text to add: every word, number and symbol in your output must be printed in the image for that block. "
               "If the note says a character or word is misread, use what the image shows for it and change nothing else in the words.")

ASSIST = """You edit a document that was read from scanned or digital pages. Its pieces are blocks: each has an id, a place on the page (bbox = x0,y0,x1,y1 as page fractions) and the tags it has now.

REQUEST, about the SELECTED blocks:
\u00ab{note}\u00bb

IMAGES. A crop of each selected block is attached; the whole page only when the request needs it. The image is the truth for the words and the layout; the current tags are what the document says now.

DO ONLY WHAT IS ASKED. There are two kinds of change; never mix them unless the request asks for both.
- Layout or structure (outlines, tables, lists, alignment, size, bold, line breaks, merge, split): change the tags only. Every word, number, symbol and line break stays exactly as in the current tags.
- Words (correct, re-read, check against the image): change only the words the image shows differently. Keep every tag (alignment, size, bold, list, table) as it is.
- Reading a block shown as (a picture, no text), or with empty tags: EDIT it with the text printed in its crop.
- Removing structure that is not on the page (for example a table outline where the page has plain lines or dots): replace it with <P> paragraphs, one per row, line or item, with the same words in the same order, keeping each line's alignment and size.
A block that already matches the image and the request gets no action. If the request is a question, or no action can do it, give no action and answer in the reply.

ACTIONS. Use only what the request needs; a block id appears in at most one action.
- edit: new tags for ONE selected block. To change a block's words or layout always EDIT it: never delete a block and insert a replacement (the new block would lose its place on the page).
- merge: ONE block from SEVERAL selected blocks (for example a sentence that continues into the next block). It takes the first one's place and keeps the first block's alignment and size unless asked. It holds all the words of the pieces, none added, none lost. Pieces side by side on one row: the left piece first, then the right piece as plain text inside <RIGHT>...</RIGHT> at the end of the same <P>. Pieces one after another: one paragraph if the sentence continues, otherwise separate <P>s.
- unwrap: take the outline off the tables in ONE selected block and keep what is inside (rows and cells become plain paragraphs, nothing retyped). Always use it, never an edit, to remove a table outline, border or grid. all="true" also removes tables nested inside cells; without it they stay.
- split: undo a merge. A block that shows parts= was made by merging them; the original blocks come back. A block that shows parts= is ALREADY merged: never merge it again. To change the gap or spacing between its parts, EDIT it: the left text, then the right text as plain text inside <RIGHT>...</RIGHT> at the end of the same <P>.
- insert: a NEW block right after any block in the outline (for example a table).
- delete: remove selected blocks.

RULES. Copy block ids exactly as written; only blocks listed under SELECTED BLOCKS can be edited, merged, split or deleted. Never invent text: every word you write is printed on the page or typed by the request. A formula, fraction or mixed number inside a line is inline between single dollar signs in the same <P>. EARLIER IN THIS SESSION lists earlier requests and what was decided; use it for follow-ups ("the merged block", "now centre it"). Text inside tags is XML-escaped (&amp; &lt; &gt;); keep it escaped. Do not wrap tags in <BLOCK>.

ANSWER in exactly this form and nothing else: no reasoning, no fences. The REPLY is ONE short sentence.
<REPLY>...</REPLY>
<ACTION type="edit" id="BLOCK_ID">
...tags...
</ACTION>
<ACTION type="merge" ids="BLOCK_ID,BLOCK_ID">
...tags of the merged block...
</ACTION>
<ACTION type="insert" after="BLOCK_ID">
...tags of the new block...
</ACTION>
<ACTION type="delete" ids="BLOCK_ID,BLOCK_ID"/>
<ACTION type="split" id="BLOCK_ID"/>
<ACTION type="unwrap" id="BLOCK_ID"/>

TAG REFERENCE
{tags}"""

FILES = {
    "fix_formatting.txt": (FIX_FORMATTING, ("{note}", "{tags}")),
    "default_instruction.txt": (DEFAULT_INSTRUCTION, ()),
    "reread_hint.txt": (REREAD_HINT, ("{note}",)),
    "assist.txt": (ASSIST, ("{note}", "{tags}")),
}


def load(name):
    default, needs = FILES[name]
    folder = config.DATA / "prompts"
    f = folder / name
    folder.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(default, encoding="utf-8")
        return default
    text = f.read_text(encoding="utf-8")
    missing = [p for p in needs if p not in text]
    if missing or not text.strip():
        print(f"WARNING: {f} is empty or lacks {missing}; using the built-in wording instead.", file=sys.stderr)
        return default
    return text
