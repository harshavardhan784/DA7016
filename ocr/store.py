"""One folder per job. Plain JSON files, written atomically, no database.

jobs/<id>/
  input.pdf          the upload
  status.json        state, owner, filename, pages, timestamps, built_rev
  run.log            output of the pipeline run
  ops.log            output of rebuild / review runs
  blocks_pre.json    blocks as the pipeline produced them, before fix_blocks (never edited)
  history.json       every edit since then, with an undo/redo cursor
  ui.json            what the screen shows (written by rebuild)
  proposals/<id>.json  review results waiting for accept / reject
  pages/<n>.jpg      page images for the screen
  output.docx        the latest built document
"""
import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import config

_locks = {}
_locks_guard = threading.Lock()


def lock(job_id):
    with _locks_guard:
        return _locks.setdefault(job_id, threading.RLock())


def rebuild_lock(job_id):
    """Rebuilds queue behind each other, but never hold up edits (which take lock(job_id) for a moment)."""
    with _locks_guard:
        return _locks.setdefault(("rebuild", job_id), threading.RLock())


def job_dir(job_id):
    if not job_id.isalnum():
        raise KeyError(job_id)
    d = config.JOBS / job_id
    if not d.is_dir():
        raise KeyError(job_id)
    return d


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write_json(path, data):
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ── status ────────────────────────────────────────────────────────────────
def new_job(owner, owner_email, filename, pdf_bytes, pages):
    job_id = uuid.uuid4().hex[:12]
    d = config.JOBS / job_id
    d.mkdir(parents=True)
    (d / "input.pdf").write_bytes(pdf_bytes)
    set_status(job_id, state="queued", owner=owner, owner_email=owner_email,
               filename=filename, pages=pages, created=time.time(), built_rev=-1)
    return job_id


def status(job_id):
    return read_json(job_dir(job_id) / "status.json", {})


def set_status(job_id, **kw):
    with lock(job_id):
        p = config.JOBS / job_id / "status.json"
        d = read_json(p, {})
        d.update(kw)
        write_json(p, d)
        return d


def jobs_of(owner):
    out = []
    for d in config.JOBS.iterdir():
        st = read_json(d / "status.json", None)
        if st and st.get("owner") == owner:
            out.append({"job_id": d.name, **st})
    return sorted(out, key=lambda s: -s.get("created", 0))


# ── history: blocks_pre.json + events, with a cursor ──────────────────────
BLOCK_FIELDS = {"formatted_text", "is_picture", "bodhan_type", "bbox_norm", "bbox_xyxy", "merged_from"}
NEW_BLOCK_KEYS = {"block_id", "page", "bbox_xyxy", "bbox_norm", "bodhan_type", "is_picture",
                  "image_path", "formatted_text", "text", "page_wh"}


def _find(blocks, block_id):
    for b in blocks:
        if b["block_id"] == block_id:
            return b
    raise ValueError(f"unknown block {block_id}")


def apply_event(blocks, ev):
    """Return the block list after one event. Raises ValueError for anything invalid. Does not touch `blocks`."""
    blocks = [dict(b) for b in blocks]
    op = ev.get("op")
    if op == "set_fields":
        b = _find(blocks, ev["block"])
        bad = set(ev["fields"]) - BLOCK_FIELDS
        if bad:
            raise ValueError(f"fields not editable: {sorted(bad)}")
        b.update(ev["fields"])
    elif op == "reorder":
        order = ev["order"]
        if sorted(order) != sorted(b["block_id"] for b in blocks):
            raise ValueError("order must list every block exactly once")
        by = {b["block_id"]: b for b in blocks}
        if [by[i]["page"] for i in order] != [b["page"] for b in blocks]:
            raise ValueError("blocks can only be moved within their own page")
        blocks = [by[i] for i in order]
    elif op == "add_block":
        nb, idx = dict(ev["block"]), ev["index"]
        if set(nb) - NEW_BLOCK_KEYS or not {"block_id", "page", "formatted_text"} <= set(nb):
            raise ValueError("invalid new block")
        if any(b["block_id"] == nb["block_id"] for b in blocks):
            raise ValueError("block id already exists")
        if not isinstance(idx, int) or not 0 <= idx <= len(blocks):
            raise ValueError("invalid position")
        blocks.insert(idx, nb)
    elif op == "delete":
        ids = set(ev["blocks"])
        unknown = ids - {b["block_id"] for b in blocks}
        if unknown:
            raise ValueError(f"unknown blocks: {sorted(unknown)}")
        blocks = [b for b in blocks if b["block_id"] not in ids]
        if not blocks:
            raise ValueError("a document needs at least one block")
    elif op == "batch":
        for sub in ev["events"]:
            blocks = apply_event(blocks, sub)
    else:
        raise ValueError(f"unknown event {op!r}")
    return blocks


