"""Infinite OCR web server. The browser UI (web/dist) talks to the /api routes below."""
import queue
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pymupdf
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, intent, ops, store

work_q: "queue.Queue[str]" = queue.Queue()
MAX_MARKUP = 200_000


def _worker():
    """One pipeline run at a time: the notebook already uses threads for its model calls."""
    while True:
        job_id = work_q.get()
        d = config.JOBS / job_id
        try:
            store.set_status(job_id, state="running", started=time.time())
            ops.run_pipeline(d)
            store.set_status(job_id, state="finalizing")
            ops.call(d, "rebuild")
            store.set_status(job_id, state="done", finished=time.time())
        except Exception as e:
            store.set_status(job_id, state="error", error=str(e), finished=time.time())


@asynccontextmanager
async def lifespan(_):
    config.JOBS.mkdir(parents=True, exist_ok=True)
    for d in config.JOBS.iterdir():                       # jobs interrupted by a restart
        st = store.read_json(d / "status.json", {})
        if st.get("state") == "queued":
            work_q.put(d.name)
        elif st.get("state") in ("running", "finalizing"):
            store.set_status(d.name, state="error", error="server restarted while this job was running")
    threading.Thread(target=_worker, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


# ── sign-in ───────────────────────────────────────────────────────────────
def user(request: Request):
    if config.AUTH_DISABLED:
        return {"sub": "local", "email": "local"}
    s = auth.read_session(request.cookies.get("session"))
    if not s:
        raise HTTPException(401, "sign in required")
    return s


def own(job_id: str, u=Depends(user)):
    try:
        d = store.job_dir(job_id)
    except KeyError:
        raise HTTPException(404, "unknown job")
    if store.status(job_id).get("owner") != u["sub"]:
        raise HTTPException(404, "unknown job")
    return d


class Credential(BaseModel):
    credential: str


@app.get("/api/config")
def get_config():
    return {"auth": not config.AUTH_DISABLED, "google_client_id": config.GOOGLE_CLIENT_ID,
            "max_mb": config.MAX_MB}


@app.post("/api/auth/google")
def sign_in(body: Credential, response: Response):
    if config.AUTH_DISABLED or not config.GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google sign-in is not configured")
    try:
        sub, email = auth.verify_google(body.credential)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError:
        raise HTTPException(401, "Google sign-in could not be verified")
    response.set_cookie("session", auth.make_session(sub, email), httponly=True, secure=True, samesite="lax",
                        max_age=config.SESSION_HOURS * 3600)
    return {"email": email}


@app.post("/api/auth/logout")
def sign_out(response: Response):
    response.delete_cookie("session")
    return {}


@app.get("/api/me")
def me(u=Depends(user)):
    return {"email": u["email"]}


# ── jobs ──────────────────────────────────────────────────────────────────
@app.post("/api/jobs")
async def upload(file: UploadFile = File(...), u=Depends(user)):
    data = await file.read()
    if len(data) > config.MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"file larger than {config.MAX_MB} MB")
    try:
        pages = len(pymupdf.open(stream=data, filetype="pdf"))
    except Exception:
        raise HTTPException(400, "that is not a readable PDF")
    job_id = store.new_job(u["sub"], u["email"], file.filename or "document.pdf", data, pages)
    work_q.put(job_id)
    return {"job_id": job_id, "pages": pages}


@app.get("/api/jobs")
def list_jobs(u=Depends(user)):
    return [{k: v for k, v in j.items() if k not in ("owner", "owner_email")} for j in store.jobs_of(u["sub"])]


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, d: Path = Depends(own)):
    st = {k: v for k, v in store.status(job_id).items() if k not in ("owner", "owner_email")}
    log = d / "run.log"
    st["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else ""
    st["queue_size"] = work_q.qsize()
    return st


@app.get("/api/jobs/{job_id}/doc")
def document(job_id: str, d: Path = Depends(own)):
    doc = store.light_doc(job_id)               # built from the saved edits: instant, never waits for a rebuild
    if doc is None:
        raise HTTPException(409, "document not ready yet")
    return doc


@app.get("/api/jobs/{job_id}/pages/{n}.jpg")
def page_image(n: int, d: Path = Depends(own)):
    f = d / "pages" / f"{n}.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/blocks/{block_id}/image")
