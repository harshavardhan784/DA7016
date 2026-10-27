# Stage file 'docx_b': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ── 1. symbol tables ───────────────────────────────────────────────────────────────────────────────────
_GREEK = ("alpha α beta β gamma γ delta δ epsilon ε varepsilon ε zeta ζ eta η theta θ vartheta ϑ iota ι kappa κ lambda λ mu μ nu ν xi ξ pi π rho ρ "
          "sigma σ varsigma ς tau τ upsilon υ phi φ varphi φ chi χ psi ψ omega ω Gamma Γ Delta Δ Theta Θ Lambda Λ Xi Ξ Pi Π Sigma Σ Upsilon Υ Phi Φ Psi Ψ Omega Ω").split()
_LATEX_SYM = dict(zip(_GREEK[::2], _GREEK[1::2]))
_LATEX_SYM.update({
    "times": "×", "div": "÷", "pm": "±", "mp": "∓", "cdot": "·", "cdots": "⋯", "ldots": "…", "dots": "…", "ast": "∗", "star": "⋆", "circ": "°", "degree": "°",
    "bullet": "•", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "sim": "∼", "simeq": "≃", "cong": "≅", "equiv": "≡",
    "propto": "∝", "infty": "∞", "partial": "∂", "nabla": "∇", "int": "∫", "iint": "∬", "oint": "∮", "sum": "∑", "prod": "∏", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔", "uparrow": "↑",
    "downarrow": "↓", "mapsto": "↦", "therefore": "∴", "because": "∵", "angle": "∠", "perp": "⊥", "parallel": "∥", "triangle": "△", "square": "□",
    "checkmark": "✓", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨", "setminus": "∖", "hbar": "ħ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ",
    "aleph": "ℵ", "prime": "′", "dagger": "†", "S": "§", "P": "¶", "copyright": "©", "pounds": "£", "euro": "€", "rupee": "₹", "textdegree": "°", "textbullet": "•",
    "micro": "µ", "Angstrom": "Å", "AA": "Å", "lbrace": "{", "rbrace": "}", "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "vert": "|", "mid": "|", "lvert": "|", "rvert": "|", "Vert": "‖", "ll": "≪", "gg": "≫", "oplus": "⊕", "otimes": "⊗", "odot": "⊙", "quad": " ", "qquad": "  ",
    "nless": "≮", "ngtr": "≯", "nmid": "∤", "ldotp": ".", "cdotp": "·"})
_LATEX_DROP = set("left right big Big bigg Bigg displaystyle textstyle scriptstyle limits nolimits nonumber notag centering newline break".split())
_FUNCS = set("sin cos tan cot sec csc sinh cosh tanh arcsin arccos arctan log ln exp lim max min sup inf det dim gcd mod Pr".split())
_KNOWN_CMDS = set(_LATEX_SYM) | _FUNCS | _LATEX_DROP | set("frac dfrac tfrac sqrt text textbf textit mathrm mathbf mathit mathbb mathcal operatorname overline bar vec hat tilde dot ddot binom begin end".split())
_SUPCH = dict(zip("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")); _BBCH = dict(zip("RNZQCHP", "ℝℕℤℚℂℍℙ"))
_ACCENT = {"vec": "\u20d7", "hat": "\u0302", "tilde": "\u0303", "dot": "\u0307", "ddot": "\u0308", "bar": "\u0304", "overline": "\u0304"}
_RULE = re.compile(r"[\s*\-_=~.·•—–#+|/\\]{8,}")                    # ruler / separator-only paragraph

def _par(x):
    x = x.strip(); return x if re.fullmatch(r"[\w.°′]+", x) else f"({x})"

def _strip_braces(s):
    """drop grouping braces, but keep the braces of ^{..} / _{..} groups (the script splitter needs them)"""
    out, keep = [], []
    for i, ch in enumerate(s):
        if ch == "{":
            k = i > 0 and s[i - 1] in "^_"; keep.append(k)
            if k: out.append(ch)
        elif ch == "}":
            if keep and keep.pop(): out.append(ch)
        else: out.append(ch)
    return "".join(out)

