# Stage file 'utils': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── CELL: shared utils for the non-Bodhan routes ──────────────────────────
import threading
_DOC_LOCK = threading.Lock()                 # PyMuPDF Document is not thread-safe: serialise page access
_render_page_unsafe = render_page
def render_page(pno):
    with _DOC_LOCK: return _render_page_unsafe(pno)
_PAGE_WH = {p: (doc[p].rect.width, doc[p].rect.height) for p in page_indices}

def _x(s):                       # escape text for the tag markup
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _img_uri(bgr, max_side=1600, q=85):
    h, w = bgr.shape[:2]; s = min(1.0, max_side / max(h, w))
    if s < 1: bgr = cv2.resize(bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()).decode()

def _or_chat(model, content, json_mode=False, timeout=240, tries=5, max_tokens=None):
    import random
    if not OPENROUTER_KEY: raise RuntimeError("OPENROUTER_API_KEY is not set")
    body = {"model": model, "temperature": 0.0, "messages": [{"role": "user", "content": content}]}
    if json_mode: body["response_format"] = {"type": "json_object"}
    if max_tokens: body["max_tokens"] = max_tokens
    last = None
    for a in range(tries):
        wait = min(40, 3 * 2 ** a) + random.uniform(0, 2)           # 3s, 6s, 12s, 24s, 40s (+jitter)
        try:
            r = requests.post(OPENROUTER_URL, headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                              json=body, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit(): wait = max(wait, min(60, int(ra)))
                last = RuntimeError(f"HTTP {r.status_code} from {model}"); time.sleep(wait); continue
            if r.status_code == 402: raise RuntimeError("HTTP 402: OpenRouter credits exhausted")
            r.raise_for_status(); data = r.json()
            if "error" in data: last = RuntimeError(str(data["error"])[:200]); time.sleep(wait); continue
            return data["choices"][0]["message"]["content"] or ""
        except requests.RequestException as e:
            last = e; time.sleep(wait)
    raise RuntimeError(f"openrouter failed after {tries} tries: {last}")

def _json_from(text):
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try: return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {}

_DROP_RE = re.compile(r"<DROP>.*?</DROP>", re.S)
def _strip_drop(markup): return _DROP_RE.sub("", markup or "").strip()

def _mkblock(bid, page, bbox_norm, btype, markup="", picture_path=None, wh=None):
    W, H = wh or (0, 0)
    bb = [round(bbox_norm[0]*W, 1), round(bbox_norm[1]*H, 1), round(bbox_norm[2]*W, 1), round(bbox_norm[3]*H, 1)] if bbox_norm else None
    return {"block_id": bid, "page": page, "bbox_xyxy": bb, "bbox_norm": bbox_norm, "bodhan_type": btype,
            "is_picture": bool(picture_path), "image_path": picture_path, "formatted_text": markup}