def block_image(job_id: str, block_id: str, d: Path = Depends(own)):
    b = next((b for b in store.current_blocks(job_id) if b["block_id"] == block_id), None)
    f = (d / b["image_path"]).resolve() if b and b.get("image_path") else None
    if not f or d.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


def _rebuild(job_id, d):
    with store.rebuild_lock(job_id):              # not the edit lock: saving a change never waits for a rebuild
        ops.call(d, "rebuild")


@app.post("/api/jobs/{job_id}/rebuild")
def rebuild(job_id: str, d: Path = Depends(own)):
    _rebuild(job_id, d)
    return document(job_id, d)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str, d: Path = Depends(own)):
    h = store.history(job_id)
    if h["rev"] != store.status(job_id).get("built_rev"):
        _rebuild(job_id, d)
    f = d / "output.docx"
    if not f.exists():
        raise HTTPException(404, "output not ready")
    name = Path(store.status(job_id).get("filename", "document")).stem
    return FileResponse(f, filename=f"{name}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ── edits ─────────────────────────────────────────────────────────────────
class Markup(BaseModel):
    markup: str = Field(max_length=MAX_MARKUP)


class Order(BaseModel):
    order: list[str]


def _push(job_id, ev):
    try:
        return store.push(job_id, ev)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/jobs/{job_id}/blocks/{block_id}")
def edit_block(job_id: str, block_id: str, body: Markup, d: Path = Depends(own)):
    return _push(job_id, {"op": "set_fields", "block": block_id, "fields": {"formatted_text": body.markup}})


@app.put("/api/jobs/{job_id}/order")
def reorder(job_id: str, body: Order, d: Path = Depends(own)):
    return _push(job_id, {"op": "reorder", "order": body.order})


class Delete(BaseModel):
    blocks: list[str] = Field(min_length=1, max_length=200)


@app.post("/api/jobs/{job_id}/delete")
def delete_blocks(job_id: str, body: Delete, d: Path = Depends(own)):
    return _push(job_id, {"op": "delete", "blocks": body.blocks})


@app.post("/api/jobs/{job_id}/undo")
def undo(job_id: str, d: Path = Depends(own)):
    return store.move_cursor(job_id, -1)


@app.post("/api/jobs/{job_id}/redo")
def redo(job_id: str, d: Path = Depends(own)):
    return store.move_cursor(job_id, +1)


# ── review ────────────────────────────────────────────────────────────────
class Review(BaseModel):
    mode: str = "auto"                       # auto = the assistant (any request); or the shortcuts format / reread / delete
    blocks: list[str] = Field(min_length=1, max_length=40)
    note: str = Field("", max_length=1000)
    model: Optional[str] = None


class NewBox(BaseModel):
    page: int
    bbox_norm: list[float] = Field(min_length=4, max_length=4)
    note: str = Field("", max_length=1000)
    model: Optional[str] = None


def _propose(job_id, d, op, req):
    try:
        result = ops.call(d, op, req)
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    return store.save_proposal(job_id, result)


ZERO_USAGE = {"calls": 0, "tok_in": 0, "tok_out": 0, "cost": 0.0}


@app.post("/api/jobs/{job_id}/review")
def review(job_id: str, body: Review, d: Path = Depends(own)):
    if body.mode not in ("auto", "format", "reread", "delete"):
        raise HTTPException(422, "mode must be auto, format, reread or delete")
    current = {b["block_id"]: b for b in store.current_blocks(job_id)}
    missing = [i for i in body.blocks if i not in current]
    if missing:
        raise HTTPException(422, f"unknown blocks: {missing}")
    if body.mode == "auto":                           # anything the person says: the assistant decides what to propose
        prop = _propose(job_id, d, "assist", body.model_dump())
        return {**prop, "block_ids": body.blocks, "understood": prop.get("message", ""), "alternatives": []}
    u = intent.understand(body.note, body.mode)
    base = {"note": body.note, "model": None, "skipped": [], "usage": ZERO_USAGE, "block_ids": body.blocks,
            "understood": u["say"], "alternatives": u["choices"]}
    if u["action"] in ("ask", "info"):                 # nothing to do yet: say so and offer the choices
        return store.save_proposal(job_id, {**base, "mode": "ask", "blocks": [], "message": u["say"], "choices": u["choices"]})
    if u["action"] == "delete":
        return store.save_proposal(job_id, {**base, "mode": "delete", "alternatives": [], "blocks": [
            {"id": i, "before": current[i].get("formatted_text") or "", "after": "", "fields": {}, "plain_text_fallback": False} for i in body.blocks]})
    req = {**body.model_dump(), "mode": u["action"]}
    return {**_propose(job_id, d, "review", req), **{k: base[k] for k in ("block_ids", "understood", "alternatives")}}


@app.post("/api/jobs/{job_id}/newbox")
def newbox(job_id: str, body: NewBox, d: Path = Depends(own)):
    return _propose(job_id, d, "newbox", body.model_dump())


class NewBlock(BaseModel):
    after: str
    markup: str = Field(max_length=MAX_MARKUP)
    type: str = "Text"


@app.post("/api/jobs/{job_id}/blocks")
def add_block(job_id: str, body: NewBlock, d: Path = Depends(own)):
    """A new block with no box on the page (for example a table the person inserts), placed right after another block."""
    blocks = store.current_blocks(job_id)
    ids = [b["block_id"] for b in blocks]
    if body.after not in ids:
        raise HTTPException(422, f"unknown block: {body.after}")
    page = blocks[ids.index(body.after)]["page"]
    n = 1 + max([int(m.group(1)) for i in ids if (m := re.fullmatch(rf"p{page}_n(\d+)", i))] or [0])
    new = {"block_id": f"p{page}_n{n}", "page": page, "bbox_xyxy": None, "bbox_norm": None, "bodhan_type": body.type,
           "is_picture": False, "image_path": None, "formatted_text": body.markup}
    head = _push(job_id, {"op": "add_block", "block": new, "index": ids.index(body.after) + 1})
    return {**head, "id": new["block_id"]}


class Accept(BaseModel):
    blocks: Optional[list[str]] = None        # default: every block in the proposal


def _assist_events(job_id, todo, prop):
    """The edits behind the accepted assistant cards, checked against the document as it is now.
    Positions and ids of new blocks, and every block's context, are worked out here (from the blocks as they are at this moment),
    not when the proposal was made, so earlier changes cannot misplace them or overwrite each other's context."""
    sim = store.current_blocks(job_id)
    events = []

    def add(ev):
        nonlocal sim
        try:
            sim = store.apply_event(sim, ev)
        except ValueError as e:
            raise HTTPException(409, str(e))
        events.append(ev)

    for card in todo:
        have = {b["block_id"]: b for b in sim}
        for bid, before in (card.get("expect") or {}).items():
            if bid not in have:
                raise HTTPException(409, f"{bid} is no longer in the document")
            if (have[bid].get("formatted_text") or "") != before:
                raise HTTPException(409, f"{bid} was edited after this proposal was made")
        eid, asked, said = f"{prop['id']}.{card['id']}", prop.get("note") or "", prop.get("message") or ""
        for ev in card["events"]:
            ids = [b["block_id"] for b in sim]
            have = {b["block_id"]: b for b in sim}
            if ev["op"] == "set_fields":                         # an edit: the request goes into that block's context
                entry = ops._entry(eid, asked, said, ops._card_said(card))
                add({**ev, "fields": {**ev["fields"], "context": ops._ctx_add(have[ev["block"]].get("context"), entry)}})
            elif ev["op"] == "insert_after":
                if ev["after"] not in ids:
                    raise HTTPException(409, f"{ev['after']} is no longer in the document")
                at = ids.index(ev["after"])
                page = sim[at]["page"]
                n = 1 + max([int(m.group(1)) for i in ids if (m := re.fullmatch(rf"p{page}_n(\d+)", i))] or [0])
                add({"op": "add_block", "index": at + 1,
                     "block": {"block_id": f"p{page}_n{n}", "page": page, "bbox_xyxy": None, "bbox_norm": None, "bodhan_type": ev["type"],
                               "is_picture": False, "image_path": None, "formatted_text": ev["markup"],
                               "context": [ops._entry(eid, asked, said, ops._card_said(card))]}})
            elif ev["op"] == "merge_blocks":                     # one new block (a new id, a box around the pieces) takes the place of the pieces
                srcs = [have[i] for i in ev["ids"]]
                new_id = ops._merged_id(ids, srcs[0]["page"], [a for s in srcs for a in ops._atom_ids(s)])
                entry = ops._entry(eid, asked, said, ops._card_said(card, new_id))
                block = {k: v for k, v in srcs[0].items() if k in store.NEW_BLOCK_KEYS}
                block.update(ev["fields"])
                block.update(block_id=new_id, merged_from=[a for s in srcs for a in ops._atoms_with_context(s, entry)],
                             context=ops._ctx_join(*[s.get("context") for s in srcs], [entry]))
                add({"op": "add_block", "index": ids.index(ev["ids"][0]), "block": block})
                add({"op": "delete", "blocks": list(ev["ids"])})
            elif ev["op"] == "split_block":                      # the original blocks come back, each with its own context (and what happened since)
                host = have.get(ev["id"])
                atoms = (host or {}).get("merged_from") or []
                if len(atoms) < 2:
                    raise HTTPException(409, f"{ev['id']} is no longer a merged block")
                taken = [a["block_id"] for a in atoms if a["block_id"] in ids]
                if taken:
                    raise HTTPException(409, f"{', '.join(taken)} already exists")
                entry = ops._entry(eid, asked, said, ops._card_said(card))
                known = {e.get("eid") for a in atoms for e in (a.get("context") or [])}
                later = [e for e in (host.get("context") or []) if e.get("eid") not in known]
                at = ids.index(host["block_id"])
                for k, a in enumerate(atoms):
                    block = {kk: v for kk, v in a.items() if kk in store.NEW_BLOCK_KEYS}
                    block["context"] = ops._ctx_join(a.get("context"), later, [entry])
                    add({"op": "add_block", "index": at + 1 + k, "block": block})
                add({"op": "delete", "blocks": [host["block_id"]]})
            else:
                add(ev)
    return events


def _prop(job_id, pid):
    try:
        return store.proposal(job_id, pid)
    except KeyError:
        raise HTTPException(404, "unknown proposal")


@app.post("/api/jobs/{job_id}/proposals/{pid}/accept")
def accept(job_id: str, pid: str, body: Accept, d: Path = Depends(own)):
    with store.lock(job_id):
        prop = _prop(job_id, pid)
        todo = [b for b in prop["blocks"] if not b.get("resolved") and (body.blocks is None or b["id"] in body.blocks)]
        if not todo:
            raise HTTPException(409, "nothing left to accept")
        current = {b["block_id"]: b for b in store.current_blocks(job_id)}
        events = []
        if prop["mode"] == "newbox":
            events.append({"op": "add_block", "block": prop["new_block"], "index": prop["index"]})
        elif prop["mode"] == "delete":
            events.append({"op": "delete", "blocks": [b["id"] for b in todo]})
        elif prop["mode"] == "assist":
            events = _assist_events(job_id, todo, prop)
        else:
            for b in todo:
                if (current[b["id"]].get("formatted_text") or "") != b["before"]:
                    raise HTTPException(409, f"{b['id']} was edited after this proposal was made")
                events.append({"op": "set_fields", "block": b["id"], "fields": b["fields"]})
        head = _push(job_id, {"op": "batch", "events": events})
        ids = {b["id"] for b in todo}
        blocks = [{**b, "resolved": "accepted"} if b["id"] in ids else b for b in prop["blocks"]]
        store.update_proposal(job_id, pid, blocks=blocks,
                              status="closed" if all(b.get("resolved") for b in blocks) else "ready")
        return head


@app.post("/api/jobs/{job_id}/proposals/{pid}/reject")
def reject(job_id: str, pid: str, body: Accept, d: Path = Depends(own)):
    with store.lock(job_id):
        prop = _prop(job_id, pid)
        blocks = [{**b, "resolved": "rejected"} if not b.get("resolved") and (body.blocks is None or b["id"] in body.blocks)
                  else b for b in prop["blocks"]]
        store.update_proposal(job_id, pid, blocks=blocks,
                              status="closed" if all(b.get("resolved") for b in blocks) else "ready")
        return {}


# ── the screen ────────────────────────────────────────────────────────────
DIST = config.APP_DIR / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
else:
    @app.get("/")
    def index():
        return PlainTextResponse("Infinite OCR API is running. The web screen has not been installed (web/dist is missing).")
