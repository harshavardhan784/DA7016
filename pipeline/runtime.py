"""Runs the Infinite OCR pipeline from the stage files in this folder.

Each stage file holds the code of one cell of infinite_ocr.ipynb, unchanged. The notebook is kept as the
reference and is never read at run time. The stage files are not modules: they share one namespace and are
run in order, exactly as the notebook runs its cells (a stage uses the names the stages before it defined).

This is what ocr/nb.py used to do for the notebook: find the code, patch the two INPUT lines, build the script
that runs the whole pipeline in a job folder, and load the toolkit (everything except routing and the routes)
for rebuild, review and the assistant.
"""
import ast
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTEBOOK = HERE / "infinite_ocr.ipynb"          # the reference copy; nothing reads it at run time

# role -> stage file, in pipeline order
STAGES = {
    "config": "stage_config.py",
    "metrics": "stage_metrics.py",
    "helpers": "stage_helpers.py",
    "grammar": "stage_grammar.py",
    "utils": "stage_utils.py",
    "docx_a": "stage_docx_builder.py",
    "docx_b": "stage_docx_math.py",
    "fixers": "stage_fixers.py",
    "router": "stage_router.py",
    "digital": "stage_route_digital.py",
    "single": "stage_route_single.py",
    "indic_ocr": "stage_route_indic_ocr.py",
    "indic_fmt": "stage_route_indic_format.py",
    "final": "stage_final.py",
}
# What the full pipeline runs, in order (the final stage runs after the snapshot below).
PIPELINE = ["config", "metrics", "helpers", "grammar", "utils", "docx_a", "docx_b", "fixers",
            "router", "digital", "single", "indic_ocr", "indic_fmt"]
# What rebuild / review / the assistant need: everything except routing and the routes themselves.
TOOLKIT = ["config", "metrics", "helpers", "grammar", "utils", "docx_a", "docx_b", "fixers"]

# Route stages whose definitions (not their stage code) review reuses: role -> text where the stage code starts.
LIFT_UNTIL = {"indic_ocr": "reset_metrics()", "indic_fmt": "blocks_A"}

# Snapshot of the blocks just before the final stage runs. Reads ordered_blocks, changes nothing.
SNAPSHOT_HOOK = '''
Path("blocks_pre.json").write_text(json.dumps(
    {"route": ROUTE, "route_info": ROUTE_INFO, "models": M, "blocks": ordered_blocks},
    ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
'''


class PipelineError(RuntimeError):
    pass


def stage_path(role):
    try:
        path = HERE / STAGES[role]
    except KeyError:
        raise PipelineError(f"pipeline changed: no stage file for role '{role}'")
    if not path.is_file():
        raise PipelineError(f"pipeline changed: stage file {path.name} is missing")
    return path


def stage(role):
    """Source of the stage file that plays `role`."""
    return stage_path(role).read_text(encoding="utf-8")


def patch_input(src):
    """The only edit to a stage: the two INPUT lines, set to this job's PDF and all pages."""
    src, n1 = re.subn(r"(?m)^PDF_PATH\s*=.*$", 'PDF_PATH     = Path(os.environ["OCR_PDF"])', src)
    src, n2 = re.subn(r"(?m)^PAGES\s*=.*$", "PAGES        = -1", src)
    if (n1, n2) != (1, 1):
        raise PipelineError(f"INPUT block changed: PDF_PATH matched {n1}, PAGES matched {n2} (expected 1 each)")
    return src


def source(role):
    s = stage(role)
    return patch_input(s) if role == "config" else s


def run_script():
    """Python text that runs the whole pipeline in the current directory (used as a subprocess)."""
    parts = [f"# ===== stage: {r} =====\n{source(r)}\n" for r in PIPELINE]
    parts.append(f"# ===== snapshot before the final stage =====\n{SNAPSHOT_HOOK}\n")
    parts.append(f"# ===== stage: final =====\n{source('final')}\n")
    return "\n".join(parts)


def lift_defs(role):
    """Compiled definitions of an `if ROUTE == ...:` stage, without the stage code that follows them."""
    src = stage(role)
    tree = ast.parse(src)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.If):
        raise PipelineError(f"pipeline changed: stage '{role}' is no longer a single if-block")
    keep = []
    for st in tree.body[0].body:
        if (ast.get_source_segment(src, st) or "").startswith(LIFT_UNTIL[role]):
            break
        keep.append(st)
    else:
        raise PipelineError(f"pipeline changed: stage start '{LIFT_UNTIL[role]}' not found in stage '{role}'")
    return compile(ast.Module(body=keep, type_ignores=[]), str(stage_path(role)), "exec")


def load(roles=TOOLKIT, lift=()):
    """Run stage files into a fresh namespace and return it. Call from the job folder with OCR_PDF set."""
    ns = {"__name__": "ocr_pipeline"}
    for r in roles:
        exec(compile(source(r), str(stage_path(r)), "exec"), ns)
    for r in lift:
        exec(lift_defs(r), ns)
    return ns
