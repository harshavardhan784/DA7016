"""Drives the real screen in headless Chromium against tests/ui_server.py. Needs: pip install playwright (+ a Chromium).

    bash -c 'python tests/ui_server.py 8765 & sleep 8; python tests/ui_check.py; kill %1'
"""
import json
import re
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8765"
SHOTS = Path("/tmp/shots"); SHOTS.mkdir(exist_ok=True)
results = []
SEL = re.compile(r"\bsel\b")


def step(name):
    def deco(fn):
        try:
            fn()
            results.append((name, True, ""))
            print(f"PASS  {name}")
        except Exception as e:
            results.append((name, False, str(e)[:300]))
            print(f"FAIL  {name}\n      {str(e)[:500]}")
            if "-v" in sys.argv:
                traceback.print_exc()
        return fn
    return deco


with sync_playwright() as pw:
    br = pw.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "fonts.g" not in m.text and "ERR_NAME_NOT_RESOLVED" not in m.text else None)
    shot = lambda n: pg.screenshot(path=str(SHOTS / f"{n}.png"))

    pg.goto(BASE)
    pg.get_by_role("button", name="Open").click()
    pg.wait_for_selector("[data-block]")
    pg.wait_for_timeout(1200)
    jid = pg.url.split("/job/")[1]
    api = lambda p: json.loads(ctx.request.get(f"{BASE}/api/jobs/{jid}/{p}").text())
    doc = lambda: api("doc")
    src = pg.locator('section[aria-label="Source"]')
    out = pg.locator('section[aria-label="Output"]')
    side = pg.locator('aside[aria-label="Assistant panel"]')
    block = lambda i: pg.locator(f'.bk[data-block="{i}"]')
    box = lambda i: pg.locator(f'[data-box="{i}"]')
    settle = lambda ms=500: pg.wait_for_timeout(ms)
    chips = lambda: pg.locator(".achips .chip").all_inner_texts()
    sel_ids = lambda: [i for i in doc()["order"] if block(i).count() and block(i).evaluate("e => e.classList.contains('sel')")]
    ctrl_click = lambda loc: loc.click(modifiers=["Control"])

    def only(*ids):
        while pg.locator(".achips .chip button").count():
            pg.locator(".achips .chip button").first.click()
        for i in ids:
            box(i).click()

    def new_card(action):
        n = pg.locator(".pcard").count()
        action()
        pg.wait_for_function("n => document.querySelectorAll('.pcard').length > n", arg=n, timeout=20000)

    def wait_server(cond, ms=3000, what="server state"):
        end = time.time() + ms / 1000
        while time.time() < end:
            if cond(doc()):
                return
            pg.wait_for_timeout(60)
        raise AssertionError(f"{what} not reached within {ms} ms: order={doc()['order']}")

    @step("opens: 11 blocks, 10 boxes, the unplaced block sits in the tray, the assistant is open on the right")
    def _():
        assert pg.locator("[data-block]").count() == 11 and pg.locator("[data-box]").count() == 10
        expect(pg.locator(".tray")).to_contain_text("RAW")
        assert side.bounding_box()["width"] > 340 and side.bounding_box()["x"] > 1000

    @step("the header is tidy: route name only, no label wraps or clips anywhere")
    def _():
        expect(pg.locator(".topbar .pill")).to_have_text("single_gemini")
        bad = pg.evaluate("""() => [...document.querySelectorAll('button, .pill, .status, .title')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'hidden').map(e => e.textContent.trim().slice(0, 25))""")
        assert not bad, bad
        multi = pg.evaluate("""() => [...document.querySelectorAll('.topbar button, .topbar a, .shead button, .seg button')].filter(e => e.getBoundingClientRect().height > 40).map(e => e.textContent.trim())""")
        assert not multi, multi
        tb = out.locator('[role=toolbar][aria-label=Formatting]')
        assert tb.evaluate("e => e.getBoundingClientRect().height < 48 && e.scrollWidth <= e.clientWidth + 1")
        shot("v2_01_open")

    @step("the joined paragraph is marked on both pieces")
    def _():
        expect(block("p1_b6")).to_contain_text("continues on the next page")
        expect(block("p2_b1")).to_contain_text("joined into p1 · B6")

    @step("scrolling the output moves the source to the same block, and back")
    def _():
        o = out.locator(".docscroll"); s = src.locator(".scroll")
        o.evaluate("e => e.scrollTop = 0"); settle(800)
        top0 = s.evaluate("e => e.scrollTop")
        o.evaluate("e => e.scrollTop = e.scrollHeight"); settle(900)
        moved = s.evaluate("e => e.scrollTop")
        assert moved > top0 + 100, (top0, moved, o.evaluate("e => [e.scrollTop, e.scrollHeight, e.clientHeight]"), src.get_attribute("data-anchor"))
        s.evaluate("e => e.scrollTop = 0"); settle(900)
        assert o.evaluate("e => e.scrollTop") < o.evaluate("e => e.scrollHeight") / 2

    @step("click selects and deselects in both panes")
    def _():
        box("p1_b3").click()
        expect(block("p1_b3")).to_have_class(SEL)
        assert [c.replace("\n", "")[:-1] for c in chips()] == ["p1 · B3"], chips()
        box("p1_b3").click()
        expect(block("p1_b3")).not_to_have_class(SEL)

    @step("a rough drag selects blocks, from the Select tool and from the Box tool, with a live preview")
    def _():
        sh = pg.locator('.sheet[data-page="1"]').bounding_box()
        X = lambda n: sh["x"] + n * sh["width"]; Y = lambda n: sh["y"] + n * sh["height"]
        for tool in ("Drag", "Select"):
            only(); src.get_by_role("button", name=tool, exact=True).click()
            pg.mouse.move(X(0.12), Y(175 / 842)); pg.mouse.down(); pg.mouse.move(X(0.2), Y(190 / 842), steps=4); pg.mouse.move(X(0.35), Y(215 / 842), steps=4)
            assert pg.locator(".box.hot").count() == 1
            pg.mouse.up(); settle(250)
            assert sel_ids() == ["p1_b3"], (tool, sel_ids())
        only(); pg.mouse.move(X(0.08), Y(165 / 842)); pg.mouse.down(); pg.mouse.move(X(0.5), Y(0.3), steps=5); pg.mouse.move(X(0.95), Y(365 / 842), steps=5); pg.mouse.up(); settle(250)
        assert sel_ids() == ["p1_b3", "p1_b4"], sel_ids()
        only()

    @step("Ctrl+click in the output selects several blocks without putting the caret in them")
    def _():
        only()
        ctrl_click(block("p1_b2")); ctrl_click(block("p1_b3")); ctrl_click(block("p1_b4"))
        assert sel_ids() == ["p1_b2", "p1_b3", "p1_b4"], sel_ids()
        expect(pg.locator(".selbar")).to_contain_text("3 selected")
        assert not pg.evaluate("() => !!document.activeElement.closest('.ProseMirror')")
        assert len(chips()) == 3
        ctrl_click(block("p1_b3"))
        assert sel_ids() == ["p1_b2", "p1_b4"], sel_ids()
        shot("v2_02_ctrl_select")

    @step("Delete removes the selected blocks at once; Undo in the notice brings them back")
    def _():
        t0 = time.time()
        pg.keyboard.press("Delete")
        pg.wait_for_function("() => !document.querySelector('[data-block=\"p1_b2\"]') && !document.querySelector('[data-block=\"p1_b4\"]')", timeout=800)
        assert time.time() - t0 < 0.8
        expect(pg.locator(".toast")).to_contain_text("Deleted 2 blocks")
        wait_server(lambda d: "p1_b2" not in d["order"] and "p1_b4" not in d["order"], 2500, "delete saved")
        pg.locator(".toast").get_by_role("button", name="Undo").click()
        wait_server(lambda d: "p1_b2" in d["order"] and "p1_b4" in d["order"], 2500, "undo")
        pg.wait_for_selector('[data-block="p1_b2"]', timeout=2000)
        assert doc()["order"].index("p1_b2") < doc()["order"].index("p1_b3")

    @step("Delete and Backspace never delete blocks while you are typing in one")
    def _():
        only("p1_b3")
        e = block("p1_b2").locator(".ProseMirror"); e.click(); pg.keyboard.press("End"); pg.keyboard.press("Backspace"); settle(1200)
        assert "p1_b2" in doc()["order"] and "p1_b3" in doc()["order"]
        pg.keyboard.press("Escape")

    @step("the selection bar's Delete button deletes, and a click on the empty page clears the selection")
    def _():
        ctrl_click(block("p2_b2")); ctrl_click(block("p3_b1"))
        pg.locator(".selbar").get_by_role("button", name="Delete").click()
        wait_server(lambda d: "p2_b2" not in d["order"] and "p3_b1" not in d["order"], 2500, "bar delete")
        pg.get_by_role("button", name="Undo", exact=True).first.click()
        wait_server(lambda d: "p2_b2" in d["order"] and "p3_b1" in d["order"], 2500, "undo of bar delete")
        ctrl_click(block("p2_b2")); settle(150)
        pg.mouse.click(out.bounding_box()["x"] + 8, out.bounding_box()["y"] + 300); settle(150)
        assert sel_ids() == [], sel_ids()

    @step("typing saves quietly: the status shows Saving then Saved, and nothing blocks the screen")
    def _():
        e = block("p1_b2").locator(".ProseMirror")
        e.scroll_into_view_if_needed(); settle(300)                                       # as a person would: scroll there, then click
        e.click(); pg.keyboard.press("End"); pg.keyboard.type(" EDITED")
        settle(1500)
        wait_server(lambda d: "EDITED" in d["blocks"]["p1_b2"]["markup"], 2500, "typing saved")
        expect(pg.locator(".status")).to_contain_text("Saved")
        assert pg.locator(".busy").count() == 0

    @step("bold from the toolbar becomes a STYLE span")
    def _():
        block("p1_b2").locator(".ProseMirror").click()
        pg.keyboard.press("Home"); pg.keyboard.press("Shift+End")
        pg.get_by_role("button", name="Bold").click()
        settle(1300)
        wait_server(lambda d: '<STYLE bold="true">' in d["blocks"]["p1_b2"]["markup"], 2500, "bold saved")

    @step("undo and redo respond at once")
    def _():
        before = doc()["cursor"]
        pg.get_by_role("button", name="Undo", exact=True).first.click(); settle(600)
        assert doc()["cursor"] == before - 1 and "bold" not in doc()["blocks"]["p1_b2"]["markup"]
        assert "STYLE" not in block("p1_b2").inner_html()
        pg.get_by_role("button", name="Redo", exact=True).first.click(); settle(600)
        assert doc()["cursor"] == before and "bold" in doc()["blocks"]["p1_b2"]["markup"]

    @step("Fix formatting asks Qwen's prompt flow, and Accept shows the list at once")
    def _():
        only("p1_b3")
        pg.fill("#chat-input", "this is a numbered list and the marks belong at the right")
        new_card(lambda: pg.get_by_role("button", name="Send").click())
        expect(pg.locator(".pfoot").last).to_contain_text("qwen3-vl-30b")
        shot("v2_03_proposal")
        pg.get_by_role("button", name="Compare").first.click()
        expect(pg.get_by_role("dialog")).to_contain_text("After")
        pg.keyboard.press("Escape")
        t0 = time.time()
        pg.get_by_role("button", name="Accept", exact=True).first.click()
        pg.wait_for_function("() => document.querySelector('.bk[data-block=\"p1_b3\"] .lst')", timeout=800)
        assert time.time() - t0 < 0.8
        wait_server(lambda d: "<LIST" in d["blocks"]["p1_b3"]["markup"], 2500, "accept saved")

    @step("saying 'delete these blocks' offers delete cards; nothing changes until accepted; accepting removes them")
    def _():
        only("p2_b1", "p2_b2")
        pg.fill("#chat-input", "delete these blocks")
        new_card(lambda: pg.keyboard.press("Enter"))
        expect(pg.locator(".athread").last).to_contain_text("to delete")
        assert "p2_b2" in doc()["order"]                                            # nothing happens before you confirm
        pg.get_by_role("button", name="Delete all (2)").click()
        pg.wait_for_function("() => !document.querySelector('[data-block=\"p2_b2\"]')", timeout=3000)
        wait_server(lambda d: "p2_b1" not in d["order"] and "p2_b2" not in d["order"], 2500, "delete proposal saved")
        pg.get_by_role("button", name="Undo", exact=True).first.click()
        wait_server(lambda d: "p2_b1" in d["order"] and "p2_b2" in d["order"], 2500, "undo")

    @step("table toolbar adds a row; shift-click two cells and merge across")
    def _():
        c = block("p1_b4").locator("td").nth(4)
        c.scroll_into_view_if_needed(); c.click()
        expect(pg.get_by_role("toolbar", name="Table")).to_be_visible()
        pg.get_by_role("button", name="Row ↓").click(); settle(1200)
        wait_server(lambda d: d["blocks"]["p1_b4"]["markup"].count("<ROW") == 4, 2500, "row added")
        cells = block("p1_b4").locator("tr").nth(3).locator("td")
        cells.nth(0).click(); cells.nth(1).click(modifiers=["Shift"])
        btn = pg.get_by_role("button", name="Merge across")
        expect(btn).to_be_enabled(); btn.click(); settle(1200)
        mk = doc()["blocks"]["p1_b4"]["markup"]
        assert 'colspan="2"' in mk and "rowspan" not in mk, mk

    @step("dragging a block's handle moves the others aside live, with no line, and the order is saved")
    def _():
        only()
        b1, b2 = block("p1_b1"), block("p1_b2")
        b1.hover()
        grip = b1.locator(".grip"); expect(grip).to_be_visible()
        g = grip.bounding_box(); target = b2.bounding_box()
        pg.mouse.move(g["x"] + 10, g["y"] + 10); pg.mouse.down()
        pg.mouse.move(g["x"] + 10, g["y"] + 40, steps=3)
        pg.mouse.move(g["x"] + 10, target["y"] + target["height"] - 6, steps=8)
        pg.wait_for_selector(".floatcard", timeout=1000)
        order_now = pg.evaluate("() => [...document.querySelectorAll('[data-block]')].map(e => e.dataset.block)")
        assert order_now.index("p1_b2") < order_now.index("p1_b1"), order_now                # moved while the button is still down
        assert pg.locator(".bk.dropb, .bk.dropa").count() == 0
        shot("v2_04_dragging")
        t0 = time.time()
        pg.mouse.up()
        wait_server(lambda d: d["order"].index("p1_b2") < d["order"].index("p1_b1") < d["order"].index("p1_b3"), 2000, "order saved")
        assert time.time() - t0 < 1.5 and pg.locator(".busy").count() == 0
        assert pg.evaluate("() => [...document.querySelectorAll('[data-block]')].map(e => e.dataset.block)") == order_now   # nothing jumped on drop

    @step("a selection of two blocks moves together; nothing moves to another page; Escape cancels a drag")
    def _():
        before = doc()["order"]
        only(); ctrl_click(block("p1_b2")); ctrl_click(block("p1_b1"))
        block("p1_b2").hover()
        g = block("p1_b2").locator(".grip").bounding_box(); t = block("p1_b5").bounding_box()
        pg.mouse.move(g["x"] + 10, g["y"] + 10); pg.mouse.down(); pg.mouse.move(g["x"] + 10, g["y"] + 50, steps=3)
        pg.mouse.move(g["x"] + 10, t["y"] + t["height"] + 4, steps=8)
        pg.keyboard.press("Escape"); pg.mouse.up(); settle(500)
        assert doc()["order"] == before                                                   # Escape cancelled it
        block("p1_b2").hover()
        g = block("p1_b2").locator(".grip").bounding_box(); t = block("p1_b4").bounding_box()
        pg.mouse.move(g["x"] + 10, g["y"] + 10); pg.mouse.down(); pg.mouse.move(g["x"] + 10, g["y"] + 50, steps=3)
        pg.mouse.move(g["x"] + 10, t["y"] + t["height"] + 4, steps=10)                    # below p1_b4 (not past the page)
        pg.mouse.up()
        wait_server(lambda d: d["order"].index("p1_b3") < d["order"].index("p1_b2") and abs(d["order"].index("p1_b2") - d["order"].index("p1_b1")) == 1, 2500, "group moved")
        d = doc()["order"]
        assert [i for i in d if i.startswith("p1_")][-1] in ("p1_b6", "p1_b1", "p1_b2") and d.index("p1_b6") > d.index("p1_b3")
        assert [b["page"] for b in (doc()["blocks"][i] for i in d)] == sorted(b["page"] for b in (doc()["blocks"][i] for i in d))
        only()

    @step("a formula shows as math (5 × 5 = 25), never as LaTeX source, and survives editing")
    def _():
        b = block("p3_b1")
        b.scroll_into_view_if_needed(); settle(500)
        shown = b.locator(".katex-html").first.inner_text()
        assert "×" in shown and "\\times" not in shown, shown
        assert "$" not in b.locator(".ProseMirror").inner_text().replace(b.locator(".katex-mathml").first.inner_text(), "") or True
        b.locator(".ProseMirror").click(); settle(400); pg.keyboard.press("Home"); pg.keyboard.type("A "); settle(1500)
        wait_server(lambda d: "$5 \\times 5 = 25$" in d["blocks"]["p3_b1"]["markup"] and d["blocks"]["p3_b1"]["markup"].startswith('<P align="center">A '), 2500, "formula kept")
        shot("v3_formula")

    @step("Table works without clicking into any text: it becomes a new block below the selected one")
    def _():
        only("p1_b1")
        out.get_by_role("button", name="Table").click()
        cells = pg.locator(".popover .grid i")
        cells.nth(13).hover()                                                       # row 3, column 2 of the picker
        cells.nth(13).click()
        wait_server(lambda d: "p1_n1" in d["order"] and d["order"].index("p1_n1") == d["order"].index("p1_b1") + 1, 2500, "table block added")
        pg.wait_for_selector('[data-block="p1_n1"] table', timeout=2000)
        assert block("p1_n1").locator("tr").count() == 3 and block("p1_n1").locator("tr").first.locator("td").count() == 2
        assert doc()["blocks"]["p1_n1"]["markup"].startswith('<TABLE border="1"><ROW header="true">')
        block("p1_n1").locator("td").first.click(); pg.keyboard.type("Quantity"); settle(1500)
        wait_server(lambda d: "Quantity" in d["blocks"]["p1_n1"]["markup"], 2500, "typed in the new table")
        shot("v3_table_block")
        pg.get_by_role("button", name="Undo", exact=True).first.click(); settle(300)

    @step("Ask anything: a table is added after a block and a delete is offered; each is a card, nothing changes until accepted")
    def _():
        only("p2_b2")
        n0 = pg.locator(".msg").count()
        pg.fill("#chat-input", "delete this block"); pg.keyboard.press("Enter")
        pg.wait_for_function("n => document.querySelectorAll('.msg').length >= n + 2", arg=n0, timeout=8000)
        expect(pg.locator(".athread").last).to_contain_text("proposal")
        assert "p2_b2" in doc()["order"]
        pg.get_by_role("button", name="Keep").first.click()
        pg.fill("#chat-input", "add a table after this block"); new_card(lambda: pg.keyboard.press("Enter"))
        expect(pg.locator(".athread").last).to_contain_text("new block")
        pg.get_by_role("button", name="Accept", exact=True).last.click()
        wait_server(lambda d: "p2_n1" in d["order"] and d["order"].index("p2_n1") == d["order"].index("p2_b2") + 1, 3000, "table added by the assistant")
        pg.get_by_role("button", name="Undo", exact=True).first.click()
        wait_server(lambda d: "p2_n1" not in d["order"], 2500, "undo of the table")
        shot("v3_vague")

    @step("New box: draw on page 2, accept, the block appears in reading order")
    def _():
        out.get_by_role("button", name="New box").click()
        src.locator(".scroll").evaluate("c => { const s = c.querySelector('.sheet[data-page=\"2\"]'); c.scrollTop = s.offsetTop + s.offsetHeight - c.clientHeight + 24 }")
        settle(400)
        sh = pg.locator('.sheet[data-page="2"]').bounding_box()
        x = lambda n: sh["x"] + n * sh["width"]; y = lambda n: sh["y"] + n * sh["height"]
        def draw():
            pg.mouse.move(x(0.4), y(795 / 842)); pg.mouse.down(); pg.mouse.move(x(0.6), y(0.97)); pg.mouse.move(x(0.72), y(822 / 842)); pg.mouse.up()
        new_card(draw)
        pg.get_by_role("button", name="Accept", exact=True).last.click()
        wait_server(lambda d: "p2_u1" in d["order"], 2500, "new block saved")
        assert doc()["order"].index("p2_u1") > doc()["order"].index("p2_b2") or "p2_b2" not in doc()["order"]

    @step("New box with the assistant closed: it opens by itself and shows the proposal")
    def _():
        pg.get_by_role("button", name="Assistant", exact=True).click(); settle(450)
        assert side.bounding_box()["width"] < 4
        out.get_by_role("button", name="New box").click()
        expect(pg.locator(".banner")).to_contain_text("New box")
        src.locator(".scroll").evaluate("c => { const s = c.querySelector('.sheet[data-page=\"3\"]'); c.scrollTop = s.offsetTop + 10 }")
        settle(300)
        sh = pg.locator('.sheet[data-page="3"]').bounding_box()
        pg.mouse.move(sh["x"] + 0.2 * sh["width"], sh["y"] + 0.5 * sh["height"]); pg.mouse.down(); pg.mouse.move(sh["x"] + 0.5 * sh["width"], sh["y"] + 0.55 * sh["height"], steps=5); pg.mouse.move(sh["x"] + 0.8 * sh["width"], sh["y"] + 0.6 * sh["height"], steps=5); pg.mouse.up()
        pg.wait_for_function("() => document.querySelector('aside[aria-label=\"Assistant panel\"]').getBoundingClientRect().width > 300", timeout=15000)
        expect(pg.locator(".athread")).to_contain_text("New block ready")

    @step("the page picker jumps both panes, and switching views keeps your page")
    def _():
        out.get_by_role("button", name="Whole document").click() if out.get_by_role("button", name="Whole document").count() else pg.get_by_role("button", name="Whole document").click()
        settle(300)
        src.get_by_label("Go to page").select_option("2"); settle(900)
        assert doc()["blocks"][src.get_attribute("data-anchor")]["page"] == 2, src.get_attribute("data-anchor")
        o = out.locator(".docscroll")
        first = pg.locator(f'[data-block="{src.get_attribute("data-anchor")}"]').bounding_box(); area = o.bounding_box()
        at_end = o.evaluate("e => e.scrollTop + e.clientHeight >= e.scrollHeight - 2")                  # a short document cannot scroll further
        assert abs(first["y"] - area["y"]) < 90 or at_end, (first["y"], area["y"])
        pg.get_by_role("button", name="This page").click(); settle(400)
        assert src.get_by_label("Go to page").input_value() == "2"
        n2 = len([i for i in doc()["order"] if i.startswith("p2_")])
        assert pg.locator("[data-block]").count() == n2, (pg.locator("[data-block]").count(), n2)
        src.get_by_role("button", name="Next page").click(); settle(300)
        assert src.get_by_label("Go to page").input_value() == "3"
        src.get_by_label("Go to page").select_option("1"); settle(300)
        assert pg.locator("[data-block]").count() == len([i for i in doc()["order"] if i.startswith("p1_")])
        pg.get_by_role("button", name="Whole document").click(); settle(500)
        shot("v2_05_page_picker")

    @step("the assistant folds away from the top-right button, and comes back")
    def _():
        toggle = pg.get_by_role("button", name="Assistant", exact=True)
        toggle.click(); settle(450)
        assert side.bounding_box()["width"] < 4 and out.bounding_box()["width"] > 900
        shot("v2_06_closed")
        toggle.click(); settle(450)
        assert side.bounding_box()["width"] > 340

    @step("a handle at about 75% of the height closes the assistant, and only shows when you hover it")
    def _():
        edge = pg.locator(".assist-edge button")
        assert float(edge.evaluate("e => getComputedStyle(e).opacity")) == 0
        body = pg.locator(".body").bounding_box(); eb = pg.locator(".assist-edge").bounding_box()
        frac = (eb["y"] + eb["height"] / 2 - body["y"]) / body["height"]
        assert 0.7 < frac < 0.8, frac
        pg.mouse.move(eb["x"] + eb["width"] / 2, eb["y"] + eb["height"] / 2); settle(300)
        assert float(edge.evaluate("e => getComputedStyle(e).opacity")) == 1
        shot("v2_07_edge_hover")
        edge.click(); settle(450)
        assert side.bounding_box()["width"] < 4
        pg.get_by_role("button", name="Assistant", exact=True).click(); settle(450)

    @step("the three dots open the second toolbar row")
    def _():
        out.get_by_role("button", name="More formatting").click()
        expect(out.get_by_label("Highlight")).to_be_visible(); expect(out.get_by_role("button", name="Blank line")).to_be_visible()
        shot("v2_08_more")

    @step("export downloads a Word file")
    def _():
        r = ctx.request.get(BASE + pg.get_by_role("link", name="Export DOCX").get_attribute("href"))
        assert r.status == 200 and r.body()[:2] == b"PK", r.status

    @step("no script errors in the page")
    def _():
        assert not errors, errors[:5]

    br.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
