"""Runs the Infinite OCR notebook without copying any of its logic.

The notebook (pipeline/infinite_ocr.ipynb) is the only place the pipeline lives.
Cells are found by what they define, not by position, so adding or moving cells
does not break anything; if a cell this module relies on is missing or ambiguous
it fails with a clear message instead of running the wrong code.
"""
import ast
import json
import re
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parent.parent / "pipeline" / "infinite_ocr.ipynb"

# role -> regex that must match exactly one code cell. Listed in notebook order.
ROLES = {
    "config":    r"(?m)^# ── INPUT ──",
    "metrics":   r"(?m)^def set_stage\(",
    "helpers":   r"(?m)^def preprocess_page\(",
    "grammar":   r"(?m)^TAG_GRAMMAR\s*=",
    "utils":     r"(?m)^def _or_chat\(",
    "docx_a":    r"(?m)^def build_docx_with_blocks\(",
    "docx_b":    r"(?m)^def _latex_to_omml\(",
    "fixers":    r"(?m)^def fix_blocks\(",
    "router":    r"(?m)^ROUTE, ROUTE_INFO = decide_route\(\)",
    "digital":   r"(?m)^def run_digital_english\(",
    "single":    r"(?m)^def run_single_gemini\(",
    "indic_ocr": r"(?m)^\s+def _stage1_page\(",
    "indic_fmt": r"(?m)^\s+def _format_chunk\(",
    "final":     r"(?m)^ordered_blocks = fix_blocks\(ordered_blocks\)",
}
# What the full pipeline executes, in notebook order (cells 19 and 23 are diagnostics / a self-test).
PIPELINE = ["config", "metrics", "helpers", "grammar", "utils", "docx_a", "docx_b", "fixers",
            "router", "digital", "single", "indic_ocr", "indic_fmt"]
# What rebuild / review need: everything except routing and the routes themselves.
TOOLKIT = ["config", "metrics", "helpers", "grammar", "utils", "docx_a", "docx_b", "fixers"]

# Route cells whose definitions (not their stage code) review reuses: role -> text where the stage code starts.
LIFT_UNTIL = {"indic_ocr": "reset_metrics()", "indic_fmt": "blocks_A"}

_MAGIC = re.compile(r"^\s*(!|%%?[A-Za-z])")

# Snapshot of the blocks just before cell 22 runs. Reads ordered_blocks, changes nothing.
SNAPSHOT_HOOK = '''
Path("blocks_pre.json").write_text(json.dumps(
    {"route": ROUTE, "route_info": ROUTE_INFO, "models": M, "blocks": ordered_blocks},
    ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
'''


class NotebookError(RuntimeError):
    pass


def _cells(path=NOTEBOOK):
    nb = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            out.append("\n".join(l for l in "".join(c["source"]).split("\n") if not _MAGIC.match(l)))
    return out


def cell(role, path=NOTEBOOK):
    """Source of the one cell that plays `role`."""
    hits = [s for s in _cells(path) if re.search(ROLES[role], s)]
    if len(hits) != 1:
        raise NotebookError(f"notebook changed: role '{role}' matches {len(hits)} cells (expected 1)")
    return hits[0]


def patch_input(src):
    """The only edit to the notebook text: the two INPUT lines, set to this job's PDF and all pages."""
    src, n1 = re.subn(r"(?m)^PDF_PATH\s*=.*$", 'PDF_PATH     = Path(os.environ["OCR_PDF"])', src)
    src, n2 = re.subn(r"(?m)^PAGES\s*=.*$", "PAGES        = -1", src)
    if (n1, n2) != (1, 1):
        raise NotebookError(f"INPUT block changed: PDF_PATH matched {n1}, PAGES matched {n2} (expected 1 each)")
    return src


def source(role, path=NOTEBOOK):
    s = cell(role, path)
    return patch_input(s) if role == "config" else s


def run_script(path=NOTEBOOK):
    """Python text that runs the whole pipeline in the current directory (used as a subprocess)."""
    parts = [f"# ===== notebook role: {r} =====\n{source(r, path)}\n" for r in PIPELINE]
    parts.append(f"# ===== snapshot before the final cell =====\n{SNAPSHOT_HOOK}\n")
    parts.append(f"# ===== notebook role: final =====\n{source('final', path)}\n")
    return "\n".join(parts)


def lift_defs(role, path=NOTEBOOK):
    """Compiled definitions of an `if ROUTE == ...:` cell, without the stage code that follows them."""
    src = cell(role, path)
    tree = ast.parse(src)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.If):
        raise NotebookError(f"notebook changed: role '{role}' is no longer a single if-block")
    keep = []
    for st in tree.body[0].body:
        if (ast.get_source_segment(src, st) or "").startswith(LIFT_UNTIL[role]):
            break
        keep.append(st)
    else:
        raise NotebookError(f"notebook changed: stage start '{LIFT_UNTIL[role]}' not found in role '{role}'")
    return compile(ast.Module(body=keep, type_ignores=[]), f"<notebook:{role}>", "exec")


def load(roles=TOOLKIT, lift=(), path=NOTEBOOK):
    """Execute notebook cells into a fresh namespace and return it. Call from the job folder with OCR_PDF set."""
    ns = {"__name__": "ocr_notebook"}
    for r in roles:
        exec(compile(source(r, path), f"<notebook:{r}>", "exec"), ns)
    for r in lift:
        exec(lift_defs(r, path), ns)
    return ns
