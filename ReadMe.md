# Infinite OCR

Turns a PDF (digital or scanned; English, Hindi and other Indic scripts) into an editable Word file. A notebook holds the pipeline; a web app runs it, lets a person check each block against the page, fix it with an assistant, and export the `.docx`.

- **How it works, stage by stage:** [methodology.md](methodology.md)
- **Server updates, Gmail access, prompt files, fixes:** [DEPLOY.md](DEPLOY.md)

**How to read the commands.** Each command lists the folder to run it in, whether it needs an Administrator PowerShell, what it does, and what you should see.
✅ = run during writing this guide (Linux, Python 3.12, Node 22). ⚠️ = not run: it needs Windows Server, real API keys, Google sign-in, Caddy or a browser, so treat it as the documented procedure, not a tested one.

## Repository layout

```text
pipeline/infinite_ocr.ipynb   the notebook: the only copy of the pipeline logic. Replace it to update the pipeline.
ocr/nb.py                     finds the notebook's cells by what they define and runs them (fails loudly if it changed)
ocr/ops.py                    pipeline run, rebuild, review, new box, assistant: each in its own process
ocr/store.py                  one folder per job, edit history with undo / redo
ocr/api.py, ocr/auth.py       web server and Google sign-in
ocr/config.py                 settings (environment variables)
ocr/prompts.py, ocr/intent.py the assistant's wording (editable files) and the API shortcuts
ocr/smoke.py                  one real-model check on the server
go_public.ps1, start.bat, Caddyfile   Windows setup: HTTPS, Google sign-in, start at boot (see DEPLOY.md)
web/dist/                     the built screen (what the server serves; no Node needed on the server)
web_src/                      the screen's source: React + TypeScript + TipTap
tests/                        python -m pytest tests -q   (the Word-fidelity test needs `npm test` in web_src first)
```

The app changes the notebook in only two places at run time: the `PDF_PATH` and `PAGES` lines, plus a snapshot of `ordered_blocks` saved to `blocks_pre.json` just before the final cell (`ocr/nb.py`).

---

# Part 1: Administrator Guide

## 1.1 Prerequisites

| Need | Why |
|---|---|
| Windows Server (the project runs on AWS EC2 with an Elastic IP) | hosts the app; scripts are PowerShell and `.bat` ⚠️ |
| Python 3.11 with `python` on `PATH` | the server runs 3.11; tests here ran on 3.12 ✅ |
| `pip install -r requirements.txt` | web server, PDF, image and Word libraries. `docling` (large) is only used by the digital-English route |
| Node.js 22 + npm | **only** to rebuild `web/dist` or run the screen's tests; not needed on the server ✅ |
| A domain name pointing at the server (DuckDNS works) | HTTPS |
| Caddy | HTTPS proxy; `go_public.ps1` installs it with `winget` if missing ⚠️ |
| AWS CLI | only if you move the zip through S3 as DEPLOY.md does |
| OpenRouter API key, Bodhan API key | the models (never stored in the repository) |
| Google OAuth "Web application" Client ID, with `https://<your-domain>` as an Authorized JavaScript origin | sign-in |
| AWS security group: HTTP 80 and HTTPS 443 from anywhere; RDP 3389 from your IP only | access |

## 1.2 Install on a new server

Run everything in an **Administrator PowerShell**. Steps follow DEPLOY.md section C. ⚠️ (not run on Windows)

**1. Put the app in `C:\app`** · Folder: any · Admin: yes
Copies the program from the zip in your bucket (replace `<your-bucket>` and `<your-region>`).
```powershell
mkdir C:\app
aws s3 cp s3://<your-bucket>/infinite_ocr_app.zip C:\app\app.zip --region <your-region>
Expand-Archive C:\app\app.zip C:\app -Force
Copy-Item C:\app\ocr_server\* C:\app\ -Recurse -Force
Remove-Item C:\app\ocr_server -Recurse -Force
Remove-Item C:\app\app.zip
```
Expect: `C:\app` contains `ocr`, `pipeline`, `web`, `go_public.ps1`, `requirements.txt`.

**2. Install the Python packages** · Folder: `C:\app` · Admin: yes
```powershell
cd C:\app
python -m pip install -r requirements.txt
```
Expect: pip ends with "Successfully installed …" (docling pulls in a large set of packages; this can take several minutes).

**3. Store the API keys on the machine** · Folder: any · Admin: yes (`/M` writes machine-wide)
```powershell
setx OPENROUTER_API_KEY "<your-openrouter-key>" /M
setx BODHAN_OCR_API_KEY "<your-bodhan-key>" /M
```
Expect: `SUCCESS: Specified value was saved.` Open a **new** PowerShell afterwards so it sees them.

