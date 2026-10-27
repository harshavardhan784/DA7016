# Stage file 'indic_ocr': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
if ROUTE == "scanned_indic":
    # ─── CELL 4: Stage 1 — Bodhan OCR + Python dedup + labeled image ─────────

    # ── OCR primary + fallback ───────────────────────────────────────────────
    def _b64_png(bgr):
        return base64.b64encode(cv2.imencode(".png", bgr)[1].tobytes()).decode()

    import random, threading
    BODHAN_ATTEMPTS = 5
    BODHAN_JPEG     = True      # smaller upload, SAME pixel size (bbox coordinates unchanged). Set False to go back to PNG.
    _BODHAN_COOLDOWN = {"until": 0.0}      # shared by all worker threads

    def _img_b64(bgr):
        if BODHAN_JPEG:
            return "image/jpeg", base64.b64encode(cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()).decode()
        return "image/png", _b64_png(bgr)

    def _call_bodhan(bgr, api_key, max_tokens=8192, max_retries=BODHAN_ATTEMPTS):
        mime, b64 = _img_b64(bgr)
        payload = {
            "model": "indic-ocr",
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            ]}],
            "max_tokens": max_tokens,
        }
        for attempt in range(max_retries):
            wait = _BODHAN_COOLDOWN["until"] - time.time()
            if wait > 0: time.sleep(wait + random.uniform(0, 1))
            r = requests.post(BODHAN_URL, headers={"Authorization": f"Bearer {api_key}"},
                              json=payload, timeout=(10, 300))
            if r.status_code == 429 and attempt < max_retries - 1:
                ra = r.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(30, 4 * 2 ** attempt)
                _BODHAN_COOLDOWN["until"] = max(_BODHAN_COOLDOWN["until"], time.time() + delay)
                print(f"  ⚠ Bodhan rate limit (429); all workers pause {delay:.0f}s (retry {attempt + 1}/{max_retries})...")
                continue
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"bodhan: {data['error'].get('message')}")
            data["_engine"] = "bodhan/indic-ocr"
            return data
        
    # def _call_bodhan(bgr, api_key, max_tokens=8192, max_retries=3):
    #     payload = {
    #         "model": "indic-ocr",
    #         "messages": [{"role": "user", "content": [
    #             {"type": "image_url",
    #              "image_url": {"url": f"data:image/png;base64,{_b64_png(bgr)}"}}
    #         ]}],
    #         "max_tokens": max_tokens,
    #     }
    
    #     for attempt in range(max_retries):
    #         r = requests.post(BODHAN_URL,
    #                           headers={"Authorization": f"Bearer {api_key}"},
    #                           json=payload, timeout=(10, 300))
        
    #         if r.status_code == 429 and attempt < max_retries - 1:
    #             print(f"  ⚠ Bodhan rate limit (429); waiting 5s (retry {attempt + 1}/{max_retries})...")
    #             time.sleep(5)
    #             continue
            
    #         r.raise_for_status()
    #         data = r.json()
    #         if "error" in data:
    #             raise RuntimeError(f"bodhan: {data['error'].get('message')}")
    #         data["_engine"] = "bodhan/indic-ocr"
    #         return data

    _FALLBACK_OCR_PROMPT = (
        "You are an OCR engine. Transcribe this document page faithfully.\n"
        "Return ONLY JSON (no fences, no commentary) matching this schema:\n"
        "{\n"
        '  "blocks": [\n'
        "    {\n"
        '      "order": 1,\n'
        '      "type": "Text" | "Title" | "SectionHeader" | "List" | "Caption"\n'
        '              | "Table" | "Picture" | "PageHeader" | "PageFooter" | "Footnote",\n'
        '      "bbox_norm": [x0, y0, x1, y1] in [0,1] top-left, or null if uncertain,\n'
        '      "text": "exact content, preserve Latin+Devanagari+Telugu+... scripts"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules: emit in natural reading order. Picture/signature/stamp blocks get text=\"\". "
        "Render tables as GitHub-style pipe tables. Never fabricate bbox coordinates — use null.\n"
        "\n"
        "Classify these as PageFooter / PageHeader (NOT Text) — they are page furniture, "
        "not body content, and must not be joined with the body across page breaks:\n"
        "  • scanner watermarks: \"Scanned by CamScanner\", \"Scanned with OKEN Scanner\"\n"
        "  • page codes / numbers: \"1-2-103\", \"Page 5 of 10\", \"[1]\"\n"
        "  • page-turn hints: \"[P.T.O.]\", \"Please Turn Over\", \"Continued on next page\"\n"
        "  • signatures or handwritten margin marks\n"
    )

    def _call_openrouter_ocr(bgr, api_key, model, timeout=180):
        H, W = bgr.shape[:2]
        payload = {
            "model": model, "temperature": 0,"max_tokens": 8000, 
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _FALLBACK_OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % _img_b64(bgr)}},
            ]}],
        }
        r = requests.post(OPENROUTER_URL,
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"openrouter: {data['error']}")
        parsed = _parse_json_object(data["choices"][0]["message"]["content"])
        blocks = []
        for i, b in enumerate(parsed.get("blocks") or [], start=1):
            bn = b.get("bbox_norm")
            bbox_px = None
            if isinstance(bn, (list, tuple)) and len(bn) == 4:
                try:
                    x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in bn]
                    bbox_px = [round(x0*W, 1), round(y0*H, 1), round(x1*W, 1), round(y1*H, 1)]
                    if bbox_px[2] <= bbox_px[0] or bbox_px[3] <= bbox_px[1]: bbox_px = None
                except Exception: bbox_px = None
            blocks.append({"order": b.get("order", i), "type": b.get("type", "Text"),
                           "text": b.get("text", "") or "", "bbox_xyxy": bbox_px})
        return {"blocks": blocks, "_engine": f"openrouter/{model}"}

    def _extract_bodhan_blocks(resp):
        if isinstance(resp.get("blocks"), list): return resp["blocks"]
        content = resp.get("choices", [{}])[0].get("message", {}).get("content")
        if isinstance(content, list): return content
        if isinstance(content, str):
            parsed = _parse_json_object(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("blocks"), list):
                return parsed["blocks"]
            if isinstance(parsed, list): return parsed
        if isinstance(resp.get("data"), dict) and isinstance(resp["data"].get("blocks"), list):
            return resp["data"]["blocks"]
        return []

    def ocr_with_fallback(bgr):
        try:
            resp = _call_bodhan(bgr, BODHAN_KEY)
            raw = _extract_bodhan_blocks(resp)
            normalised = []
            for i, b in enumerate(raw, start=1):
                if not isinstance(b, dict): continue
                normalised.append({"order": b.get("order", i),
                                   "type":  b.get("type", "Text"),
                                   "text":  b.get("text", "") or "",
                                   "bbox_xyxy": b.get("bbox_xyxy") or b.get("bbox")})
            normalised = [
                b for b in normalised
                if (b.get("text") or "").strip()
                or b.get("type") in _PICTURE_TYPES
            ]
            if not normalised or not any((b.get("text") or "").strip() for b in normalised):
                raise RuntimeError(f"Bodhan returned no usable blocks "
                                   f"(got {len(normalised)}; top keys={list(resp.keys())[:6]})")
            return {"blocks": normalised, "_engine": "bodhan/indic-ocr"}
        except Exception as e:
            print(f"  Bodhan failed: {type(e).__name__}: {str(e)[:160]} — trying Gemini")
            if not OPENROUTER_KEY: raise RuntimeError("Bodhan failed and OPENROUTER_API_KEY is not set.")
            return _call_openrouter_ocr(bgr, OPENROUTER_KEY, OCR_FALLBACK_MODEL)


    # ── Python-side geometric dedup (unchanged from the previous notebook) ────
    _PICTURE_TYPES = {"Picture", "Chart", "Diagram", "Image", "Figure", "picture"}

    def _area(b): return max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    def _inter(a, b):
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        return max(0, x1 - x0) * max(0, y1 - y0)
    def _is_informative(block):
        if (block.get("text") or "").strip(): return True
        return block.get("type") in _PICTURE_TYPES
    def _normalize_words(text):
        tbl = str.maketrans('', '', string.punctuation + "\u0964\u0965")
        return {w.translate(tbl).lower() for w in (text or "").split() if w.strip()}

    def dedup_boxes(blocks, iou_threshold=0.3, contain_threshold=0.95, text_subsume_threshold=0.6):
        sorted_blocks = sorted(blocks,
            key=lambda b: (_is_informative(b), _area(b.get("bbox_xyxy") or [0,0,0,0])),
            reverse=True)
        kept, removed = [], []
        for c in sorted_blocks:
            if not c.get("bbox_xyxy"): kept.append(c); continue
            a = _area(c["bbox_xyxy"]) or 1.0
            c_words = _normalize_words(c.get("text") or "")
            drop_reason = None
            for k in kept:
                if not k.get("bbox_xyxy"): continue
                inter = _inter(c["bbox_xyxy"], k["bbox_xyxy"])
                if inter / a >= contain_threshold:
                    drop_reason = f"contained in B{k.get('order')} ({inter/a:.0%})"; break
                ka = _area(k["bbox_xyxy"]) or 1.0
                iou = inter / (a + ka - inter)
                if iou >= iou_threshold:
                    drop_reason = f"IoU {iou:.2f} with B{k.get('order')}"; break
                if inter > 0 and c_words:
                    k_words = _normalize_words(k.get("text") or "")
                    if k_words:
                        ov = len(c_words & k_words) / len(c_words)
                        if ov >= text_subsume_threshold:
                            drop_reason = f"text {ov:.0%} subsumed by B{k.get('order')}"
            if drop_reason: c = {**c, "_drop_reason": drop_reason}; removed.append(c)
            else: kept.append(c)
        kept.sort(key=lambda b: b.get("order", 0))
        return kept, removed


    # ── draw B0, B1, ... labels on the preprocessed image ────────────────────
    _PALETTE = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
                (145, 30, 180), (0, 128, 128), (240, 50, 230), (170, 110, 40)]

    def draw_labeled(img_bgr, blocks):
        pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        try: font = ImageFont.truetype("arial.ttf", 24)
        except Exception: font = ImageFont.load_default()
        types = sorted({b.get("type", "?") for b in blocks})
        color_for = {t: _PALETTE[i % len(_PALETTE)] for i, t in enumerate(types)}
        W, H = pil.size
        for b in blocks:
            if not b.get("bbox_xyxy"): continue
            x0, y0, x1, y1 = [int(v) for v in b["bbox_xyxy"]]
            c = color_for[b.get("type", "?")]
            draw.rectangle([x0, y0, x1, y1], outline=c, width=3)
            tag = b["_label"]
            tw  = int(draw.textlength(tag, font=font)); th, pad = 28, 4
            tag_x = x0 - pad; tag_y = y0 - th - 2
            if tag_y < 0: tag_y = 0
            if tag_x < 0: tag_x = 0
            if tag_x + tw + 2*pad > W: tag_x = W - tw - 2*pad
            draw.rectangle([tag_x, tag_y, tag_x + tw + 2*pad, tag_y + th], fill=c)
            draw.text((tag_x + pad, tag_y + 2), tag, fill="white", font=font)
        return pil


    # ── Bodhan type → standard region type (used by the sidecar + builder) ──
    BODHAN_TO_STD = {
        "Title":"title", "SectionHeader":"heading", "Text":"paragraph",
        "List":"list_item", "Caption":"caption", "Table":"Table",
        "Picture":"picture", "Chart":"picture", "Diagram":"picture",
        "Image":"picture", "Figure":"picture",
        "PageHeader":"header", "PageFooter":"footer", "PageNumber":"footer",
        "Header":"header", "Footer":"footer", "Footnote":"paragraph",
        "Infobox":"paragraph", "Folio":"footer",
    }


    # ─────────────────────────────────────────────────────────────────────────
    # Run Stage 1: OCR + dedup + label + build page_dicts (RAW order from Bodhan)
    # ─────────────────────────────────────────────────────────────────────────
    reset_metrics()
    ocr_responses, preprocessed = {}, {}
    ocr_failures = {}

    # page_dicts holds blocks in the order Bodhan emitted them (after Python dedup).
    # Stage 2 will reorder them. Each region carries a stable block_id used end-to-end.
    page_dicts = []

    def _stage1_page(pno):
        print(f"\n── Stage 1 · page {pno+1}/{total} ──")
        set_stage("A", "preprocess")
        img = preprocess_page(pno)
        preprocessed[pno] = img

        set_stage("A", "ocr")
        try:
            resp = ocr_with_fallback(img)
            ocr_responses[pno] = resp
            print(f"  ocr ok: {resp['_engine']}  ({len(resp['blocks'])} blocks)")
            for _bi, _b in enumerate(resp["blocks"]):
                _show_block("[OCR]", _b, label=f"#{_bi}")
        except Exception as e:
            ocr_failures[pno] = str(e)
            print(f"  OCR failed: {e}")
            return None

        set_stage("A", "dedup")
        kept, removed = dedup_boxes(resp["blocks"])
        for i, b in enumerate(kept): b["_label"] = f"B{i}"
        print(f"  dedup: {len(resp['blocks'])} → kept {len(kept)}, removed {len(removed)}")
        print(f"  ── DEDUP kept (page {pno+1}) ──")
        for _b in kept: _show_block("[KEPT]", _b)
        if removed:
            print(f"  ── DEDUP removed (page {pno+1}) ──")
            for _b in removed:
                reason = _b.get("_drop_reason", "?")
                print(f"    [DEDUP-DROP] #{_b.get('order','?')} reason={reason}  text={_preview(_b.get('text'))!r}")

        set_stage("A", "label")
        labeled = draw_labeled(img, kept)
        labeled.save(PAGE_DIR / f"{stem}_p{pno+1}_labeled.png")

        # Build regions in Bodhan's natural (post-dedup) order — Stage 2 reorders.
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        H, W = img.shape[:2]
        regions = []
        for i, b in enumerate(kept, start=1):
            rid   = b["_label"]
            std   = BODHAN_TO_STD.get(b.get("type"), "paragraph")
            block_id = f"p{pno+1}_{rid}"
            reg = {
                "id":          f"A_p{pno+1}_r{i}",
                "block_id":    block_id,
                "page":        pno + 1,
                "type":        std,
                "bodhan_type": b.get("type", "Text"),
                "bbox_xyxy":   b.get("bbox_xyxy"),
                "page_wh":     [W, H],
            }
            if std == "picture" and b.get("bbox_xyxy"):
                x0, y0, x1, y1 = [int(v) for v in b["bbox_xyxy"]]
                crop_path = CROP_DIR / f"{stem}_p{pno+1}_{rid}.png"
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                pil.crop((x0, y0, x1, y1)).save(crop_path)
                reg["image_path"] = str(crop_path)
                reg["text"] = ""
            else:
                reg["text"] = b.get("text") or ""
            regions.append(reg)
        return {"page": pno + 1, "regions": regions}

    with ThreadPoolExecutor(BODHAN_WORKERS) as _ex:
        page_dicts = [d for d in _ex.map(_stage1_page, page_indices) if d]   # page order kept

    set_stage("-", "-")

    # Compact summary of what will enter Stage 2.
    print(f"\n── Stage 1 summary ──")
    total_regions = sum(len(p["regions"]) for p in page_dicts)
    print(f"pages processed: {len(page_dicts)} · regions: {total_regions}")
    for pd in page_dicts:
        print(f"  page {pd['page']}: {len(pd['regions'])} region(s)")