def _env(m):
    name, body = m.group(1).rstrip("*"), m.group(2)
    rows = [[c.strip() for c in r.split("&")] for r in re.split(r"\\\\", body) if r.strip()]
    if name == "cases": return "{ " + "; ".join(", ".join(r) for r in rows) + " }"
    inner = "; ".join(" ".join(r) for r in rows)
    l, r = {"pmatrix": "()", "bmatrix": "[]", "Bmatrix": "{}", "vmatrix": "||", "Vmatrix": "‖‖"}.get(name, ("", ""))
    return f"{l}{inner}{r}" if l else inner

def _latex_to_plain(s):
    """LaTeX -> readable text. ^{..} / _{..} groups are kept for _script_split (real super/subscript runs)."""
    s = re.sub(r"\\begin\{(\w+\*?)\}(.*?)\\end\{\1\}", _env, s, flags=re.S)
    for _ in range(8):                                                   # innermost-first
        s = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", lambda m: f"{_par(m.group(1))}/{_par(m.group(2))}", s)
        s = re.sub(r"\\binom\s*\{([^{}]*)\}\s*\{([^{}]*)\}", lambda m: f"C({m.group(1)},{m.group(2)})", s)
        s = re.sub(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}", lambda m: "".join(_SUPCH.get(c, c) for c in m.group(1)) + "√" + _par(m.group(2)), s)
        s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", lambda m: "√" + _par(m.group(1)), s)
        s = re.sub(r"\\sqrt\s+([A-Za-z0-9])", r"√\1", s)
        s = re.sub(r"\\mathbb\s*\{([A-Za-z])\}", lambda m: _BBCH.get(m.group(1), m.group(1)), s)
        s = re.sub(r"\\(vec|hat|tilde|dot|ddot|bar|overline)\s*\{([^{}]*)\}", lambda m: "".join(c + _ACCENT[m.group(1)] for c in m.group(2)[:3]) if len(m.group(2)) <= 3 else m.group(2), s)
        s = re.sub(r"\\(?:text|textbf|textit|textrm|mathrm|mathbf|mathit|mathsf|mathtt|mathcal|operatorname|boldsymbol|bm|mbox|hbox|underline|emph)\s*\{([^{}]*)\}", r"\1", s)
        s = re.sub(r"\\(?:phantom|hspace|vspace|label|tag)\s*\{[^{}]*\}", "", s)
    s = re.sub(r"\\([%&#_${}])", r"\1", s)                               # escaped literals
    s = re.sub(r"\\[,;:! ]", " ", s).replace("\\\\", " ")
    s = re.sub(r"\\([A-Za-z]+)", lambda m: "" if m.group(1) in _LATEX_DROP else _LATEX_SYM.get(m.group(1), m.group(1)), s)
    return re.sub(r"[ \t]+", " ", _strip_braces(s)).strip()

# ── 2. real superscript / subscript runs:  10^{-3}  x^2  N_0  a_{ij}  (snake_case words and URLs are left alone) ──
_SCRIPT_RE = re.compile(r"([\^_])(?:\{([^{}]{1,16})\}|([+\-]?\d{1,3}|[A-Za-z]))")
def _script_split(s):
    out, pos = [], 0
    for m in _SCRIPT_RE.finditer(s):
        op, br, tok = m.group(1), m.group(2), m.group(3); left = s[:m.start()]
        if m.start() < pos or not left or not (left[-1].isalnum() or left[-1] in ")]}°′"): continue
        if op == "_":
            if left[-1].isalpha() and len(left) > 1 and left[-2].isalpha(): continue              # snake_case / words
            if br is None and (m.end() < len(s) and (s[m.end()].isalnum() or s[m.end()] in "@/.")): continue
        out.append((s[pos:m.start()], None)); out.append(((br if br is not None else tok), "superscript" if op == "^" else "subscript")); pos = m.end()
    out.append((s[pos:], None)); return [(t, f) for t, f in out if t]

# ── 3. inline html / markdown remnants → runs ──────────────────────────────────────────────────────────
_HTML_TOK = re.compile(r"(<br\s*/?>|</?(?:b|strong|i|em|u|sup|sub|s|strike|del)\s*>)", re.I)
_TAG2FLAG = {"b": "bold", "strong": "bold", "i": "italic", "em": "italic", "u": "underline", "sup": "superscript", "sub": "subscript", "s": "strike", "strike": "strike", "del": "strike"}
_MD_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S); _MD_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_MD_ITAL = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_JUNK_TAGS = re.compile(r"</?(?:span|div|font|a|ul|ol|li|p|img|table|tr|td|th|tbody|thead|center|h[1-6]|mark|code|pre)\b[^>]*>", re.I)

