import copy
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from tests.conftest import BLOCKS
from ocr import store
from ocr import api
from ocr import auth
from ocr import config
from ocr import ops

QWEN = "qwen/qwen3-vl-30b-a3b-instruct"          # the model the notebook names for its 'classify' stage


def _pdf():
    d = pymupdf.open()
    for t in ("page one", "page two"):
        d.new_page().insert_text((72, 72), t)
    return d.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_models):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("BODHAN_OCR_API_KEY", "test")
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"a@example.com", "b@example.com"})
    monkeypatch.setattr(config, "ALLOWED_DOMAIN", "")
    monkeypatch.chdir(tmp_path)

    def fake_run(job_dir):                      # stands in for the paid, minutes-long notebook run
        store.write_json(job_dir / "blocks_pre.json", {"route": "single_gemini", "route_info": {},
                                                       "models": {}, "blocks": copy.deepcopy(BLOCKS)})
    monkeypatch.setattr(ops, "run_pipeline", fake_run)

    def in_process(d, op, req=None):            # same code as the child process, same error type, no spawn
        try:
            return ops.OPS[op](d, req or {})
        except Exception as e:
            raise RuntimeError(f"{type(e).__name__}: {e}")
    monkeypatch.setattr(ops, "call", in_process)

    def google(token):
        sub, email = {"tok-a": ("sub-a", "a@example.com"), "tok-b": ("sub-b", "b@example.com"),
                      "tok-x": ("sub-x", "x@example.com")}[token]
        if not auth.allowed(email):
            raise PermissionError("not allowed")
        return sub, email
    monkeypatch.setattr(auth, "verify_google", google)
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c


def sign_in(c, tok):
    c.cookies.clear()
    r = c.post("/api/auth/google", json={"credential": tok})
    return r


def upload_and_wait(c):
    jid = c.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).json()["job_id"]
    for _ in range(300):
        st = c.get(f"/api/jobs/{jid}").json()
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert st["state"] == "done", st
    return jid


def test_sign_in_rules(client):
    assert client.get("/api/me").status_code == 401
    assert sign_in(client, "tok-x").status_code == 403
    assert sign_in(client, "tok-a").status_code == 200
    assert client.get("/api/me").json() == {"email": "a@example.com"}
    client.cookies.set("session", client.cookies["session"][:-3] + "abc")      # tampered
    assert client.get("/api/me").status_code == 401


