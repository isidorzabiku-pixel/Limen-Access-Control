# Development, testing, release

## Git
- Source on GitHub; `dev` is the development branch, `main` is stable.
- Work and push on `dev`: `git fetch -q origin dev && git rebase origin/dev && git push -q origin dev` (with local changes that cannot be published: `git stash` / `git pull --rebase` / `git stash pop`).
- Code, comments and commit messages are in Italian.
- To commit only part of a mixed file, use a temporary worktree on `origin/dev`, copy the clean files and apply the edit with a string replacement.

## Starting the master
`cd backend && python3 -m venv venv && . venv/bin/activate && pip install -r requirements.txt && ./avvia.sh` (first login admin / admin; `python create_admin.py <user> <password>` creates another admin) → **https://<ip>:8443** (on first start it creates `backend/certs/`; the browser warns once about the self-signed certificate). In production: `deploy/limen.service`, which runs `avvia.sh`. Switching to HTTPS changes the addresses (8443): update your bookmarks.
Only for tests without HTTPS: `uvicorn main:app --port 8000` (the session cookie is not "secure").
The server uses the master's fixed IP; after a `git pull` a restart is needed. New columns are added automatically at startup.

## Docker (master on Linux)
`docker compose up -d --build` (files: `Dockerfile`, `docker-compose.yml` in the root). **Host** network (UDP 57990 to the ESP32s without port mappings), HTTPS on **https://<server-ip>:8443**. Database, certificates and session key live in the `limen-data` volume (`/data`, variable `LIMEN_DATA`): they survive updates. First login: **admin / admin**; the web app shows a warning until the password is changed (System properties). Update: `git pull && docker compose up -d --build`. Backup: "Download backup" in System properties, or `docker run --rm -v limen-data:/d -v $PWD:/b alpine tar czf /b/gk-dati.tgz -C /d .`. Real certificate: put `cert.pem` and `key.pem` in `/data/certs`.
**Not yet verified on a real server**: the container was written without access to a Docker daemon. Windows/Mac: Docker Desktop's host network is unreliable; for Windows the planned route is an installer with Python included (to do).

## ESP32 firmware
- Build/flash: PlatformIO (VS Code extension or CLI) from `firmware/esp32`: `python -m platformio run -t upload --upload-port COMx`.
- Tests on PC without a board: `sh firmware/esp32/test/build_host.sh /tmp/host_slave` (needs ArduinoJson 7, `ARDUINOJSON_DIR`).
- **After every change to the firmware core, the devices must be reflashed**, otherwise the new behavior is not there: say so in the release notes.

## Tests
In the repository today: `backend/tools/test_lingua.py` (translation layer), `backend/tools/controlla_traduzioni.py` (missing translations), and for the firmware `firmware/esp32/test/` (`build_host.sh` builds the core on a PC; `test_config_txt.cpp`).

The larger development suite (a harness that stubs FastAPI/pydantic/bcrypt, `t_*.py` scripts per feature, `e2e_*.py` that start the real `host_slave`, Playwright page checks) has so far been kept **outside the repository** and is not portable. **To do**: rewrite it on top of the real FastAPI test client and move it into `tests/`; contributions welcome.
Practical rules: start a Playwright test server in the **same command** that uses it; never `pkill -f` (it kills your own shell); use Chromium for page checks.

## Before saying "done"
- Did you add or change visible text? Write it with `t()` / `T()` and add the translation in `backend/lingue/en.json`; then run `python3 backend/tools/controlla_traduzioni.py` (it must report zero missing translations) and `python3 backend/tools/test_lingua.py`.
- Was the behavior tested (tests or e2e), not just written?
- Did the firmware change? Devices need a reflash; the server needs `git pull` + restart (or a Docker rebuild) + "Send data".
- Changed the API? Update `backend/docs/api_docs.html` and regenerate the PDF (`python backend/docs/genera_pdf.py`, needs Playwright with Chromium), then copy it to `api/`.
- Are the design principles in `CONTRIBUTING.md` respected?

## License headers

New `.py`, `.sh`, `.cpp` and `.h` files start with the SPDX header (see any existing file; after the shebang if present). Contributors must accept `CLA.md`.

## User manual

The PDF manuals (`backend/docs/limen-manuale-it.pdf`, `limen-manuale-en.pdf`) are generated from `backend/docs/manuale/contenuti.py` and the screenshots in `backend/docs/manuale/img/`: edit them and run `python backend/docs/manuale/genera_manuale.py` (needs Playwright with Chromium). Regenerate whenever labels or workflows of the web app change.