def _rich_runs(t):
    t = _MD_BOLD.sub(r"<b>\1</b>", t); t = _MD_STRIKE.sub(r"<s>\1</s>", t); t = _MD_ITAL.sub(r"<i>\1</i>", t)
    out, depth = [], {}
    for piece in _HTML_TOK.split(t):
        if not piece: continue
        m = re.fullmatch(r"<(/?)(\w+)\s*/?>", piece) if piece[0] == "<" else None
        if m:
            tag = m.group(2).lower()
            if tag == "br": out.append(("\n", frozenset())); continue
            f = _TAG2FLAG[tag]; depth[f] = max(0, depth.get(f, 0) + (-1 if m.group(1) else 1)); continue
        act = {k for k, v in depth.items() if v > 0}
        for txt, sc in _script_split(piece): out.append((txt, frozenset(act | ({sc} if sc else set()))))
    return out

# ── 4. text clean-up ───────────────────────────────────────────────────────────────────────────────────
_MOJI = re.compile(r"(?:Ã[\x80-\xbf]|â€|Â[°±²³µ·º»¼½¾])")
_LIGA = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
def _clean_text(t):
    if _MOJI.search(t):                                                   # UTF-8 that was decoded as cp1252/latin-1
        for enc in ("cp1252", "latin-1"):
            try: t = t.encode(enc).decode("utf-8"); break
            except Exception: pass
    if re.search(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]{2,8});", t): t = _html.unescape(t)       # double-escaped entities
    t = re.sub("[\u200b\u200c\u200d\u2060\ufeff\u00ad]", "", t).replace("\u00a0", " ")
    for k, v in _LIGA.items(): t = t.replace(k, v)
    return unicodedata.normalize("NFC", _JUNK_TAGS.sub("", t))

def _unliteral(t):
    """literal backslash-n / -t / -r that are not LaTeX commands (\\neq, \\nu, \\times, \\theta …):  3\\nrd -> 3rd,  'Hr\\nPART' -> space"""
    t = re.sub(r"(?<=\d)\\n(?=(?:st|nd|rd|th)\b)", "", t)
    return re.sub(r"\\([ntr])([A-Za-z]*)", lambda m: m.group(0) if (m.group(1) + m.group(2)) in _KNOWN_CMDS else " " + m.group(2), t)

_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
_LATEXY = re.compile(r"\\[A-Za-z]{2,}|\\[%&#_{}$,;:! ]|[\^_]\{")
def _add_plain(piece, style, out):
    piece = _unliteral(piece.replace("\x01", "$"))
    if _LATEXY.search(piece): piece = _latex_to_plain(piece)             # bare LaTeX outside $…$
    for txt, flags in _rich_runs(piece):
        if txt == "\n": out.append(("br", None, style)); continue
        txt = re.sub(r"[ \t\r\n]+", " ", txt)
        if not txt: continue
        st = dict(style)
        for f in flags: st[f] = True
        out.append(("text", txt, st))

def _flatten_text(t, style, out):
    t = _clean_text(_XML_BAD_RE.sub("", t)).replace("\\$", "\x01")
    pos = 0
    for m in _MATH_RE.finditer(t):
        body = next(g for g in m.groups() if g is not None)
        if m.group(2) is not None and not re.search(r"[\\^_{}=+\-*/<>()]|^\s*[A-Za-z]\s*$", body): continue      # "$5 and $10" is money
        _add_plain(t[pos:m.start()], style, out)
        out.append(("math", (body.strip().replace("\x01", "\\$"), m.group(1) is not None or m.group(4) is not None), style)); pos = m.end()
    _add_plain(t[pos:], style, out)

def _inline_to_text(t):                                                   # for <RIGHT>…</RIGHT>: no runs, just readable text
    t = _clean_text(t)
    t = _MATH_RE.sub(lambda m: _latex_to_plain(next(g for g in m.groups() if g is not None)), t)
    return re.sub(r"\s+", " ", _latex_to_plain(_unliteral(t)) if _LATEXY.search(t) else _unliteral(t)).strip()

