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

ASSIST = """You help a person correct a document that was read from scanned or digital pages. The page images are attached.
The document is made of blocks. Each block has an id, a place on the page (bbox = x0,y0,x1,y1 as fractions of the page) and the tags it has right now.

THE PERSON'S REQUEST, about the SELECTED blocks:
\u00ab{note}\u00bb

WHAT YOU CAN DO. Use only the actions the request needs:
- edit: replace the tags of ONE selected block.
- merge: join SEVERAL selected blocks into one block, for example a sentence or paragraph that continues from one block into the next. The merged block takes the place of the first of them; the others are removed.
- insert: add a NEW block right after any block in the outline, for example a table. Use it when the request asks for something new that is not already in a selected block.
- delete: remove selected blocks.
- split: undo a merge. A block that shows parts= was made by merging those pieces; split puts the original blocks back.
If the request is only a question, or none of these actions can do it, give no action and answer in the reply.

HOW TO WORK
1. Understand the request. If it says something looks wrong, bad, or does not match the page, compare each selected block with its crop and with the page image, line by line, and fix every difference you can see: words, numbers, symbols, line breaks, lists, tables, alignment, formulas.
2. The page image is the truth. Never invent text. Every word, number and symbol you write must be printed on the page, or be exactly what the request asks you to type.
3. Keep the words of a block exactly as they are in its current tags, unless the request asks you to check, correct or re-read them against the image. Then change only what the image shows differently.
4. merge: the new tags must contain all the words of the merged blocks, in reading order, none added and none lost. Join the pieces so they read as one paragraph, list or table, and keep the first block's alignment and size unless the request says otherwise.
5. A block that already matches the page and the request gets no action. Each block id appears in at most one action.
6. A formula, fraction or mixed number that sits inside a line of text belongs inline between single dollar signs in the same <P>.
7. A block that shows parts= was made by merging those pieces, and its bbox covers them. EARLIER IN THIS SESSION lists what was asked and decided before: use it for follow-ups such as "the merged block" or "now centre it".
8. Pieces side by side on one row (one left of the other, same height) are ONE paragraph: the left piece first, then the right piece as plain text inside <RIGHT>...</RIGHT> at the end of the paragraph, so the gap sits between them (left-most and right-most). Pieces one after another: join them as one paragraph when the sentence continues, otherwise keep separate <P>s.
9. Write the tags with the reference below. Text inside tags is XML-escaped (&amp; &lt; &gt;); keep it escaped. Do not wrap the tags in <BLOCK>.

Answer in exactly this form and nothing else (no markdown fences). Leave out the actions you do not use:
<REPLY>One or two plain sentences: what you understood and what you propose, or the answer to the question.</REPLY>
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