def test_upload_limits(client, monkeypatch):
    sign_in(client, "tok-a")
    assert client.post("/api/jobs", files={"file": ("t.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    monkeypatch.setattr(config, "MAX_PAGES", 1)
    assert client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).status_code == 400


def test_jobs_are_private(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    assert [j["job_id"] for j in client.get("/api/jobs").json()] == [jid]
    sign_in(client, "tok-b")
    assert client.get("/api/jobs").json() == []
    assert client.get(f"/api/jobs/{jid}").status_code == 404
    assert client.get(f"/api/jobs/{jid}/doc").status_code == 404
    assert client.get(f"/api/jobs/{jid}/download").status_code == 404


def test_edit_undo_download_cycle(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    doc = client.get(f"/api/jobs/{jid}/doc").json()
    assert doc["dirty"] is False and doc["blocks"]["p2_b1"]["absorbed_by"] == "p1_b4"
    assert client.get(f"/api/jobs/{jid}/pages/1.jpg").headers["content-type"] == "image/jpeg"

    assert client.put(f"/api/jobs/{jid}/blocks/p2_b2", json={"markup": "<P>Edited.</P>"}).json()["cursor"] == 1
    again = client.get(f"/api/jobs/{jid}/doc").json()          # the view is instant and already shows the edit
    assert again["blocks"]["p2_b2"]["markup"] == "<P>Edited.</P>" and again["dirty"] is True
    client.put(f"/api/jobs/{jid}/blocks/p2_b2", json={"markup": "<P>Edited twice.</P>"})
    assert client.put(f"/api/jobs/{jid}/blocks/nope", json={"markup": "x"}).status_code == 400

    from docx import Document
    import io
    r = client.get(f"/api/jobs/{jid}/download")                       # rebuilds first because the document is out of date
    assert r.status_code == 200 and "Edited twice." in "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    assert client.get(f"/api/jobs/{jid}/doc").json()["dirty"] is False

    assert client.post(f"/api/jobs/{jid}/undo").json()["cursor"] == 1
    assert client.get(f"/api/jobs/{jid}/doc").json()["blocks"]["p2_b2"]["markup"] == "<P>Edited.</P>"
    assert client.post(f"/api/jobs/{jid}/redo").json()["cursor"] == 2

    ids = [b["block_id"] for b in BLOCKS]
    assert client.put(f"/api/jobs/{jid}/order", json={"order": ids[1:] + ids[:1]}).status_code == 400


def test_proposal_accept_reject_and_stale(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    prop = client.post(f"/api/jobs/{jid}/review", json={"mode": "format", "blocks": ["p2_b2"], "note": "x"}).json()
    assert prop["status"] == "ready" and prop["blocks"][0]["after"] == "<P>Next paragraph.</P>"

    client.put(f"/api/jobs/{jid}/blocks/p2_b2", json={"markup": "<P>Hand edit.</P>"})          # edited after the proposal
    assert client.post(f"/api/jobs/{jid}/proposals/{prop['id']}/accept", json={}).status_code == 409

    prop2 = client.post(f"/api/jobs/{jid}/review", json={"mode": "format", "blocks": ["p2_b2"]}).json()
    assert prop2["blocks"][0]["before"] == "<P>Hand edit.</P>"
    assert client.post(f"/api/jobs/{jid}/proposals/{prop2['id']}/accept", json={}).status_code == 200
    assert client.post(f"/api/jobs/{jid}/proposals/{prop2['id']}/accept", json={}).status_code == 409   # already applied

    prop3 = client.post(f"/api/jobs/{jid}/review", json={"mode": "reread", "blocks": ["p2_b2"]}).json()
    assert client.post(f"/api/jobs/{jid}/proposals/{prop3['id']}/reject", json={}).status_code == 200
    assert client.post(f"/api/jobs/{jid}/proposals/{prop3['id']}/accept", json={}).status_code == 409
    assert client.post(f"/api/jobs/{jid}/review", json={"mode": "format", "blocks": ["nope"]}).status_code == 422


def test_new_box_accept_adds_block(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    prop = client.post(f"/api/jobs/{jid}/newbox", json={"page": 1, "bbox_norm": [.1, .6, .9, .7]}).json()
    assert client.post(f"/api/jobs/{jid}/proposals/{prop['id']}/accept", json={}).status_code == 200
    doc = client.post(f"/api/jobs/{jid}/rebuild").json()
    assert doc["order"][2:5] == ["p1_b3", "p1_u1", "p1_b4"] and doc["blocks"]["p1_u1"]["markup"] == "<P>alpha beta</P>"


def test_block_image_cannot_leave_the_job_folder(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    evil = dict(BLOCKS[0], block_id="p1_evil", image_path="../../../etc/hostname", is_picture=True)
    store.push(jid, {"op": "add_block", "block": evil, "index": 0})
    assert client.get(f"/api/jobs/{jid}/blocks/p1_evil/image").status_code == 404


def test_delete_endpoint_and_undo(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    assert client.post(f"/api/jobs/{jid}/delete", json={"blocks": ["p1_b2", "p1_b3"]}).json()["cursor"] == 1
    assert client.get(f"/api/jobs/{jid}/doc").json()["order"] == ["p1_b1", "p1_b4", "p2_b1", "p2_b2"]
    assert client.post(f"/api/jobs/{jid}/delete", json={"blocks": ["nope"]}).status_code == 400
    client.post(f"/api/jobs/{jid}/undo")
    assert "p1_b2" in client.get(f"/api/jobs/{jid}/doc").json()["blocks"]


def test_the_delete_shortcut_makes_a_delete_proposal_without_calling_a_model(client, fake_models):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    n = len(fake_models)
    prop = client.post(f"/api/jobs/{jid}/review", json={"mode": "delete", "blocks": ["p1_b2", "p2_b2"], "note": "delete these blocks"}).json()
    assert prop["mode"] == "delete" and [b["id"] for b in prop["blocks"]] == ["p1_b2", "p2_b2"] and len(fake_models) == n
    assert "p1_b2" in client.get(f"/api/jobs/{jid}/doc").json()["blocks"]                    # nothing happens until accepted
    assert client.post(f"/api/jobs/{jid}/proposals/{prop['id']}/accept", json={}).status_code == 200
    order = client.get(f"/api/jobs/{jid}/doc").json()["order"]
    assert "p1_b2" not in order and "p2_b2" not in order
    client.post(f"/api/jobs/{jid}/undo")
    assert "p1_b2" in client.get(f"/api/jobs/{jid}/doc").json()["order"]


def ask(client, jid, note, mode="auto", blocks=("p2_b2",)):
    return client.post(f"/api/jobs/{jid}/review", json={"mode": mode, "blocks": list(blocks), "note": note}).json()


def test_the_shortcut_modes_still_understand_their_messages(client, fake_models):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    assert ask(client, jid, "delete these two blocks", mode="format", blocks=("p1_b2", "p2_b2"))["mode"] == "delete"
    n = len(fake_models)
    m = ask(client, jid, "there is a formating issue here in this block \\\\times", mode="format")
    assert m["mode"] == "ask" and "formula" in m["message"] and m["blocks"] == [] and len(fake_models) == n       # explained, no model call
    f = ask(client, jid, "formating issue", mode="format")
    assert f["mode"] == "format" and f["blocks"]
    r = ask(client, jid, "this is wrong", mode="reread")
    assert r["mode"] == "reread" and r["blocks"]


def test_an_explicit_choice_is_respected_but_delete_words_still_ask_to_delete(client, fake_models):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    assert ask(client, jid, "centre it", mode="format")["mode"] == "format"
    assert ask(client, jid, "delete these", mode="format")["mode"] == "delete"
    assert ask(client, jid, "remove the extra space in B3", mode="format")["mode"] == "ask"         # part of a block: do not delete the block
    assert client.post(f"/api/jobs/{jid}/review", json={"mode": "nonsense", "blocks": ["p2_b2"], "note": ""}).status_code == 422


def test_inserting_a_block_without_a_box_and_undoing_it(client):
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    tbl = '<TABLE border="1"><ROW header="true"><CELL></CELL><CELL></CELL></ROW><ROW><CELL></CELL><CELL></CELL></ROW></TABLE>'
    r = client.post(f"/api/jobs/{jid}/blocks", json={"after": "p1_b3", "markup": tbl, "type": "Table"}).json()
    order = client.get(f"/api/jobs/{jid}/doc").json()["order"]
    assert r["id"] == "p1_n1" and order[order.index("p1_b3") + 1] == "p1_n1"
    b = client.get(f"/api/jobs/{jid}/doc").json()["blocks"]["p1_n1"]
    assert b["markup"] == tbl and b["bbox_norm"] is None and b["type"] == "Table"
    assert client.post(f"/api/jobs/{jid}/blocks", json={"after": "p1_b3", "markup": "<P>x</P>"}).json()["id"] == "p1_n2"
    assert client.post(f"/api/jobs/{jid}/blocks", json={"after": "nope", "markup": "<P>x</P>"}).status_code == 422
    client.post(f"/api/jobs/{jid}/undo"); client.post(f"/api/jobs/{jid}/undo")
    assert "p1_n1" not in client.get(f"/api/jobs/{jid}/doc").json()["order"]
    client.post(f"/api/jobs/{jid}/rebuild")                                                  # and the document still builds


def test_daily_page_limit_per_person(client, monkeypatch):
    monkeypatch.setattr(config, "USER_DAILY_PAGES", 5)
    sign_in(client, "tok-a")
    assert client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).status_code == 200          # 2 pages
    assert client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).status_code == 200          # 4 pages
    r = client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")})                               # would be 6
    assert r.status_code == 429 and "Daily limit" in r.json()["detail"]
    sign_in(client, "tok-b")                                                                                          # someone else is unaffected
    assert client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).status_code == 200
    monkeypatch.setattr(config, "USER_DAILY_PAGES", 0)                                                                # 0 switches it off
    sign_in(client, "tok-a")
    assert client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).status_code == 200
    for who in ("tok-a", "tok-b"):                                                                                    # let the background runs end
        sign_in(client, who)
        for _ in range(300):
            if all(j["state"] in ("done", "error") for j in client.get("/api/jobs").json()):
                break
            time.sleep(0.1)


def test_who_may_sign_in(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"a@example.com"}); monkeypatch.setattr(config, "ALLOWED_DOMAIN", "")
    assert auth.allowed("A@example.com") and not auth.allowed("x@gmail.com")
    monkeypatch.setattr(config, "ALLOWED_DOMAIN", "gmail.com")
    assert auth.allowed("anyone@gmail.com") and not auth.allowed("x@outlook.com") and not auth.allowed("a@gmail.com.evil.com")
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"*"}); monkeypatch.setattr(config, "ALLOWED_DOMAIN", "")
    assert auth.allowed("x@outlook.com")


def test_saving_never_waits_for_a_rebuild(client, monkeypatch):
    import threading
    sign_in(client, "tok-a")
    jid = upload_and_wait(client)
    real = ops.call

    def slow(d, op, req=None):
        if op == "rebuild":
            time.sleep(1.5)
        return real(d, op, req)
    monkeypatch.setattr(ops, "call", slow)
    t = threading.Thread(target=lambda: client.post(f"/api/jobs/{jid}/rebuild")); t.start()
    time.sleep(0.3)                                                     # the rebuild is now running
    t0 = time.time()
    ids = ["p1_b2", "p1_b1", "p1_b3", "p1_b4", "p2_b1", "p2_b2"]
    assert client.put(f"/api/jobs/{jid}/order", json={"order": ids}).status_code == 200
    assert client.put(f"/api/jobs/{jid}/blocks/p2_b2", json={"markup": "<P>x</P>"}).status_code == 200
    assert time.time() - t0 < 0.8, time.time() - t0
    t.join()


# ── the assistant: any request about the selected blocks ──────────────────
MERGED = "<P>The current through a conductor is directly proportional to the potential difference across its ends.</P>"
TABLE = '<TABLE border="1"><ROW header="true"><CELL>A</CELL><CELL>B</CELL></ROW><ROW><CELL>1</CELL><CELL>2</CELL></ROW></TABLE>'


@pytest.fixture
def assistant(monkeypatch):
    """Stands in for the model: answers every assistant call with `says[0]` and records what it was sent."""
    import requests
    from tests.conftest import FakeResp
    box = {"says": "", "sent": []}

    def post(url, *a, **kw):
        body = kw.get("json") or {}
        box["sent"].append(body)
        return FakeResp({"choices": [{"message": {"content": box["says"]}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}, "model": body.get("model")})
    monkeypatch.setattr(requests, "post", post)
    return box


def assist(client, jid, note, blocks):
    r = client.post(f"/api/jobs/{jid}/review", json={"mode": "auto", "blocks": list(blocks), "note": note})
    return r.json() if r.status_code == 200 else r


def doc(client, jid):
    return client.get(f"/api/jobs/{jid}/doc").json()


def test_the_assistant_gets_the_blocks_their_tags_the_page_and_a_crop(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "<REPLY>Nothing to do.</REPLY>"
    p = assist(client, jid, "combine these two", ("p1_b4", "p2_b1"))
    sent = assistant["sent"][0]
    text = next(c["text"] for c in sent["messages"][0]["content"] if c.get("type") == "text")
    assert sent["model"] == QWEN and "combine these two" in text and "TAG REFERENCE" in text
    assert '<BLOCK id="p1_b4"' in text and "directly proportional to the</P>" in text and "PAGE OUTLINE" in text and "p1_b3 -" in text
    kinds = [c.get("type") for c in sent["messages"][0]["content"]]
    assert kinds.count("image_url") == 2 + 2                                # two pages and two block crops
    assert p["mode"] == "assist" and p["blocks"] == [] and p["message"] == "Nothing to do." and p["alternatives"] == []


def test_merging_two_blocks_is_one_card_one_undo(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = f'<REPLY>The sentence continues, so I joined them.</REPLY>\n<ACTION type="merge" ids="p2_b1,p1_b4">\n{MERGED}\n</ACTION>'
    p = assist(client, jid, "we should combine these two, flow continuation", ("p2_b1", "p1_b4"))
    (c,) = p["blocks"]
    assert c["kind"] == "merge" and c["ids"] == ["p1_b4", "p2_b1"] and c["warn"] is None and c["after"] == MERGED     # reading order, not click order
    assert doc(client, jid)["blocks"]["p2_b1"]                                                                          # nothing changed yet
    assert client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={}).status_code == 200
    d = doc(client, jid)
    assert "p2_b1" not in d["order"] and d["blocks"]["p1_b4"]["markup"] == MERGED
    client.post(f"/api/jobs/{jid}/undo")
    d = doc(client, jid)
    assert "p2_b1" in d["order"] and d["blocks"]["p1_b4"]["markup"] == BLOCKS[3]["formatted_text"]


def test_a_merge_that_loses_words_is_flagged(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="merge" ids="p1_b4,p2_b1"><P>The current through a conductor is proportional to its ends.</P></ACTION>'
    (c,) = assist(client, jid, "merge", ("p1_b4", "p2_b1"))["blocks"]
    assert "differ" in c["warn"]


def test_adding_a_table_after_a_block(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = f'<REPLY>Added a table.</REPLY><ACTION type="insert" after="p1_b3">{TABLE}</ACTION>'
    p = assist(client, jid, "add a table of this after this block", ("p1_b3",))
    (c,) = p["blocks"]
    assert c["kind"] == "insert" and c["label"] == "new block" and c["after"] == TABLE
    assert client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={}).status_code == 200
    d = doc(client, jid)
    assert d["order"][d["order"].index("p1_b3") + 1] == "p1_n1" and d["blocks"]["p1_n1"]["type"] == "Table" and d["blocks"]["p1_n1"]["markup"] == TABLE
    client.post(f"/api/jobs/{jid}/undo")
    assert "p1_n1" not in doc(client, jid)["order"]
    client.post(f"/api/jobs/{jid}/rebuild")                                  # the document still builds


def test_recorrecting_a_block_from_the_image_flags_changed_words_and_nothing_else(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="edit" id="p2_b2"><P>Next paragraph!</P></ACTION>'
    (c,) = assist(client, jid, "check the image with the one you generated, it is wrong, recorrect", ("p2_b2",))["blocks"]
    assert c["kind"] == "edit" and "words changed" in c["warn"].lower() and c["fields"] == {"formatted_text": "<P>Next paragraph!</P>"}
    assistant["says"] = '<ACTION type="edit" id="p2_b2"><P align="center">Next paragraph.</P></ACTION>'
    (c,) = assist(client, jid, "centre it", ("p2_b2",))["blocks"]
    assert c["warn"] is None


def test_bad_actions_are_skipped_with_a_reason_not_applied(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = ('<ACTION type="edit" id="p1_b1"><P>not selected</P></ACTION>'            # not one of the selected blocks
                         '<ACTION type="edit" id="p2_b2"><P>broken</ACTION>'                       # unbalanced tags
                         '<ACTION type="insert" after="nowhere"><P>x</P></ACTION>'                # unknown anchor
                         '<ACTION type="merge" ids="p2_b2"><P>x</P></ACTION>'                     # a merge needs two
                         '<ACTION type="shuffle" id="p2_b2"/>')
    p = assist(client, jid, "do things", ("p2_b2",))
    assert p["blocks"] == [] and len(p["skipped"]) == 5
    assert "not one of the selected" in p["skipped"][0]["reason"] and "balanced" in p["skipped"][1]["reason"]


def test_one_block_cannot_be_used_by_two_actions_and_delete_works(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = ('<ACTION type="delete" ids="p2_b1,p2_b2"/>'
                         '<ACTION type="edit" id="p2_b2"><P>again</P></ACTION>'
                         f'<ACTION type="insert" after="p2_b2">{TABLE}</ACTION>')                  # anchor is being deleted
    p = assist(client, jid, "remove these", ("p2_b1", "p2_b2"))
    assert [c["kind"] for c in p["blocks"]] == ["delete", "delete"] and len(p["skipped"]) == 2
    assert client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={}).status_code == 200
    assert "p2_b1" not in doc(client, jid)["order"]


def test_an_edit_made_after_the_proposal_blocks_accepting_it(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="edit" id="p2_b2"><P align="right">Next paragraph.</P></ACTION>'
    p = assist(client, jid, "right align", ("p2_b2",))
    client.put(f"/api/jobs/{jid}/blocks/p2_b2", json={"markup": "<P>I typed this</P>"})
    r = client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    assert r.status_code == 409 and "edited after" in r.json()["detail"]
    assert doc(client, jid)["blocks"]["p2_b2"]["markup"] == "<P>I typed this</P>"


def test_an_unreadable_answer_is_an_error_not_a_silent_no_change(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "I think you should look at it again."
    r = assist(client, jid, "merge", ("p1_b4", "p2_b1"))
    assert r.status_code == 422 and "could not be read" in r.json()["detail"]


def test_selection_limits_and_the_prompt_file(client, assistant, tmp_path):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "<REPLY>ok</REPLY>"
    assert assist(client, jid, "x", ("p1_b1", "p2_b1"))["mode"] == "assist"
    assert "{note}" in (tmp_path / "prompts" / "assist.txt").read_text(encoding="utf-8")


def prompt_text(box):
    return next(c["text"] for c in box["sent"][-1]["messages"][0]["content"] if c.get("type") == "text")


def test_a_merge_grows_the_box_remembers_its_parts_and_can_be_split_back(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    original = {b["block_id"]: b for b in store.current_blocks(jid) if b["block_id"] in ("p1_b2", "p1_b3")}
    assistant["says"] = '<REPLY>Joined.</REPLY><ACTION type="merge" ids="p1_b2,p1_b3"><P>State Ohms law. Quantity Symbol Current I</P></ACTION>'
    p = assist(client, jid, "merge these two, they are side by side", ("p1_b2", "p1_b3"))
    client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    merged = {b["block_id"]: b for b in store.current_blocks(jid)}["p1_b2"]
    assert merged["bbox_norm"] == [.1, .15, .9, .55]                                       # the box now covers both pieces
    assert [a["block_id"] for a in merged["merged_from"]] == ["p1_b2", "p1_b3"]
    assert doc(client, jid)["blocks"]["p1_b2"]["bbox_norm"] == [.1, .15, .9, .55] and "p1_b3" not in doc(client, jid)["order"]

    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "now move the gap", ("p1_b2",))                                     # a follow-up: it must see the parts and the earlier turn
    t = prompt_text(assistant)
    assert 'parts="p1_b2 [page 1' in t and "p1_b3 [page 1" in t and "EARLIER IN THIS SESSION" in t
    assert "merge these two, they are side by side" in t and "Merge p1 \u00b7 B2 + p1 \u00b7 B3: accepted" in t

    assistant["says"] = '<ACTION type="split" id="p1_b2"/>'
    p = assist(client, jid, "split them again", ("p1_b2",))
    (c,) = p["blocks"]
    assert c["kind"] == "split"
    assert client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={}).status_code == 200
    now = {b["block_id"]: b for b in store.current_blocks(jid)}
    order = doc(client, jid)["order"]
    assert order[order.index("p1_b2") + 1] == "p1_b3" and not now["p1_b2"].get("merged_from")
    for i in ("p1_b2", "p1_b3"):
        assert now[i]["bbox_norm"] == original[i]["bbox_norm"] and now[i]["formatted_text"] == original[i]["formatted_text"]
    client.post(f"/api/jobs/{jid}/undo")                                                    # undo the split: merged again, in one step
    assert "p1_b3" not in doc(client, jid)["order"]


def test_merging_across_pages_keeps_the_first_box_and_sends_the_other_part_as_a_crop(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = f'<ACTION type="merge" ids="p1_b4,p2_b1">{MERGED}</ACTION>'
    p = assist(client, jid, "the sentence continues on the next page", ("p1_b4", "p2_b1"))
    client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    b = {x["block_id"]: x for x in store.current_blocks(jid)}["p1_b4"]
    assert b["bbox_norm"] == [.1, .85, .9, .95] and [a["page"] for a in b["merged_from"]] == [1, 2]
    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "check it", ("p1_b4",))
    kinds = [c.get("type") for c in assistant["sent"][-1]["messages"][0]["content"]]
    assert kinds.count("image_url") == 1 + 1 + 1                                            # page 1, the block's crop, the part on page 2


def test_splitting_a_block_that_was_never_merged_is_refused(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="split" id="p1_b2"/>'
    p = assist(client, jid, "split", ("p1_b2",))
    assert p["blocks"] == [] and "nothing to split" in p["skipped"][0]["reason"]
