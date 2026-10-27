# Infinite OCR on the Windows server

Not tested on Windows here. The setup script was syntax-checked and its logic run in PowerShell 7, not on Windows Server.

## A. Updating the app (your settings, domain, Google sign-in and documents stay)

Upload `infinite_ocr_app.zip` to your S3 bucket (put its name and region in the `aws` line below), then in an Administrator PowerShell on the server, one line at a time:

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

`robocopy` replaces the program files and leaves three things alone: the `data` folder (your documents), `start.bat` (your
sign-in settings) and `Caddyfile` (your domain). It prints a summary; exit codes 0 to 7 are all fine. Anything else you put by
hand in `C:\app` (such as a sample PDF) is removed.

Prompt files in `C:\app\data\prompts\` override the built-in wording and are never touched by an update. After an update that changes the assistant's wording, delete them once (`Remove-Item C:\app\data\prompts\*.txt`); they are recreated from the new built-in wording on first use. Skip this if you want to keep your own edits.

Check: `curl.exe http://127.0.0.1:8000/api/config` prints one line of JSON, then open your address from your own computer.

## The assistant's wording (editable, survives updates)

The text sent to the model lives in plain files in `C:\app\data\prompts\`, created the first time the assistant is used:

- `assist.txt` is the prompt of the one assistant on the screen (any request: fix, merge blocks, split a merge, add a table, check against the image, delete). Keep `{note}` and `{tags}` and the answer format (`<REPLY>` and `<ACTION type="edit|merge|split|insert|delete">`), which the server reads.
- `default_instruction.txt`, `fix_formatting.txt` and `reread_hint.txt` are no longer used by the screen. They remain for the API shortcuts (`mode` format / reread) and for `python -m ocr.smoke`.

The assistant remembers the session: the last six requests with what was accepted or rejected (read from `data\jobs\<job>\proposals`), and, on a merged block, the pieces it was made from.

Edit a file in Notepad: the next request uses it, with no restart. If a file loses a required placeholder, the built-in wording is used and a warning goes to `ops.log`.
To return to the built-in wording, delete the file. `C:\app\ocr\intent.py` only serves the API shortcuts above.
If you updated from an older version, delete the old prompt files once: `Remove-Item C:\app\data\prompts\*.txt` (new `assist.txt` is created on first use).

## B. Let in every Gmail account (run once)

    (Get-Content C:\app\start.bat) -replace "^set ALLOWED_EMAILS=", "set ALLOWED_DOMAIN=gmail.com`r`nset ALLOWED_EMAILS=" | Set-Content C:\app\start.bat -Encoding ASCII
    Get-Content C:\app\start.bat
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-ScheduledTask -TaskName OCR-App

The second line shows the file: there must be a `set ALLOWED_DOMAIN=gmail.com` line above the `python` line. Running the first line twice
adds a duplicate line, which is harmless. (Re-running `go_public.ps1` also lets you set this, in its fourth question.)

Google must allow it as well. In the Google console open "Audience" (Google Auth Platform) and choose **Publish app**. While the
app is in Testing, only your listed test users can sign in, whatever the server says.

Anyone with a Gmail account can then use your OpenRouter and Bodhan credit. Each person is limited to **100 pages per 24 hours**
(`USER_DAILY_PAGES`). To change it, add a line such as `set USER_DAILY_PAGES=300` above the `python` line in `C:\app\start.bat`
(0 turns the limit off), then restart as above. A single PDF is limited to 20 pages (`MAX_PAGES`) and 50 MB (`MAX_UPLOAD_MB`).

## C. First-time setup of a new server

1. A free name at https://www.duckdns.org pointing at the server's Elastic IP.
2. A Google "Web application" OAuth Client ID with `https://your-name.duckdns.org` as an Authorized JavaScript origin.
3. AWS security group: allow HTTP (80) and HTTPS (443) from anywhere, and RDP (3389) from "My IP" only.
4. Put the app in `C:\app`: `mkdir C:\app`, copy the zip from S3 into it, `Expand-Archive C:\app\app.zip C:\app -Force`, then
   `Copy-Item C:\app\ocr_server\* C:\app\ -Recurse -Force`, remove `C:\app\ocr_server` and `app.zip`, run `python -m pip install -r requirements.txt`, then:

       powershell -ExecutionPolicy Bypass -File C:\app\go_public.ps1

   It asks for the domain, the Client ID, the allowed emails and an optional mail domain; it installs Caddy (HTTPS), opens the ports,
   and starts the app and Caddy at every boot. Restart the server once to prove it.

## If something is wrong

- The app's log: `Get-Content C:\app\data\app.log -Tail 40`
- A document's log: `Get-Content C:\app\data\jobs\<job folder>\run.log -Tail 40` (and `ops.log` beside it for Fix formatting and Re-read)
- Re-read or Fix formatting says a key is missing: `setx OPENROUTER_API_KEY "..." /M` (and `BODHAN_OCR_API_KEY`), then restart the app.
- Google says "origin_mismatch" or the button is missing: the origin in the Google console must be exactly `https://` plus your name.
- To try the screen on the server only, without Google: stop the public setup first, so nobody can reach the unprotected app through the web address:

      Stop-ScheduledTask -TaskName OCR-Caddy; Stop-ScheduledTask -TaskName OCR-App
      Get-Process python, caddy -ErrorAction SilentlyContinue | Stop-Process -Force
      cd C:\app
      $env:AUTH_DISABLED = "1"
      python -m uvicorn ocr.api:app --host 127.0.0.1 --port 8000

  and open `http://127.0.0.1:8000` in Edge on the server. (Do not use `start.bat` for this: the one written by `go_public.ps1` clears `AUTH_DISABLED`.) Afterwards close the window and start both tasks again with `Start-ScheduledTask`. Not tested on Windows.
