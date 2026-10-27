"""Works out what a person means by a vague message, without calling a model.
Used only by the Fix formatting / Re-read / Delete shortcuts; "Ask anything" (mode auto) goes to the assistant in ops.assist instead.

People do not say "re-read" or "fix layout". They say "this is wrong", "check it against the page", "delete these two".
The result says what to do and, in plain words, what was understood, so the screen can show it and offer the other choices.
"""
import re

I = re.I
DELETE = re.compile(r"\b(delet\w*|remov\w*|eras\w*|discard\w*|trash\w*|get rid of|junk|useless|unwanted|clear (?:it|them|these|this|those)|(?:make|leave) (?:it|them|these|this|those) (?:blank|empty))\b", I)
PART_OF_BLOCK = re.compile(r"\b(spaces?|words?|letters?|characters?|commas?|dots?|brackets?|punctuation|lines?|sentences?|spelling|digits?|symbols?|bold|italic\w*|underlin\w*|colou?r|border|font|size|indent\w*|spacing|format\w*)\b", I)
THING = r"(?:words?|letters?|characters?|text|brackets?|numbers?|digits?|symbols?|signs?|marks?|punctuation|spelling)"
# clearly about the words themselves
STRONG = re.compile(r"(re-?read|read (?:it |this |them |these |those )?again|mis-?read|mis-?spell\w*|spelling|typos?|\bocr\b|"
                    rf"wrong {THING}|incorrect {THING}|missing {THING}|{THING} (?:is|are)\s+(?:missing|wrong|incorrect|different|not (?:there|same|right|matching)))", I)
# "check it against the page": could mean the words or the layout
WEAK = re.compile(r"(?:check|verify|compare|match)\w*(?:\s+\w+){0,3}?\s+(?:with|against|to)\s+(?:the\s+|this\s+)?(?:\w+\s+)?(?:image|scan|page|original|picture|photo)", I)
LAYOUT = re.compile(r"\b(format\w*|layout|align\w*|cent(?:er|re)\w*|left|right|justif\w*|bold|italic\w*|underlin\w*|head(?:ing|er)s?|title|lists?|bullet\w*|numbered|numbering|tables?|rows?|columns?|cells?|indent\w*|spacing|font|size|paragraphs?|merge|bigger|smaller|colou?r|border|marks?)\b", I)
FORMULA = re.compile(r"(\\[A-Za-z]{2,}|\$|\bformulas?\b|\bequations?\b|\blatex\b|\bmaths?\b)", I)

CHOICES = [{"mode": "format", "label": "Fix the layout"}, {"mode": "reread", "label": "Re-read from the image"}, {"mode": "delete", "label": "Delete these blocks"}]


def understand(note: str, mode: str = "auto") -> dict:
    """mode is what the person picked: 'auto', 'format' or 'reread'.
    Returns {"action": delete|reread|format|info|ask, "say": what was understood, "choices": [...]}."""
    note = (note or "").strip()
    if mode == "delete":
        return {"action": "delete", "say": "Deleting the selected blocks.", "choices": []}
    if DELETE.search(note):
        if PART_OF_BLOCK.search(note):
            return {"action": "ask", "choices": [c for c in CHOICES if c["mode"] != "delete"],
                    "say": "I can delete whole blocks. To remove part of a block (a word, a line, or its formatting), edit it directly or tell me what it should look like. "
                           "Or should I fix the layout, or read the blocks again from the image?"}
        return {"action": "delete", "say": "You want these blocks removed. Delete them?", "choices": []}
    if FORMULA.search(note) and mode != "reread":
        return {"action": "info", "choices": [CHOICES[1]],
                "say": "That is a formula. It shows here as math (for example 5 × 5 = 25) and the Word file writes it as a real equation, so there is nothing to fix in the layout. "
                       "If its numbers or symbols are wrong, I can read it again from the image."}
    if mode in ("format", "reread"):
        return {"action": mode, "say": "", "choices": []}
    strong, weak, layout = bool(STRONG.search(note)), bool(WEAK.search(note)), bool(LAYOUT.search(note))
    if strong or (weak and not layout):
        return {"action": "reread", "choices": [CHOICES[0]], "say": "Reading these blocks again from the image, because the words look wrong or you asked me to check them against the page."}
    if layout:
        return {"action": "format", "choices": [CHOICES[1]], "say": "Fixing the layout of these blocks and keeping every word."}
    return {"action": "ask", "choices": CHOICES,
            "say": "I am not sure what you want for these blocks. I can fix the layout (lists, tables, alignment), read them again from the image, or delete them. Which one?"}