**4. Run the setup script** · Folder: any · Admin: yes
```powershell
powershell -ExecutionPolicy Bypass -File C:\app\go_public.ps1
```
It asks four questions: the domain, the Google Client ID, the emails allowed to sign in, and (optional) a whole mail domain such as `gmail.com`. It then checks that the domain points at this server, saves the settings as machine environment variables, writes `C:\app\start.bat` (with the real Python path) and `C:\app\Caddyfile`, opens ports 80 and 443 in Windows Firewall, registers two start-at-boot tasks (`OCR-App`, `OCR-Caddy`, running as SYSTEM) and starts them.
Expect: "The app is running on this machine." and "HTTPS works: https://<your-domain>". Running the script again changes the answers.

**5. Prove it starts by itself** · Admin: yes — restart the server once, wait 3 to 5 minutes, open `https://<your-domain>`.

**6. Publish the Google app** (needed for accounts other than your test users): Google Cloud console → Google Auth Platform → Audience → **Publish app**.

## 1.3 Configuration reference

Set as machine environment variables (`setx NAME "value" /M`, new processes only) or as `set NAME=value` lines above the `python` line in `C:\app\start.bat` (that file is rewritten by `go_public.ps1`). Secrets are read only from the environment; none are in the repository.

| Variable | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY`, `BODHAN_OCR_API_KEY` | none | model access; without them review/assistant and the pipeline fail with a clear message |
| `GOOGLE_CLIENT_ID` | none | enables Google sign-in |
| `ALLOWED_EMAILS` | none | comma list; `*` allows anyone |
| `ALLOWED_DOMAIN` | none | e.g. `gmail.com` lets in every address at that domain |
| `SESSION_SECRET` | generated into `data\secret.key` | signs the login cookie |
| `SESSION_HOURS` | 168 | login lifetime |
| `AUTH_DISABLED` | off | `1` turns sign-in off: local testing only |
| `MAX_UPLOAD_MB` / `MAX_PAGES` | 50 / 20 | one PDF's limits (Caddy also caps the body at 60 MB) |
| `USER_DAILY_PAGES` | 100 | pages one person may upload per 24 h; `0` = no limit |
| `JOB_TIMEOUT` / `OP_TIMEOUT` | 3600 / 600 s | pipeline run / rebuild-review-assistant run |
| `OCR_DATA` | `<app>\data` | where documents, logs, prompts and the secret key live |

Models, routing thresholds and other pipeline knobs are in the notebook's config and route cells (see methodology.md §7).

## 1.4 Run and monitor

| Task | Command (Administrator PowerShell, any folder) | Expect |
|---|---|---|
| Health check ⚠️ | `curl.exe http://127.0.0.1:8000/api/config` | one line of JSON such as `{"auth":true,"google_client_id":"…","max_mb":50,"max_pages":20}` (✅ on Linux with auth off) |
| Task state | `Get-ScheduledTask -TaskName OCR-App, OCR-Caddy` | both listed, `Ready` or `Running` |
| Stop the app | `Stop-ScheduledTask -TaskName OCR-App -ErrorAction SilentlyContinue; Get-Process python -ErrorAction SilentlyContinue \| Stop-Process -Force` | no python process |
| Start the app | `Start-ScheduledTask -TaskName OCR-App` | health check answers within seconds |
| App log | `Get-Content C:\app\data\app.log -Tail 40` | uvicorn lines |
| One document's log | `Get-Content C:\app\data\jobs\<job folder>\run.log -Tail 60` | the pipeline output; `ops.log` beside it is for rebuild/review/assistant |

All commands in this table are ⚠️ not run, except the health-check output format (✅ on Linux).