def base_blocks(job_id):
    return read_json(job_dir(job_id) / "blocks_pre.json")["blocks"]


def history(job_id):
    return read_json(job_dir(job_id) / "history.json", {"rev": 0, "cursor": 0, "events": []})


def current_blocks(job_id):
    h = history(job_id)
    blocks = copy.deepcopy(base_blocks(job_id))
    for ev in h["events"][:h["cursor"]]:
        blocks = apply_event(blocks, ev)
    return blocks


def push(job_id, ev):
    """Validate and append an event (dropping anything that was undone). Returns the new history header."""
    with lock(job_id):
        blocks = current_blocks(job_id)
        apply_event(blocks, ev)                       # validation only
        h = history(job_id)
        h["events"] = h["events"][:h["cursor"]] + [ev]
        h["cursor"] += 1
        h["rev"] += 1
        write_json(job_dir(job_id) / "history.json", h)
        return {"rev": h["rev"], "cursor": h["cursor"], "length": len(h["events"])}


def move_cursor(job_id, delta):
    with lock(job_id):
        h = history(job_id)
        c = max(0, min(len(h["events"]), h["cursor"] + delta))
        if c != h["cursor"]:
            h["cursor"], h["rev"] = c, h["rev"] + 1
            write_json(job_dir(job_id) / "history.json", h)
        return {"rev": h["rev"], "cursor": h["cursor"], "length": len(h["events"])}


def light_doc(job_id):
    """What the screen shows, built from the saved edits alone (milliseconds, no notebook).
    Text, order and the block list are always current. Flags that need the notebook's page-break logic
    (which block was joined into which) are those of the last rebuild; `dirty` says they may be behind."""
    d = job_dir(job_id)
    ui = read_json(d / "ui.json")
    if ui is None:
        return None
    h = history(job_id)
    blocks = current_blocks(job_id)
    known = ui.get("dir") or ui["blocks"]
    out = {}
    for b in blocks:
        i = b["block_id"]
        k = known.get(i, {})
        markup = b.get("formatted_text") or ""
        same = k.get("markup") == markup
        out[i] = {
            "id": i, "page": b["page"], "type": b.get("bodhan_type"), "is_picture": bool(b.get("is_picture")),
            "has_image": bool(b.get("is_picture") and b.get("image_path")),
            "bbox_norm": b.get("bbox_norm") or k.get("bbox_norm"), "markup": markup,
            "rendered": bool(markup.strip()) or bool(b.get("is_picture") and b.get("image_path")),
            "reason": k.get("reason") if same else None,
            "absorbed": bool(k.get("absorbed")) if same else False,
            "absorbed_by": k.get("absorbed_by") if same else None,
        }
    return {"route": ui["route"], "route_info": ui["route_info"], "pages": ui["pages"], "log": ui["log"],
            "rev": h["rev"], "cursor": h["cursor"], "length": len(h["events"]),
            "order": [b["block_id"] for b in blocks], "blocks": out,
            "dirty": h["rev"] != read_json(d / "status.json", {}).get("built_rev")}


# ── proposals ─────────────────────────────────────────────────────────────
def save_proposal(job_id, prop):
    pid = uuid.uuid4().hex[:12]
    d = job_dir(job_id) / "proposals"
    d.mkdir(exist_ok=True)
    prop = {**prop, "id": pid, "status": "ready", "created": time.time()}
    write_json(d / f"{pid}.json", prop)
    return prop


def proposal(job_id, pid):
    if not pid.isalnum():
        raise KeyError(pid)
    p = read_json(job_dir(job_id) / "proposals" / f"{pid}.json")
    if p is None:
        raise KeyError(pid)
    return p


def update_proposal(job_id, pid, **kw):
    p = {**proposal(job_id, pid), **kw}
    write_json(job_dir(job_id) / "proposals" / f"{pid}.json", p)
    return p
