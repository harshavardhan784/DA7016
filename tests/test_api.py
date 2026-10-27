import copy
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from conftest import BLOCKS
from ocr import api, auth, config, ops, store

QWEN = "qwen/qwen3-vl-30b-a3b-instruct"          # the model the notebook names for its 'classify' stage
GPT = "openai/gpt-4.1-mini"                       # the assistant's model unless the server sets another


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


def test_upload_only_checks_that_it_is_a_pdf_and_its_size(client, monkeypatch):
    import pymupdf
    sign_in(client, "tok-a")
    assert client.post("/api/jobs", files={"file": ("t.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    big = pymupdf.open()
    for _ in range(60):                                                              # far more pages than the old limit of 20: accepted
        big.new_page()
    r = client.post("/api/jobs", files={"file": ("t.pdf", big.tobytes(), "application/pdf")})
    assert r.status_code == 200 and r.json()["pages"] == 60
    assert "max_pages" not in client.get("/api/config").json()
    monkeypatch.setattr(config, "MAX_MB", 0)                                         # the size limit is still there
    assert client.post("/api/jobs", files={"file": ("t.pdf", _pdf(), "application/pdf")}).status_code == 413


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


def test_there_is_no_daily_limit(client):
    import pymupdf
    sign_in(client, "tok-a")
    big = pymupdf.open()
    for _ in range(60):
        big.new_page()
    for _ in range(3):                                                                                                # 180 pages in a row: far above the old 100 a day
        assert client.post("/api/jobs", files={"file": ("t.pdf", big.tobytes(), "application/pdf")}).status_code == 200
    for _ in range(300):                                                                                              # let the background runs end
        if all(j["state"] in ("done", "error") for j in client.get("/api/jobs").json()):
            break
        time.sleep(0.1)


def test_who_may_sign_in(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"a@example.com"})
    assert auth.allowed("A@example.com") and not auth.allowed("x@gmail.com") and not auth.allowed("anyone@example.com") and not auth.allowed("a@example.com.evil.com")
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"*"})
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
    from conftest import FakeResp
    box = {"says": "", "sent": []}

    def post(url, *a, **kw):
        body = kw.get("json") or {}
        box["sent"].append(body)
        return FakeResp({"choices": [{"message": {"content": box["says"]}, "finish_reason": box.get("finish", "stop")}],
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
    assert sent["model"] == GPT and "combine these two" in text and "TAG REFERENCE" in text
    assert '<BLOCK id="p1_b4"' in text and "directly proportional to the</P>" in text and "PAGE OUTLINE" in text and "p1_b3 -" in text
    kinds = [c.get("type") for c in sent["messages"][0]["content"]]
    assert kinds.count("image_url") == 2 + 2                                # two pages and two block crops
    assert p["mode"] == "assist" and p["blocks"] == [] and p["message"] == "Nothing to do." and p["alternatives"] == []


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
    assert "is not selected" in p["skipped"][0]["reason"] and "balanced" in p["skipped"][1]["reason"]


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


def test_splitting_a_block_that_was_never_merged_is_refused(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="split" id="p1_b2"/>'
    p = assist(client, jid, "split", ("p1_b2",))
    assert p["blocks"] == [] and "nothing to split" in p["skipped"][0]["reason"]


def n_images(box):
    return sum(c.get("type") == "image_url" for c in box["sent"][-1]["messages"][0]["content"])


def test_a_layout_request_sends_only_the_block_crop_and_may_not_change_the_words(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<REPLY>Plain paragraphs.</REPLY><ACTION type="edit" id="p2_b2"><P>Next paragraph.</P><P>extra words</P></ACTION>'
    p = assist(client, jid, "remove the table outline, not the text", ("p2_b2",))
    assert n_images(assistant) == 1 and "a crop of each selected block only" in prompt_text(assistant)      # no whole page
    assert p["blocks"] == [] and "changed the words" in p["skipped"][0]["reason"]                           # layout request, words changed: not offered
    assistant["says"] = '<ACTION type="edit" id="p2_b2"><P align="center">Next paragraph.</P></ACTION>'
    assert len(assist(client, jid, "centre it", ("p2_b2",))["blocks"]) == 1                                 # same words, new layout: offered


def test_a_request_about_words_or_structure_gets_the_whole_page(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "check this against the image and correct it", ("p2_b2",))
    assert n_images(assistant) == 2 and "the whole page and a crop" in prompt_text(assistant)
    assist(client, jid, "merge these", ("p2_b1", "p2_b2"))
    assert n_images(assistant) == 1 + 2


def test_the_fast_provider_option_is_tried_first_and_dropped_if_refused(client, monkeypatch):
    import requests
    from conftest import FakeResp
    sent = []

    def post(url, *a, **kw):
        body = kw.get("json") or {}
        sent.append(body)
        if "provider" in body:
            r = FakeResp({}); r.status_code = 400
            return r
        return FakeResp({"choices": [{"message": {"content": "<REPLY>fine</REPLY>"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0}, "model": body.get("model")})
    monkeypatch.setattr(requests, "post", post)
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    p = assist(client, jid, "what is this?", ("p2_b2",))
    assert [("provider" in b) for b in sent] == [True, False] and p["message"] == "fine" and "seconds" in p["usage"]


def test_a_reply_never_shows_tags(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<REPLY><ACTION type="delete" ids="p2_b1"/></REPLY>'
    p = assist(client, jid, "remove it", ("p2_b1",))
    assert "<" not in p["message"] and p["message"] == "Here is what I propose." and p["blocks"][0]["kind"] == "delete"
    assistant["says"] = '<REPLY>I will delete it.<ACTION type="delete" ids="p2_b1"/>'            # no closing tag
    assert assist(client, jid, "remove it", ("p2_b1",))["message"] == "I will delete it."


def test_delete_plus_insert_becomes_an_edit_in_place(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="delete" ids="p2_b2"/><ACTION type="insert" after="p2_b1"><P align="center">Next paragraph.</P></ACTION>'
    p = assist(client, jid, "centre it", ("p2_b2",))
    (c,) = p["blocks"]
    assert c["kind"] == "edit" and c["ids"] == ["p2_b2"]                                        # same block, so it keeps its box on the page
    client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    d = doc(client, jid)
    assert d["blocks"]["p2_b2"]["markup"] == '<P align="center">Next paragraph.</P>' and not any("_n" in i for i in d["order"])
    assert d["blocks"]["p2_b2"]["bbox_norm"] == [x for x in BLOCKS[5]["bbox_norm"]] if "bbox_norm" in BLOCKS[5] else True


def test_a_block_that_is_not_selected_cannot_be_changed(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="edit" id="p2_b1"><P>x</P></ACTION>'
    p = assist(client, jid, "fix", ("p2_b2",))
    assert p["blocks"] == [] and "is not selected" in p["skipped"][0]["reason"]


def test_a_picture_or_empty_block_can_be_read_from_its_crop_by_the_assistant(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    store.push(jid, {"op": "set_fields", "block": "p2_b2", "fields": {"is_picture": True, "formatted_text": ""}})
    assistant["says"] = '<REPLY>Read it.</REPLY><ACTION type="edit" id="p2_b2"><P>Text printed in the crop</P></ACTION>'
    p = assist(client, jid, "read this block again from the image", ("p2_b2",))
    (c,) = p["blocks"]
    assert c["label"] == "read from image" and c["warn"] is None and c["fields"]["is_picture"] is False
    client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    b = {x["block_id"]: x for x in store.current_blocks(jid)}["p2_b2"]
    assert b["is_picture"] is False and b["bodhan_type"] == "Text" and b["formatted_text"] == "<P>Text printed in the crop</P>"


def test_an_answer_that_was_cut_off_is_an_error_and_long_hindi_blocks_get_room(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="edit" id="p2_b2"><P>Next para'
    assistant["finish"] = "length"
    r = assist(client, jid, "centre it", ("p2_b2",))
    assert r.status_code == 422 and "cut off" in r.json()["detail"]
    assistant["finish"] = "stop"
    store.push(jid, {"op": "set_fields", "block": "p2_b2", "fields": {"formatted_text": "<P>" + "प्रश्न " * 400 + "</P>"}})
    assist(client, jid, "centre it", ("p2_b2",))
    assert assistant["sent"][-1]["max_tokens"] >= 1500 + 2 * 2800                       # about 2,800 characters of Hindi need about that many tokens


def test_the_assistant_model_and_answer_length_can_be_set(client, assistant, monkeypatch):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "what is this?", ("p2_b2",))
    assert assistant["sent"][-1]["model"] == GPT and assistant["sent"][-1]["max_tokens"] == 1500 + 2 * len(BLOCKS[5]["formatted_text"])   # defaults
    monkeypatch.setattr(config, "ASSIST_MODEL", "google/gemini-2.5-flash")
    monkeypatch.setattr(config, "ASSIST_MAX_TOKENS", 3333)
    p = assist(client, jid, "what is this?", ("p2_b2",))
    assert assistant["sent"][-1]["model"] == "google/gemini-2.5-flash" and assistant["sent"][-1]["max_tokens"] == 3333 and p["model"] == "google/gemini-2.5-flash"


REAL = ('<TABLE border="1"><ROW header="true"><CELL><P>explained Reason (R).<BR/>d) Both are true</P></CELL></ROW>'
        '<ROW><CELL><TABLE border="1"><ROW><CELL colspan="2"><P align="center" bold="true" size="11">VERY SHORT ANSWER TYPE QUESTIONS<BR/>(2 MARKS EACH)</P></CELL></ROW></TABLE></CELL></ROW>'
        '<ROW><CELL><P>\u092a\u094d\u0930\u0936\u094d\u0928 16 \u0921\u0942\u0930\u0923\u094d\u0921 \u0915\u092a<BR/>(\u0905\u0925\u0935\u093e)</P></CELL></ROW>'
        '<ROW><CELL>plain text cell</CELL></ROW></TABLE>')


def put_table(jid, block="p2_b2"):
    store.push(jid, {"op": "set_fields", "block": block, "fields": {"formatted_text": REAL}})


def test_removing_a_table_outline_is_done_in_code_with_no_model_call(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    put_table(jid)
    p = assist(client, jid, "I want you remove the table outline just keep the inside text intact and structure", ("p2_b2",))
    assert assistant["sent"] == [] and p["model"] == "built in" and p["usage"]["calls"] == 0                 # no model was asked
    (c,) = p["blocks"]
    assert c["label"] == "table outline removed" and c["warn"] is None
    after = c["after"]
    assert after.count("<TABLE") == 1 and "<ROW" in after and after.lstrip().startswith('<P bold="true">')   # the outer outline is gone; the heading box inside stays; the header row stays bold
    assert "<P>plain text cell</P>" in after
    assert client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={}).status_code == 200
    assert doc(client, jid)["blocks"]["p2_b2"]["markup"] == after
    client.post(f"/api/jobs/{jid}/undo")
    assert doc(client, jid)["blocks"]["p2_b2"]["markup"] == REAL


def test_all_table_outlines_can_be_removed_and_the_words_never_change(client, assistant):
    import re
    from ocr import ops
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    put_table(jid)
    p = assist(client, jid, "remove all table outlines", ("p2_b2",))
    (c,) = p["blocks"]
    assert "<TABLE" not in c["after"] and "kept" not in p["message"] and ops._same_words(c["after"], REAL)
    assert "kept" in assist(client, jid, "remove the outside table border", ("p2_b2",))["message"]           # the default keeps tables inside cells


def test_the_outline_request_goes_to_the_model_when_there_is_no_table_or_the_words_differ(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "<REPLY>There is no table here.</REPLY>"
    p = assist(client, jid, "remove the table outline", ("p2_b2",))                                          # p2_b2 has no table: ask the model
    assert len(assistant["sent"]) == 1 and p["message"] == "There is no table here."
    put_table(jid)
    assistant["says"] = '<ACTION type="unwrap" id="P2_B2" all="true"/>'                                       # other wording: the model picks the same exact operation
    p = assist(client, jid, "make this plain paragraphs, no grid", ("p2_b2",))
    assert len(assistant["sent"]) == 2 and "<TABLE" not in p["blocks"][0]["after"]
    assistant["says"] = '<ACTION type="unwrap" id="p2_b1"/>'
    assert "has no table" in assist(client, jid, "make this plain paragraphs, no grid", ("p2_b1",))["skipped"][0]["reason"]


def test_words_of_a_table_survive_the_outline_removal_in_the_word_file(client, assistant):
    """The Word file of the outline-free block has the same text as the Word file of the table."""
    from ocr import ops
    old, new = REAL, ops._unwrap_all(REAL)
    import re
    flat = lambda m: re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", m))
    assert flat(old) == flat(new)

def by_id(jid):
    return {b["block_id"]: b for b in store.current_blocks(jid)}


def accept_all(client, jid, p):
    r = client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    assert r.status_code == 200, r.text
    return r


def merge(client, assistant, jid, ids, note="merge these", words=MERGED):
    assistant["says"] = f'<REPLY>Joined.</REPLY><ACTION type="merge" ids="{",".join(ids)}">{words}</ACTION>'
    p = assist(client, jid, note, ids)
    accept_all(client, jid, p)
    return p


def test_merging_two_blocks_gives_one_new_block_in_their_place_and_one_undo(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    before = doc(client, jid)["order"]
    assistant["says"] = f'<REPLY>The sentence continues, so I joined them.</REPLY>\n<ACTION type="merge" ids="p2_b1,p1_b4">\n{MERGED}\n</ACTION>'
    p = assist(client, jid, "we should combine these two, flow continuation", ("p2_b1", "p1_b4"))
    (c,) = p["blocks"]
    assert c["kind"] == "merge" and c["ids"] == ["p1_b4", "p2_b1"] and c["warn"] is None and c["after"] == MERGED     # reading order, not click order
    assert doc(client, jid)["blocks"]["p2_b1"]                                                                          # nothing changed yet
    accept_all(client, jid, p)
    d = doc(client, jid)
    assert "p1_b4" not in d["order"] and "p2_b1" not in d["order"]
    (new,) = [i for i in d["order"] if i not in before]
    assert new == "p1_b4_b1" and d["order"].index(new) == before.index("p1_b4") and d["blocks"][new]["markup"] == MERGED   # the pieces' ids joined; the first piece's place
    client.post(f"/api/jobs/{jid}/undo")
    d = doc(client, jid)
    assert d["order"] == before and d["blocks"]["p1_b4"]["markup"] == BLOCKS[3]["formatted_text"]


def test_a_merge_grows_the_box_names_its_parts_and_can_be_split_back(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    original = {i: b for i, b in by_id(jid).items() if i in ("p1_b2", "p1_b3")}
    before = doc(client, jid)["order"]
    merge(client, assistant, jid, ("p1_b2", "p1_b3"), "merge these two, they are side by side", "<P>State Ohms law. Quantity Symbol Current I</P>")
    m = by_id(jid)["p1_b2_b3"]
    assert m["bbox_norm"] == [.1, .15, .9, .55] and [a["block_id"] for a in m["merged_from"]] == ["p1_b2", "p1_b3"]      # the box covers both pieces
    assert doc(client, jid)["blocks"]["p1_b2_b3"]["bbox_norm"] == [.1, .15, .9, .55]

    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "now move the gap", ("p1_b2_b3",))                                  # a follow-up on the NEW name
    t = prompt_text(assistant)
    assert 'parts="p1_b2 [page 1' in t and "p1_b3 [page 1" in t and "CONTEXT OF EACH SELECTED BLOCK (what" in t and "EARLIER IN THIS SESSION" not in t
    assert "p1_b2_b3:" in t and "merge these two, they are side by side" in t and "merged p1 \u00b7 B2, p1 \u00b7 B3 into the new block p1 \u00b7 B2_B3" in t

    assistant["says"] = '<ACTION type="split" id="p1_b2_b3"/>'
    p = assist(client, jid, "split them again", ("p1_b2_b3",))
    (c,) = p["blocks"]
    assert c["kind"] == "split"
    accept_all(client, jid, p)
    now, order = by_id(jid), doc(client, jid)["order"]
    assert order == before and "p1_b2_b3" not in now
    for i in ("p1_b2", "p1_b3"):
        assert now[i]["bbox_norm"] == original[i]["bbox_norm"] and now[i]["formatted_text"] == original[i]["formatted_text"] and not now[i].get("merged_from")
    client.post(f"/api/jobs/{jid}/undo")                                                    # undo the split: merged again, in one step
    assert "p1_b2_b3" in doc(client, jid)["order"] and "p1_b3" not in doc(client, jid)["order"]


def test_merging_across_pages_keeps_the_first_box_and_sends_the_other_part_as_a_crop(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    merge(client, assistant, jid, ("p1_b4", "p2_b1"), "the sentence continues on the next page")
    b = by_id(jid)["p1_b4_b1"]
    assert b["page"] == 1 and b["bbox_norm"] == [.1, .85, .9, .95] and [a["page"] for a in b["merged_from"]] == [1, 2]
    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "check it", ("p1_b4_b1",))
    kinds = [c.get("type") for c in assistant["sent"][-1]["messages"][0]["content"]]
    assert kinds.count("image_url") == 1 + 1 + 1                                            # page 1, the block's crop, the part on page 2


def test_ids_are_matched_without_case_and_a_merged_away_block_is_explained(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = f'<ACTION type="merge" ids="P1_B2, p1_B3">{MERGED}</ACTION>'            # the model wrote the ids in another case
    p = assist(client, jid, "merge", ("p1_b2", "p1_b3"))
    assert [c["ids"] for c in p["blocks"]] == [["p1_b2", "p1_b3"]]
    accept_all(client, jid, p)
    assistant["says"] = '<ACTION type="merge" ids="p1_b2_b3,P1_B3"><P>again</P></ACTION>'       # B3 was merged away: asked for again
    p = assist(client, jid, "there is a gap between them", ("p1_b2_b3",))
    assert p["blocks"] == [] and "no longer exists: it was merged into p1 \u00b7 B2_B3" in p["skipped"][0]["reason"]


def test_a_merged_block_has_the_context_of_both_pieces_in_order_and_each_piece_has_the_merge(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    for blk, note in (("p1_b2", "first thing about B2"), ("p1_b3", "then something about B3")):
        assistant["says"] = f'<REPLY>Done {blk}.</REPLY><ACTION type="edit" id="{blk}"><P align="center">{blk}</P></ACTION>'
        accept_all(client, jid, assist(client, jid, note, (blk,)))
    assert [e["asked"] for e in by_id(jid)["p1_b2"]["context"]] == ["first thing about B2"]                    # kept on the block itself
    merge(client, assistant, jid, ("p1_b2", "p1_b3"), "now join them")
    m = by_id(jid)["p1_b2_b3"]
    assert [e["asked"] for e in m["context"]] == ["first thing about B2", "then something about B3", "now join them"]   # both, in order, the merge once
    assert [e["said"] for e in m["context"]][:2] == ["Done p1_b2.", "Done p1_b3."]
    for atom in m["merged_from"]:                                                                              # the merge is in B2's context and in B3's context
        assert atom["context"][-1]["asked"] == "now join them" and atom["context"][-1]["did"].startswith("merged p1 \u00b7 B2, p1 \u00b7 B3 into the new block")
    assert len({e["eid"] for e in m["context"]}) == 3


def test_a_split_gives_each_piece_its_own_context_back_plus_what_happened_since(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="edit" id="p1_b2"><P align="center">x</P></ACTION>'
    accept_all(client, jid, assist(client, jid, "tidy B2", ("p1_b2",)))
    merge(client, assistant, jid, ("p1_b2", "p1_b3"), "join")
    assistant["says"] = '<ACTION type="edit" id="p1_b2_b3"><P align="right">y</P></ACTION>'
    accept_all(client, jid, assist(client, jid, "tidy the merged one", ("p1_b2_b3",)))
    assistant["says"] = '<ACTION type="split" id="p1_b2_b3"/>'
    accept_all(client, jid, assist(client, jid, "split", ("p1_b2_b3",)))
    now = by_id(jid)
    assert [e["asked"] for e in now["p1_b2"]["context"]] == ["tidy B2", "join", "tidy the merged one", "split"]
    assert [e["asked"] for e in now["p1_b3"]["context"]] == ["join", "tidy the merged one", "split"]    # B3 never had its own earlier entry


def test_merging_a_merged_block_keeps_every_original_piece_and_one_flat_name(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    merge(client, assistant, jid, ("p1_b2", "p1_b3"), "first join")
    merge(client, assistant, jid, ("p1_b2_b3", "p1_b4"), "second join")
    m = by_id(jid)["p1_b2_b3_b4"]
    assert [a["block_id"] for a in m["merged_from"]] == ["p1_b2", "p1_b3", "p1_b4"]
    assert [e["asked"] for e in m["context"]] == ["first join", "second join"]
    assert all(a["context"][-1]["asked"] == "second join" for a in m["merged_from"])


def test_the_model_sees_only_the_context_of_the_selected_blocks_and_rejections(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "first", ("p1_b2",))
    assert "CONTEXT OF EACH SELECTED BLOCK (what" not in prompt_text(assistant)                                   # nothing yet: no section
    assistant["says"] = '<ACTION type="edit" id="p1_b2"><P align="center">x</P></ACTION>'
    accept_all(client, jid, assist(client, jid, "the B2 request", ("p1_b2",)))
    assistant["says"] = '<ACTION type="edit" id="p1_b3"><P align="right">y</P></ACTION>'
    p = assist(client, jid, "the rejected B3 request", ("p1_b3",))
    client.post(f"/api/jobs/{jid}/proposals/{p['id']}/reject", json={})
    assistant["says"] = "<REPLY>ok</REPLY>"
    assist(client, jid, "look at both", ("p1_b2", "p1_b3"))
    t = prompt_text(assistant)
    assert "p1_b2:\n- asked \u00abthe B2 request\u00bb" in t and "the rejected B3 request" in t and "REJECTED" in t
    assist(client, jid, "only B4", ("p1_b4",))
    assert "the B2 request" not in prompt_text(assistant) and "rejected B3" not in prompt_text(assistant)         # other blocks' context is not mixed in


def test_undo_takes_the_context_away_with_the_edit(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    assistant["says"] = '<ACTION type="edit" id="p1_b2"><P align="center">x</P></ACTION>'
    accept_all(client, jid, assist(client, jid, "tidy it", ("p1_b2",)))
    assert len(by_id(jid)["p1_b2"]["context"]) == 1
    client.post(f"/api/jobs/{jid}/undo")
    assert not by_id(jid)["p1_b2"].get("context")
    client.post(f"/api/jobs/{jid}/redo")
    assert by_id(jid)["p1_b2"]["context"][0]["asked"] == "tidy it"


def test_a_split_is_refused_if_an_original_id_has_been_taken(client, assistant):
    sign_in(client, "tok-a"); jid = upload_and_wait(client)
    merge(client, assistant, jid, ("p1_b2", "p1_b3"), "join")
    assistant["says"] = '<ACTION type="split" id="p1_b2_b3"/>'
    p = assist(client, jid, "split", ("p1_b2_b3",))
    store.push(jid, {"op": "add_block", "index": 0, "block": {"block_id": "p1_b3", "page": 1, "formatted_text": "<P>taken</P>"}})
    r = client.post(f"/api/jobs/{jid}/proposals/{p['id']}/accept", json={})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]


def test_merged_ids_are_readable_unique_and_never_confused_with_another_block():
    from ocr import ops
    assert ops._merged_id(["p1_B1", "p1_B2"], 1, ["p1_B1", "p1_B2"]) == "p1_B1_B2"
    assert ops._merged_id(["p1_B1_B2"], 1, ["p1_B1", "p1_B2"]) == "p1_B1_B2_2"                                  # a second block with the same pieces
    assert ops._merged_id(["p1_B12"], 1, ["p1_B1", "p1_B2"]) == "p1_B1_B2"                                        # B1+B2 is not B12
    assert ops._merged_id([], 3, [f"p3_B{i}" for i in range(1, 11)]) == "p3_B1_B2_B3_plus7"                      # long names are shortened
    assert ops._merged_id([], 2, ["p2_n1", "p2_u1"]) == "p2_n1_u1"