Data: `C:\app\data\jobs\<id>\` holds each document (layout in methodology.md §2). **Nothing deletes old job folders**; remove them yourself when disk space matters. Back up `C:\app\data` to keep documents and `secret.key`.

## 1.5 Update the app

Folder: any · Admin: yes ⚠️. Keeps `data`, `start.bat` and `Caddyfile`. Run one line at a time:
```powershell
Stop-ScheduledTask -TaskName OCR-App -ErrorAction SilentlyContinue
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
aws s3 cp s3://<your-bucket>/infinite_ocr_app.zip C:\app_update.zip --region <your-region>
Remove-Item C:\app_new -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive C:\app_update.zip C:\app_new -Force
robocopy C:\app_new\ocr_server C:\app /MIR /XD data /XF start.bat Caddyfile
Remove-Item C:\app_new -Recurse -Force
Remove-Item C:\app_update.zip
cd C:\app
python -m pip install -r requirements.txt
Start-ScheduledTask -TaskName OCR-App
```
`robocopy` exit codes 0 to 7 are success. Anything else you put by hand in `C:\app` is removed. If the assistant's wording changed in the update, also delete the old prompt files once (they override the built-in wording): `Remove-Item C:\app\data\prompts\*.txt`.

## 1.6 Let in more people

Folder: any · Admin: yes ⚠️. DEPLOY.md section B has the exact commands (it adds `set ALLOWED_DOMAIN=gmail.com` to `start.bat` and restarts the app). Remember step 1.2-6 (Publish app). Everyone admitted uses your model credit, limited by `USER_DAILY_PAGES`.

## 1.7 Edit the assistant's wording

Plain files in `C:\app\data\prompts\` (created on first use). Edit in Notepad; the next request uses them, no restart. `assist.txt` is the assistant; keep `{note}`, `{tags}` and the `<REPLY>` / `<ACTION type="edit|merge|split|insert|delete">` answer format. A file missing a placeholder is ignored with a warning in `ops.log`. Delete a file to return to the built-in text.

## 1.8 Build and test (developers)

Run from the repository root unless noted. Admin: no.

```bash
cd web_src
npm ci                 # installs the exact versions in package-lock.json
npm test               # 54 tests; also writes tests/roundtrip.out.json (git-ignored)
npm run build          # type-checks, then writes ../web/dist (commit it: the server serves it)
cd ..
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```
✅ Result here: `npm test` 54 passed (also on a clean checkout), build OK, `pytest` 103 passed. `requirements-dev.txt` includes `docling`; the tests passed here without it because only Route 1 imports it.
Browser walk-through (`tests/ui_server.py`, `tests/ui_check.py`, needs `pip install playwright` and Chromium) ⚠️ not run.
One real-model check on the server (costs a few cents) ⚠️: `python -m ocr.smoke C:\path\to\small.pdf` from `C:\app`. Without keys or network it stops with `RuntimeError: pipeline failed - see run.log` ✅; with no file argument it fails with `IndexError`.

## 1.9 Administrator troubleshooting

| Symptom | Check / fix |
|---|---|
| Other Gmail accounts cannot sign in | `ALLOWED_DOMAIN`/`ALLOWED_EMAILS` set (section 1.6) **and** the Google app is Published |
| Google says `origin_mismatch` or no sign-in button | the origin in Google must be exactly `https://<your-domain>` |
| "The server has no OPENROUTER_API_KEY" | `setx … /M` (step 3), then restart the app task; if still missing, restart the server ⚠️ |
| Job shows "pipeline failed - see run.log" | read the end of `run.log`. If it stops with **no error text**, the Python process crashed natively: check Windows Event Viewer → Application for `python.exe` / `APPCRASH` (once seen inside `docling_parse`); uploading the same file again worked |
| `HTTPS cannot be issued` | the domain must point at this server and ports 80/443 be open in the security group |
| Test the screen without Google | the safe procedure is in DEPLOY.md ("If something is wrong"): stop Caddy first |

## 1.10 Security notes

- No secrets are in the repository. Keys live in machine environment variables; `data\secret.key` is generated at runtime. `.gitignore` excludes `data/`, `secret.key`, `.env*`, `*.key`, `*.pem`.
- `data\` contains every uploaded document: protect and back it up accordingly.
- Never run with `AUTH_DISABLED=1` while Caddy is up: anyone could open the app.
- RDP (3389) should be open to your own IP only.

---

# Part 2: Regular User Guide

## 2.1 Using the web app (normal way)

You need the web address and a Google account that the administrator has allowed. ⚠️ (browser, Google and server not available here)

1. **Open the address and sign in** with Google. Expect: the "Infinite OCR" page with your email at the top.
2. **Add a PDF**: drop it on the box or press **Choose a PDF**. Limits: 20 pages, 50 MB, and 100 pages per 24 hours per person. Expect: a new card with "Waiting in the queue", then "Reading the pages", "Building the document", **Ready**. One document is processed at a time, so you may see "(n ahead)".
3. **Press Open.** The left side is the original page with a box per block; the right side is the Word-style result; the **Assistant** panel is on the far right. The pill at the top says how the PDF was read.
4. **Check and correct.**
   - Click a block to select it (Ctrl+click adds more; the **Drag** tool selects with a rectangle). Click inside a block to type; formatting buttons are above the page (alignment and table menus open under their buttons).
   - **New box** reads text the OCR missed: draw a box on the page and accept the result.
   - **Assistant**: select blocks, then type anything in plain words: "merge these two, they are side by side", "split them again", "add a table after this block", "check this against the image, it is wrong, correct it", "the brackets are missing", "delete these". It proposes cards. **Nothing changes until you press Accept** (Compare shows the page crop, before and after; a warning note appears when words would change). It remembers your earlier requests in this document, so you can say "now centre the merged block".
   - **Undo / Redo** (toolbar) reverse accepted changes; one Accept is one Undo. The **Delete** key removes the selected blocks.
5. **Export DOCX** (top right). Expect: a Word file named after your PDF.

## 2.2 Running from a command line (server or your own machine)

Use this to test the app locally or to check the models. Admin: no. Directory: the repository root (`C:\app` on the server).

**Navigate and activate an environment** (optional; the server itself uses the system Python)
```powershell
cd C:\app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```
Linux/macOS: `cd <repo>`, `python3 -m venv .venv`, `source .venv/bin/activate`, `python -m pip install -r requirements.txt` ✅ (venv step). Expect: the prompt shows `(.venv)`.

**Prepare the input:** a PDF that opens normally, at most 20 pages and 50 MB (the command line has no page-range option: all pages are read).

**Set the keys for this window only**
```powershell
$env:OPENROUTER_API_KEY = "<your-openrouter-key>"
$env:BODHAN_OCR_API_KEY = "<your-bodhan-key>"
```
(Linux/macOS: `export NAME="value"`.)

**A. One real-model check of the whole pipeline** (runs the notebook on the PDF, rebuilds, then tries the format and re-read shortcuts on one block; nothing is applied) ⚠️ with keys, ✅ failure path
```powershell
python -m ocr.smoke C:\path\to\small.pdf
```
Expect: it prints `route: … blocks: … job folder: …` and a before/after for each shortcut. Cost: a few cents.

**B. Run the app on your own machine, without sign-in** ✅ (Linux)
```powershell
$env:AUTH_DISABLED = "1"
python -m uvicorn ocr.api:app --host 127.0.0.1 --port 8000
```
Expect: `Uvicorn running on http://127.0.0.1:8000`. In a second window, `curl http://127.0.0.1:8000/api/config` prints `{"auth":false,"google_client_id":"","max_mb":50,"max_pages":20}`; open `http://127.0.0.1:8000` in a browser and use the app as in 2.1. Stop with Ctrl+C. Data is written under `.\data` unless `OCR_DATA` is set.

