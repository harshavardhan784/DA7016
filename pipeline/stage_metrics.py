# Stage file 'metrics': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── CELL 2: metrics instrumentation (wraps requests.post) ────────────────
# Every API call to Bodhan / OpenRouter is logged with model, tokens, latency, cost.
# Call set_stage(method, stage) before each pipeline step so calls are attributed.

CALLS = []                       # one dict per HTTP call
_CTX  = {"method": "-", "stage": "-"}
_stage_t0 = {"method": "-", "stage": "-", "t": None}

def set_stage(method, stage):
    """Tag every HTTP call until the next set_stage(...) with (method, stage)."""
    global _stage_t0
    if _stage_t0["t"] is not None:
        CALLS.append({"method": _stage_t0["method"], "stage": _stage_t0["stage"],
                      "engine": "_wallclock_", "ok": True,
                      "tok_in": 0, "tok_out": 0, "cost": 0.0,
                      "seconds": time.perf_counter() - _stage_t0["t"]})
    _stage_t0.update(method=method, stage=stage, t=time.perf_counter())
    _CTX.update(method=method, stage=stage)

def reset_metrics():
    CALLS.clear(); _CTX.update(method="-", stage="-")
    _stage_t0.update(method="-", stage="-", t=None)
    print("[metrics] cleared")

def _extract(url, body_json, resp_json, dt, ok):
    model = "?"
    if isinstance(body_json, dict):
        model = body_json.get("model", model)
    if isinstance(resp_json, dict):
        model = resp_json.get("model") or model
    if model == "?" and "bodhan" in url.lower():
        model = "bodhan/indic-ocr"
    in_tok, out_tok, cost = 0, 0, None
    if isinstance(resp_json, dict):
        u = resp_json.get("usage") or {}
        in_tok  = u.get("prompt_tokens",     0) or 0
        out_tok = u.get("completion_tokens", 0) or 0
        if u.get("cost") is not None:
            cost = float(u["cost"])
    if cost is None:
        for key, (pin, pout) in PRICES_USD_PER_M.items():
            if key in model.lower():
                cost = (in_tok * pin + out_tok * pout) / 1_000_000
                break
    if cost is None and "bodhan" in model.lower() and BODHAN_USD_PER_PAGE is not None:
        cost = float(BODHAN_USD_PER_PAGE)
    return {"method": _CTX["method"], "stage": _CTX["stage"], "engine": model,
            "ok": ok, "tok_in": in_tok, "tok_out": out_tok,
            "cost": cost, "seconds": dt}

# install wrapper (idempotent — safe to re-run this cell)
if not getattr(requests.post, "_instrumented", False):
    _orig_post = requests.post

    def _tracked_post(url, *args, **kwargs):
        # ask OpenRouter to include cost in the usage block
        if "openrouter.ai" in url and isinstance(kwargs.get("json"), dict):
            _b = {**kwargs["json"], "usage": {"include": True}}
            if GEMINI_THINK_TOKENS is not None and "gemini" in str(_b.get("model", "")).lower() and "reasoning" not in _b:
                _b["reasoning"] = {"max_tokens": int(GEMINI_THINK_TOKENS)}
            kwargs["json"] = _b
        t0 = time.perf_counter()
        body = kwargs.get("json")
        try:
            resp = _orig_post(url, *args, **kwargs)
        except Exception as e:
            dt = time.perf_counter() - t0
            CALLS.append(_extract(url, body, None, dt, ok=False))
            raise
        dt = time.perf_counter() - t0
        try:    data = resp.json()
        except Exception: data = None
        CALLS.append(_extract(url, body, data, dt, ok=resp.ok))
        return resp

    _tracked_post._instrumented = True
    requests.post = _tracked_post
    print("[metrics] requests.post wrapped — call set_stage(method, stage) before each step")
else:
    print("[metrics] already wrapped — this cell is idempotent")

