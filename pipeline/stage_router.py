# Stage file 'router': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# # ─── CELL: ROUTER — follows the tree ──────────────────────────────────────
# DIGITAL_MIN_CHARS_PER_PAGE = 120     # median text chars/page to call a PDF "digital"
# DIGITAL_MIN_PAGE_SHARE     = 0.6     # share of sampled pages that must have a text layer
# ENGLISH_MIN_LATIN_SHARE    = 0.92    # share of letters that are plain Latin to call digital text "English"
# SCRIPT_PROBE_PAGES         = 2       # scanned: pages sent to the cheap script probe
# ENGLISH_HINDI_OK = {"english", "hindi"}

# def _digital_stats():
#     pcs, lat = [], []
#     for p in page_indices:
#         t = doc[p].get_text()
#         pcs.append(len(t.strip()))
#         L = [ch for ch in t if ch.isalpha()]
#         lat.append((sum(ch.isascii() for ch in L), len(L)))
#     a, b = sum(x for x, _ in lat), sum(y for _, y in lat)
#     share_txt = sum(c >= 30 for c in pcs) / len(pcs)
#     return statistics.median(pcs), share_txt, (a / b if b else 0.0)

# def _probe_scripts():
#     if not OPENROUTER_KEY: return None
#     # content = [{"type": "text", "text": 'List every writing script (not language) that appears as real text on these document pages, '
#     #                                     'e.g. Latin, Devanagari, Telugu, Tamil, Kannada, Malayalam, Bengali, Gujarati, Gurmukhi, Odia, Arabic. '
#     #                                     'Ignore tiny stamps/watermarks. Return ONLY JSON: {"scripts": ["..."]}'}]
#     content = [{
#         "type": "text", 
#         "text": 'Look at these document pages. List every writing script (such as English, Hindi, Marathi, Telugu, Tamil, Kannada, etc.) found in the text. '
#                 'Ignore watermarks. You must respond with a JSON object containing a single key "scripts" whose value is a list of strings. Example: {"scripts": ["English", "telugu"]}'
#     }]
    
#     for p in page_indices[:SCRIPT_PROBE_PAGES]:
#         content.append({"type": "image_url", "image_url": {"url": _img_uri(render_page(p), max_side=900, q=70)}})
#     models = [M["classify"]] + [m for m in MODEL_OPTIONS["classify"] if m != M["classify"]]
#     for m in models:
#         try:
#             return [x.strip().lower() for x in (_json_from(_or_chat(m, content, json_mode=True, max_tokens=200, tries=3)).get("scripts") or [])]
#         except Exception as e:
#             print(f"  script probe failed on {m}: {str(e)[:120]}")
#     print("  script probe failed on every model -> defaulting to the Indic route (set FORCE_ROUTE to override)")
#     return None

# def decide_route():
#     med, share, latin = _digital_stats()
#     digital = med >= DIGITAL_MIN_CHARS_PER_PAGE and share >= DIGITAL_MIN_PAGE_SHARE
#     info = {"median_chars_per_page": med, "pages_with_text": round(share, 2), "latin_share": round(latin, 3)}
#     if digital:
#         route = "digital_english" if latin >= ENGLISH_MIN_LATIN_SHARE else "single_gemini"
#         info["kind"] = "digital · " + ("English" if route == "digital_english" else "non-English")
#     else:
#         scripts = _probe_scripts()
#         info["scripts"] = scripts
#         if scripts is None:
#             route = "scanned_indic"; info["kind"] = "scanned · script probe unavailable → default Indic route"
#         elif set(scripts) <= ENGLISH_HINDI_OK:
#             route = "single_gemini"; info["kind"] = "scanned · English/Hindi"
#         else:
#             route = "scanned_indic"; info["kind"] = "scanned · Indic"
#     return route, info

# ROUTE, ROUTE_INFO = decide_route()
# if FORCE_ROUTE: ROUTE = FORCE_ROUTE; ROUTE_INFO["forced"] = True
# print("ROUTE =", ROUTE, "\n", json.dumps(ROUTE_INFO, indent=1))

# ─── CELL: ROUTER — follows the tree ──────────────────────────────────────
DIGITAL_MIN_CHARS_PER_PAGE = 120     # median text chars/page to call a PDF "digital"
DIGITAL_MIN_PAGE_SHARE     = 0.6     # share of sampled pages that must have a text layer
ENGLISH_MIN_LATIN_SHARE    = 0.92    # share of letters that are plain Latin to call digital text "English"
SCRIPT_PROBE_PAGES         = 3       # scanned: pages sent to the cheap probe (spread evenly over the selected pages)
ENGLISH_HINDI_OK = {"english", "hindi"}
_SCRIPT_ALIAS    = {"latin": "english", "english": "english", "hindi": "hindi", "devanagari": "hindi"}   # names the probe may return
_IGNORE_SCRIPTS  = {"numerals", "numeral", "digits", "digit", "numbers", "number", "symbols", "punctuation", ""}
COMPLEX_BEATS_MATH = True            # scanned page that is BOTH complex and math-heavy: True -> scanned_indic, False -> single_gemini