**C. The notebook by itself (advanced)** ⚠️ not run. The notebook is meant to be run top to bottom in Jupyter (Jupyter is not in `requirements.txt`; install it yourself). Set `PDF_PATH` and `PAGES` in the config cell first. Results go to `compare_out\infinite_ocr\<name>__<route>.docx` and `.blocks.json` next to where you run it.

## 2.3 Where the results are

| What | Where |
|---|---|
| The Word file you export | download from the screen: named `<your pdf name>.docx` |
| The same file on the server | `data\jobs\<job id>\output.docx` |
| Raw notebook result and map of blocks | `data\jobs\<job id>\compare_out\infinite_ocr\input__<route>.docx` and `input__<route>.blocks.json` |
| Page images used by the screen | `data\jobs\<job id>\pages\<n>.jpg` |
| Pipeline log / review log | `run.log` / `ops.log` in the job folder |
| Your edits and undo history | `history.json`; the untouched pipeline result is `blocks_pre.json` |

(The job id is the folder name; it is not shown on the screen.)

## 2.4 Check that it worked

- The document card says **Ready** and **Open** works; the top bar shows **Saved**.
- Export DOCX and open the file in Word: paragraphs, tables and equations appear, and every page's text is there. Pictures and diagrams are inserted as images.
- On the server: `Get-Content data\jobs\<id>\status.json` shows `"state": "done"`.
- For an install check, run `python -m pytest tests -q` ✅ (needs `npm test` first for one test).

## 2.5 Troubleshooting

| You see | Do this |
|---|---|
| "… is not on the allowed list" or Google refuses | ask the administrator to add your address |
| "PDF has N pages; limit is 20" / "file larger than 50 MB" | split or compress the PDF |
| "Daily limit: …" | wait; the limit counts the last 24 hours |
| "Failed — pipeline failed - see run.log" | upload again once (a one-off crash happened before); if it repeats, send the administrator the PDF and the end of `run.log` |
| "pipeline took longer than …" | the PDF is too big or the models are slow: split it |
| Assistant says the model's answer could not be read, or a card is skipped with a reason | ask again in other words, or select fewer blocks (at most 3 pages) |
| "… was edited after this proposal was made" | you changed the block meanwhile; ask the assistant again |
| A menu under Align or Table is not visible | hard-refresh the page (Ctrl+F5) after the administrator updates the server |
| Words are wrong in a scanned block | select the block, tell the assistant "check this against the image and correct it", then compare before accepting |

---

## Known issues and not tested

- Everything Windows-, Google-, Caddy-, AWS- or key-dependent is documented from the scripts but **not run**; the browser walk-through (`tests/ui_check.py`) was updated for the single assistant but **not run**.
- `requirements.txt` has no version pins.
- `go_public.ps1`'s header comment says it asks three questions; it asks four.
- Old job folders are never cleaned up automatically.
- A native crash in the Docling parser was seen once on the production server (see 1.9).