# ── 5. math: native Word equation (OMML) when the libs exist, else text with real sup/subscripts ───────
_OMML_CACHE, _MATH_LIB = {}, {"ok": None}
_OM = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
def _latex_to_omml(latex):
    if latex in _OMML_CACHE: return _OMML_CACHE[latex]
    el = None
    if _MATH_LIB["ok"] is not False:
        try:
            import latex2mathml.converter as _L, mathml2omml as _M
            from lxml import etree as _et
            el = _et.fromstring('<w xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">' + _M.convert(_L.convert(latex)) + '</w>')[0]
            for bx in reversed(list(el.iter(_OM + "box"))):                # flatten <m:box><m:e>…</m:e></m:box> (same shape as pandoc's OMML)
                pa, e = bx.getparent(), bx.find(_OM + "e")
                if pa is None: continue
                i = list(pa).index(bx); pa.remove(bx)
                for k, ch in enumerate(list(e) if e is not None else []): pa.insert(i + k, ch)
            _MATH_LIB["ok"] = True
        except ImportError:
            _MATH_LIB["ok"] = False
            print("  ℹ latex2mathml / mathml2omml not installed -> formulas are written as text (pip install latex2mathml mathml2omml for Word equations)")
        except Exception:
            el = None
    _OMML_CACHE[latex] = el
    return el

def _add_math(p, latex, display, st):
    el = _latex_to_omml(latex)
    if el is not None:
        try: p._p.append(copy.deepcopy(el)); return
        except Exception: pass                                            # fall through to readable text
    for txt, flags in _rich_runs(_latex_to_plain(latex)):                 # fallback: readable text, real super/subscripts
        if txt == "\n": p.add_run().add_break(); continue
        r = p.add_run(txt); _apply_font(r); s2 = dict(st)
        for f in flags: s2[f] = True
        _apply_run_style(r, s2)

# ── 6. overrides of the builder functions ──────────────────────────────────────────────────────────────
def _flatten_inline(nodes, style, out):
    for n in nodes:
        if isinstance(n, str):
            _flatten_text(n, style, out)
        elif n.tag == "STYLE":
            _flatten_inline(n.children, _style_from_attrs(n.attrs, style), out)
        elif n.tag in ("IMG", "FORMULA"):
            if n.attrs.get("bbox"): out.append(("img", n.attrs["bbox"], style))
        elif n.tag == "RIGHT":
            tx = _inline_to_text("".join(c for c in n.children if isinstance(c, str)))
            if tx: out.append(("right", tx, style))
        elif n.tag == "BR":
            out.append(("br", None, style))
        elif n.tag == "BLANK":
            out.append(("blank", n.attrs, style))
        else:                                                              # SPAN and any other known wrapper: just its content
            _flatten_inline(n.children, style, out)
    return out

def _emit_segments(p, segs):
    if segs and all(k == "math" for k, _, _ in segs) and any(v[1] for _, v, _ in segs) and p.alignment is None:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER                             # display equation alone on its line
    for kind, val, st in segs:
        if kind == "math": _add_math(p, val[0], val[1], st); continue
        if kind == "br": p.add_run().add_break(); continue
        if kind == "img": _add_inline_img(p, val); continue
        if kind == "right": _add_right(p, val, st); continue
        r = p.add_run(_blank_text(val) if kind == "blank" else val)
        _apply_font(r); _apply_run_style(r, st)

def _render_p(docx, node, hanging=None, left_default=None):
    attrs = node.attrs
    segs = _trim_segments(_flatten_inline(node.children, _style_from_attrs(attrs, {}), []))
    if not segs: return None
    if all(k in ("text", "br") for k, _, _ in segs) and _RULE.fullmatch("".join(v for k, v, _ in segs if k == "text").strip() or "x"): return None   # *****, -----, ......
    if segs[0][0] == "text" and re.match(r"#{1,6}\s+", segs[0][1]):         # markdown header marks
        segs[0] = ("text", re.sub(r"^#{1,6}\s+", "", segs[0][1]), segs[0][2])
    lvl = _heading_level(attrs)
    if lvl:
        p = docx.add_heading("", level=lvl)
        segs = [(k, v, {**{"color": "000000", "bold": False}, **st}) for k, v, st in segs]
    else:
        p = docx.add_paragraph()
    _apply_para_format(p, attrs, hanging, left_default)
    _emit_segments(p, segs)
    return p

