# Stage file 'final': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── FINAL: tags → DOCX + sidecar (all routes) ─────────────────────────────
set_stage("final", "fix")
ordered_blocks = fix_blocks(ordered_blocks)
set_stage("final", "build_docx")
docx_path = METHOD_DIR / f"{stem}__{ROUTE}.docx"
try:
    final_docx, block_meta = build_docx_with_blocks(ordered_blocks, docx_path)
except PermissionError:
    docx_path = docx_path.with_name(f"{docx_path.stem}_{datetime.datetime.now():%Y%m%d_%H%M%S}.docx")
    final_docx, block_meta = build_docx_with_blocks(ordered_blocks, docx_path)

sidecar = {
    "pdf_path": str(PDF_PATH), "docx_path": str(final_docx), "route": ROUTE, "route_info": ROUTE_INFO,
    "models": M, "pages_processed": [p + 1 for p in page_indices],
    "reading_order": [b["block_id"] for b in ordered_blocks],
    "blocks": {bid: {**m, "bbox_norm": next((b.get("bbox_norm") for b in ordered_blocks if b["block_id"] == bid), None)} for bid, m in block_meta.items()},
}
sidecar_path = Path(final_docx).with_suffix("").with_suffix(".blocks.json")
sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
set_stage("-", "-")

calls = [c for c in CALLS if c["engine"] != "_wallclock_"]
print(f"saved {final_docx}\nsaved {sidecar_path}")
print(f"route={ROUTE} · blocks={len(block_meta)} · rendered={sum(m['rendered'] for m in block_meta.values())}")
print(f"api calls={len(calls)} · tokens in/out={sum(c['tok_in'] for c in calls):,}/{sum(c['tok_out'] for c in calls):,} "
      f"· ${sum((c['cost'] or 0) for c in calls):.4f} · {sum(c['seconds'] for c in calls):.0f}s (summed over calls)")

