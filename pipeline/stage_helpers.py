# Stage file 'helpers': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── CELL 3: shared helpers ──────────────────────────────────────────────
# Preprocessing + sanitisers + cross-page continuation detector + font helper.

# ── preprocess: three modes, same signature as the previous notebook ──────
def _illumination_normalize(bgr, sigma=35):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    bg = cv2.GaussianBlur(gray, (0, 0), sigma)
    norm = cv2.divide(gray, bg, scale=192).clip(0, 255).astype(np.uint8)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lab[..., 0] = norm
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

def _clahe(bgr, clip=3.0):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

def render_page(pno):
    pix = doc[pno].get_pixmap(dpi=DPI, alpha=False)
    return cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)

def preprocess_page(pno, method="original"):
    bgr = render_page(pno)
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
    raise ValueError(f"Unknown preprocessing method: {method}")


# ── sanitisers: strip HTML tags and LaTeX math from OCR output ────────────
_HTML_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(text):
    if not text: return ""
    s = text
    s = re.sub(r"<br\s*/?>",   "\n", s, flags=re.I)
    s = re.sub(r"</tr\s*>",    "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]\s*>", "\n", s, flags=re.I)
    s = _HTML_TAG_RE.sub("", s)
    s = unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

_SUPER = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB   = str.maketrans("0123456789+-=()",  "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_LATEX_CMDS = {
    r"\alpha":"α", r"\beta":"β", r"\gamma":"γ", r"\delta":"δ", r"\epsilon":"ε",
    r"\theta":"θ", r"\lambda":"λ", r"\mu":"μ", r"\pi":"π", r"\rho":"ρ",
    r"\sigma":"σ", r"\tau":"τ", r"\phi":"φ", r"\omega":"ω",
    r"\Gamma":"Γ", r"\Delta":"Δ", r"\Theta":"Θ", r"\Lambda":"Λ",
    r"\Pi":"Π", r"\Sigma":"Σ", r"\Phi":"Φ", r"\Omega":"Ω",
    r"\infty":"∞", r"\times":"×", r"\cdot":"·", r"\div":"÷", r"\pm":"±",
    r"\leq":"≤", r"\geq":"≥", r"\neq":"≠", r"\approx":"≈",
    r"\rightarrow":"→", r"\partial":"∂", r"\sum":"∑", r"\int":"∫", r"\sqrt":"√",
    r"\degree":"°", r"\circ":"°",
    r"\overline":"", r"\bar":"", r"\hat":"", r"\vec":"",
    r"\text":"", r"\mathrm":"", r"\mathbf":"", r"\mathit":"",
    r"\left":"", r"\right":"",
    r"\,":" ", r"\;":" ", r"\!":"", r"\ ":" ",
}

def _sup(b):
    return b.translate(_SUPER) if b and all(c in "0123456789+-=()n" for c in b) else f"^({b})"
def _sub(b):
    return b.translate(_SUB)   if b and all(c in "0123456789+-=()"  for c in b) else f"_({b})"

def _flatten_latex(s):
    s = re.sub(r"(\d)\\frac", r"\1 \\frac", s)
    for k, v in _LATEX_CMDS.items(): s = s.replace(k, v)
    s = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1/\2", s)
    s = re.sub(r"\^\{([^{}]*)\}",  lambda m: _sup(m.group(1)), s)
    s = re.sub(r"\^([A-Za-z0-9])", lambda m: _sup(m.group(1)), s)
    s = re.sub(r"_\{([^{}]*)\}",   lambda m: _sub(m.group(1)), s)
    s = re.sub(r"_([A-Za-z0-9])",  lambda m: _sub(m.group(1)), s)
    s = re.sub(r"\\[A-Za-z]+", "", s)
    return s.replace("{", "").replace("}", "").strip()

def _strip_latex(text):
    if not text: return ""
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: _flatten_latex(m.group(1)), text, flags=re.S)
    text = re.sub(r"\$(.+?)\$",     lambda m: _flatten_latex(m.group(1)), text, flags=re.S)
    return text

def sanitize(text):
    return _strip_latex(_strip_html(text or ""))


# ── cross-page continuation detector (kept as a safety net) ───────────────
_TERMINAL_PUNCT_RE = re.compile(r"[.!?।॥:;\"')\]\}]\s*$")
_FURNITURE_RE = re.compile(
    r"^\s*("
    r"scanned\s+(by|with)\s+.+"
    r"|page\s+\d+(\s+of\s+\d+)?"
    r"|\[?\s*p\.?\s*t\.?\s*o\.?\s*\]?"
    r"|\d+-\d+-\d+"
    r"|\[\s*\]"
    r")\s*$", re.I)

_BODY_TYPES = {"paragraph", "list_item"}

def _is_real_body(region):
    if region.get("type") not in _BODY_TYPES: return False
    t = (region.get("text") or "").strip()
    if not t: return False
    return not _FURNITURE_RE.match(t)


# ── JSON parse helper (shared across stages) ──────────────────────────────
def _parse_json_object(text):
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"): lines = lines[1:]
        if lines and lines[-1].strip() == "```": lines = lines[:-1]
        s = "\n".join(lines)
    try: return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {}


# ── font helper (shared with DOCX render in Stage 3) ──────────────────────
_FONT_NAME = "Nirmala UI"

def _apply_font(run, name=_FONT_NAME):
    run.font.name = name
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts"); rPr.insert(0, rFonts)
        for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rFonts.set(qn(a), name)
    except Exception:
        pass


# ── small preview helpers used by diagnostic prints ───────────────────────
def _preview(t, n=70):
    t = (t or "").replace("\n", " ⏎ ")
    return t[:n]

def _show_block(tag, b, label=None):
    bb = b.get("bbox_xyxy")
    bb_s = "null" if bb is None else f"[{int(bb[0])},{int(bb[1])},{int(bb[2])},{int(bb[3])}]"
    lab = label if label is not None else b.get("_label", "?")
    print(f"    {tag:<10} {lab:>5}  type={b.get('type','?'):<14} "
          f"bbox={bb_s:<28} text={_preview(b.get('text'))!r}")

print("shared helpers loaded")