def _digital_stats():
    pcs, lat = [], []
    for p in page_indices:
        t = doc[p].get_text()
        pcs.append(len(t.strip()))
        L = [ch for ch in t if ch.isalpha()]
        lat.append((sum(ch.isascii() for ch in L), len(L)))
    a, b = sum(x for x, _ in lat), sum(y for _, y in lat)
    share_txt = sum(c >= 30 for c in pcs) / len(pcs)
    return statistics.median(pcs), share_txt, (a / b if b else 0.0)

def _probe_pages():
    n, k = len(page_indices), min(SCRIPT_PROBE_PAGES, len(page_indices))
    if k >= n: return list(page_indices)
    if k <= 1: return [page_indices[0]]
    return [page_indices[round(i * (n - 1) / (k - 1))] for i in range(k)]

_PROBE_PROMPT = (
    'Look at these scanned document pages and answer with ONE JSON object with exactly these keys:\n'
    '"scripts": list of the writing scripts/languages of the real text, e.g. ["english", "hindi", "telugu", "tamil", "kannada", "marathi"]. '
    'Do NOT list numerals or symbols here. Ignore watermarks and tiny stamps.\n'
    '"has_math": true if ANY page contains mathematical formulas or equations, chemical equations or structure diagrams, or physics/electronics expressions; else false.\n'
    '"numbers_heavy": true if digits/numerals make up more than about 10% of the text (number tables, data columns, calculations); else false.\n'
    '"complex_layout": true ONLY if a page is clearly complex: figures, diagrams, graphs, maps or circuit drawings, dense or nested tables, '
    'multi-column layout, handwriting or stamps over the text, or a poor/skewed scan; false for plain text pages (headings, paragraphs, numbered questions, simple lists).\n'
    'Example: {"scripts": ["english"], "has_math": false, "numbers_heavy": false, "complex_layout": false}'
)

def _as_bool(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")

def _probe_scripts():
    """Returns {"scripts": [...], "has_math": bool, "numbers_heavy": bool, "complex_layout": bool} or None if every model failed."""
    if not OPENROUTER_KEY: return None
    content = [{"type": "text", "text": _PROBE_PROMPT}]
    for p in _probe_pages():
        content.append({"type": "image_url", "image_url": {"url": _img_uri(render_page(p), max_side=900, q=70)}})
    models = [M["classify"]] + [m for m in MODEL_OPTIONS["classify"] if m != M["classify"]]
    for m in models:
        try:
            d = _json_from(_or_chat(m, content, json_mode=True, max_tokens=250, tries=3)) or {}
            return {"scripts": [str(x).strip().lower() for x in (d.get("scripts") or [])],
                    "has_math": _as_bool(d.get("has_math", False)),
                    "numbers_heavy": _as_bool(d.get("numbers_heavy", False)),
                    "complex_layout": _as_bool(d.get("complex_layout", False))}
        except Exception as e:
            print(f"  script probe failed on {m}: {str(e)[:120]}")
    print("  script probe failed on every model -> defaulting to the Indic route (set FORCE_ROUTE to override)")
    return None

def decide_route():
    med, share, latin = _digital_stats()
    digital = med >= DIGITAL_MIN_CHARS_PER_PAGE and share >= DIGITAL_MIN_PAGE_SHARE
    info = {"median_chars_per_page": med, "pages_with_text": round(share, 2), "latin_share": round(latin, 3)}
    if digital:
        route = "digital_english" if latin >= ENGLISH_MIN_LATIN_SHARE else "single_gemini"
        info["kind"] = "digital · " + ("English" if route == "digital_english" else "non-English")
        return route, info

    probe = _probe_scripts()
    info["probe"] = probe
    if probe is None:
        return "scanned_indic", {**info, "kind": "scanned · probe unavailable → default Indic route"}

    names  = {_SCRIPT_ALIAS.get(s, s) for s in (str(x).strip().lower() for x in probe["scripts"]) if s not in _IGNORE_SCRIPTS}
    pure   = bool(names) and names <= ENGLISH_HINDI_OK                    # only English and/or Hindi (Latin / numerals are folded in)
    mathy  = probe["has_math"] or probe["numbers_heavy"]
    cplx   = probe["complex_layout"]
    info["scripts"] = sorted(names)

    if cplx or mathy:
        why = "complex page" if cplx else "math / equations / chemistry / many numbers"
        return "scanned_indic", {**info, "kind": f"scanned · {why} → Indic route"}
    if pure:
        return "single_gemini", {**info, "kind": "scanned · pure English/Hindi"}
    return "scanned_indic",     {**info, "kind": "scanned · Indic / other script"}

ROUTE, ROUTE_INFO = decide_route()
if FORCE_ROUTE: ROUTE = FORCE_ROUTE; ROUTE_INFO["forced"] = True
print("ROUTE =", ROUTE, "\n", json.dumps(ROUTE_INFO, indent=1))
