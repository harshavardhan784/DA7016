# Stage file 'config': the code of one cell of pipeline/infinite_ocr.ipynb, unchanged. The notebook is the reference; this file is what runs.
# It is not a module: pipeline/runtime.py runs the stage files in order in one shared namespace, exactly as the notebook runs its cells.
# ─── CELL 1: setup & config ───────────────────────────────────────────────
# First run only:  !pip -q install pymupdf opencv-python-headless python-docx requests pillow docling latex2mathml mathml2omml
import base64, io, json, os, re, sys, time, string, datetime, hashlib, socket, math, difflib, statistics, copy
import html as _html
from pathlib import Path
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
import pymupdf
import requests
from PIL import Image, ImageDraw, ImageFont

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT
from html import unescape
import unicodedata
import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# ── INPUT ──
PDF_PATH     = Path("pdf/tmax.pdf")   # change this
PAGES        = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]           # -1 = all, or 0-indexed list e.g. [0, 2, 5]
DPI          = 200
CHUNK_BLOCKS = 8             # Indic route: blocks per formatting call
FORCE_ROUTE  = None          # None = auto | "digital_english" | "single_gemini" | "scanned_indic"

# ── MODELS: one menu per step, pick with CHOICE (index into the list). Edit the lists freely. ──
MODEL_OPTIONS = {
    "classify":      ["qwen/qwen3-vl-30b-a3b-instruct","google/gemini-2.5-flash-lite", "google/gemini-2.5-flash", "qwen/qwen3-vl-30b-a3b-instruct"],            # script probe (scanned)
    "digital_order": ["google/gemini-2.5-flash", "google/gemini-2.5-flash-lite", "qwen/qwen3-vl-30b-a3b-instruct", "nvidia/nemotron-3.5-super-vl"],
    "single_call":   ["google/gemini-2.5-flash", "google/gemini-2.5-pro", "qwen/qwen3-vl-235b-a22b-instruct"],               # OCR+order+tags in one call
    "indic_order":   ["google/gemini-2.5-flash", "nvidia/nemotron-3.5-super-vl", "qwen/qwen3-vl-30b-a3b-instruct"],           # after Bodhan
    "indic_format":  ["google/gemini-2.5-flash","qwen/qwen3-vl-30b-a3b-instruct", "google/gemini-2.5-flash", "qwen/qwen3.5-9b"],                        # tags for DOCX ("Docx Qwen")
    "ocr_fallback":  ["google/gemini-2.5-flash", "qwen/qwen3-vl-30b-a3b-instruct"],                                           # if Bodhan fails
}
CHOICE = {"classify": 0, "digital_order": 0, "single_call": 0, "indic_order": 0, "indic_format": 0, "ocr_fallback": 0}
M = {k: MODEL_OPTIONS[k][CHOICE[k]] for k in MODEL_OPTIONS}

GEMINI_THINK_TOKENS = 512     # reasoning cap applied to every google/gemini* call (None = model default). Lower = faster.
# ── parallelism (threads; lower a knob if OpenRouter/Bodhan return many HTTP 429) ──
ORDER_WORKERS       = 8       # pages in parallel: digital ordering, single-Gemini calls, Indic ordering
BODHAN_WORKERS      = 8       # pages in parallel for Bodhan OCR (+dedup/label)
CHUNK_WORKERS       = 8       # Indic formatting chunks in parallel

# ── legacy names the Indic route (Stage 1/2 code) expects ──
OCR_FALLBACK_MODEL = M["ocr_fallback"]
COMBINED_MODELS    = [M["indic_format"]] + [m for m in ("google/gemini-2.5-flash",) if m != M["indic_format"]]
order_model        = [M["indic_order"]]
INSERT_PAGE_BREAKS = False

OUT_DIR    = Path("compare_out")
METHOD_DIR = OUT_DIR / "infinite_ocr"
CROP_DIR   = METHOD_DIR / "crops"
PAGE_DIR   = METHOD_DIR / "pages"
for d in (OUT_DIR, METHOD_DIR, CROP_DIR, PAGE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── CREDS (only OpenRouter + Bodhan) ──
BODHAN_KEY     = os.environ.get("BODHAN_OCR_API_KEY")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
BODHAN_URL     = "https://api.bodhan.ai/v1/chat/completions"

PRICES_USD_PER_M = {                      # (input, output) USD per 1M tokens — used only if OpenRouter omits cost
    "google/gemini-2.5-flash-lite":   (0.10, 0.40),
    "google/gemini-2.5-flash":        (0.30, 2.50),
    "google/gemini-2.5-pro":          (1.25, 10.0),
    "qwen/qwen3-vl-30b-a3b-instruct": (0.10, 0.40),
    "qwen/qwen3.5-9b":                (0.10, 0.40),
    "nvidia/nemotron-3.5-super-vl":   (0.10, 0.40),
}
BODHAN_USD_PER_PAGE = 0.0024

# ── open PDF ──
doc   = pymupdf.open(PDF_PATH)
stem  = PDF_PATH.stem
total = len(doc)
page_indices = list(range(total)) if PAGES == -1 else [p for p in PAGES if 0 <= p < total]
if not page_indices:
    raise ValueError(f"no valid pages in PAGES={PAGES} for a {total}-page PDF")
print(f"PDF: {PDF_PATH.name} ({total} pages) · processing {len(page_indices)} page(s)")
print("models:", json.dumps(M, indent=1))

