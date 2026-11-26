# Architecture

## Overview
```
Browser ──HTTP──► MASTER (FastAPI + SQLite)  ◄──UDP 57990, unicast, signed──►  SLAVE ESP32 (one per gate)
                  people, badges, gates,                                         local cache, reader, relay,
                  rules, time windows, log                                       inputs, event queue on flash
```
- The **master** is the single source of truth: people, credentials, rules, time windows, gates, log, system log.
- The **slave** never asks for permission: it decides using the badge it read and the **cache** it received, even with the master powered off, then
  reports the event. The decision is therefore always slightly "stale" relative to the master: this is why "Send data" exists.

## UDP protocol (port 57990)
Implementation: master `backend/services/udp_agent.py` (docstring at the top of the file), slave `firmware/esp32/lib/limen/slave.cpp`.
- Small packets: `TYPE|GATE_ID|TS|fields...|signature`. Signature = **HMAC-SHA256** (first 32 hex characters) computed over the part to the left of the last `|` using the gate's `api_key`. Timestamps outside ±30 s (`FINESTRA_REPLAY_SEC`) are discarded.
- Cache: compact JSON + `\n` + signature, split into datagrams of at most 1350 bytes (`spezza_cache`).
- The master accepts packets for a gate **only from the IP registered** for that gate.
- Table of types (HB, EV, SP, IN, SU, CA, RC, EA, PR / HI, PA, AP, UC, AE, cache): see `README.md`.
- **`HI` touch**: the master sends `HI` to the gate's IP for as long as it is silent (3 s, then 15 s); the slave learns the master's IP only from a valid signed touch and replies with `HB`. With an invalid key it replies `PR`.
- **Phones (BLE)**: the cache meta carries `kt`, the site phone key encrypted for that gate (key XOR HMAC(gate key, `PHONE-KEY|id|versione`)). Pairing packets: `BP|id|ts|seconds|kid|k_enc` master→slave opens the pairing window for one invite (`k_enc` = invite key XOR HMAC(gate key, `BP-KEY|id|ts`); `seconds` 0 closes it); `AB|id|ts|kid|nonce|phone_nonce|mac` (hex) slave→master, sent only when the phone's proof is right, repeated up to 3 times; `AR|id|ts|ok|kid|code` or `AR|id|ts|no|kid` master→slave. Calculations: `backend/services/ble_abbinamento.py` = `firmware/esp32/lib/limen/ble.cpp`. The cache is accepted only from the master IP and only with a newer `versione`.
- **Site key** (16/11): the phone key above is the **site key** (`chiave_telefoni` in `impostazioni`, `services/chiavi_impianto.py`). Phones derive their secret from it with the label `LIMEN-PHONE`; DESFire cards use `HMAC(site key, "LIMEN-DESFIRE-v1")[:16]` as the AES site key. Same key → same results, so restoring the key file brings phones and cards back. «Rigenera» deletes phone and DESFire credentials.
- **DESFire cards** (`firmware/esp32/lib/limen/desfire.*`, `backend/services/desfire.py`): on the card a Limen application (AID `4C 4D 01`) with two AES keys, 0 = management, 1 = read, each derived per card with NXP AN10922 (CMAC) from the site key and `UID | AID | key no. | "LIMEN"`. Reading: select the application and authenticate (AES, EV1 `AA`) with key 1; success proves the UID, and the credential code is `DF-<UID>` (no file on the card). Other cards (MIFARE Classic, blank or foreign DESFire) are read by UID as before. Writing: `DW|id|ts|session|seconds` master→slave opens a writing window (`seconds` 0 closes it); the reader writes the first blank DESFire card placed on it (create application, authenticate with the factory key, change key 1, re-authenticate, change key 0, check with key 1; an interrupted writing is completed on the next attempt) and answers `DR|id|ts|session|result|uid|reason` (`scritta`, `gia`, `altro`, `nondesfire`, `tolta`, `errore`), repeated up to 3 times until the master closes the session. Cleaning: `DC|id|ts|session|seconds|mode` (new type, so an old firmware ignores it instead of writing) with mode `i` (identify: the reader authenticates with key 1 and answers `DR` `nostra`/`nonnostra` with the UID) or `c` (clean: authenticate with the management key and delete only the Limen application, `DR` `pulita`); the master asks for confirmation when the card is assigned, then deletes its credential. The PICC itself is not touched, so the card is blank and writable again. Cleaning or rewriting a card is a **revocation**: the master sends the cache to the gates by itself (repeated until confirmed), like deleting a credential. **Reader behaviour**: after every exchange with an ISO 14443-4 card (DESFire, phone) the PN532 RF field is switched off and on (RFConfiguration item 01), because a card left active on the reader would not answer the next search; a card that stays on the reader is counted once (same credential within 1 s), and a cleaned card is counted by its UID so it does not show up as an unknown badge. The master links `DF-<UID>` to the card number printed on the card (the number is also **stored in each event**, `eventi.media_numero`, so passages keep the number the card had at that moment, whatever happens to the card later) (`AccessMedia.numero`, searched ignoring leading zeros). The PICC master key of the card is left at factory default for now: anyone can format the card (it then stops working), nobody can clone it.
- **Backup** (`backend/services/backup.py`): two files, the data (`.zip`, database with the gate keys replaced, the site key removed and invite keys cleared) and the keys (`.limenkey`, JSON: format version, site id, site key and its date, gate keys). Restore needs both from the same site (`impianto_id`), warns if the keys are older than the last «Rigenera», swaps the database and restarts the process. Old single-file backups still restore alone.
- **Pairing**: a slave without a key sends `PR|id|ts|nonce` (unsigned, carries no secrets). The admin presses "Pair" in the Gates page; the master sends `PA|id|ts|nonce|key`. The key travels in clear **once** on the LAN (do this only on the dedicated VLAN).
- **Reliable events**: persistent queue in the slave, retransmitted until the master replies `AE`; the master deduplicates with `Varco.ultimo_evento_seq`.
- **Known limitations**: no encryption (badge codes travel in clear, signed); anti-replay only via a 30 s time window, not a counter.

## The cache (what the slave decides on)
Built in `udp_agent.py` (`invia_cache`, `_costruisci_autorizzazioni_varco`, `spezza_cache`; periodic sending in `_loop_cache_periodica`). It contains: open duration, gate enabled, master time and time zone,
holidays, gate mode (normal / scheduled unlock / maintenance / emergency), operation (`fn`: readers,
outputs, inputs), time-window profiles (indexed, deduplicated) and the **badges**: `{"c": code, "s":1 | "f": windows, "d": from, "e": to, "x": block}`.
- A badge enters the cache only if it has an active rule on that gate; for the slave, a badge without permissions is "unknown".
- The master reconstructs the **real reason** for a denial (`services/motivi.py`) using the full configuration.
- Sending: every 6 h, on "Send data", after saves that require it, right after a credential is deactivated or deleted (revocation), and (block-mode anti-passback) after every granted passage of a person with anti-passback, on reset, and on settings changes (`aggiorna_cache_ap`). Every cache is acknowledged with `CA`.

## Permissions
Person → (groups → rules) and/or direct rules. **Access rule** (`Policy` model) = one or more gates + one time window (applies to all). A rule without a time window is "incomplete" and grants nothing.
Time windows in half-hour steps with holidays (day 7 of the grid). Evaluation in `services/policy_engine.py`.
Visitors: people with mandatory expiry (user and badge), same cache as people.

## Anti-passback
Per gate: switch, mode **"Alarm only"** (soft) or **"Block access"** (hard), optional timeout ("forget after N minutes").
Per person: switch. A violation requires **both**. The position (`passback_stato` inside/outside, gate, time) is updated by the master on every granted passage.
- Soft: the passage is granted and an alarm is raised.
- Hard: for anyone recorded as "inside", the master sets `x = <unix expiry instant>` on their credential in the gate's cache (`policy_engine.blocco_ingresso_fino`; without a timeout = 4102444800). The firmware denies with `negato_anti_passback` **only on entry readers** and **only while `now < x`**; exit always passes (even on a gate with one entry reader and one exit reader). Without a valid time the slave stays blocked.
- `x` is an instant, not a yes/no, because the slave must honor the timeout expiry on its own.

## Gate operation
Per-gate popup (`services/funzionamento.py`, JSON stored in the gate, carried in the cache as `fn`): readers (technology, direction, outputs opened),
outputs (Opens, Siren, Alarm arming, Other), inputs (Door sensor, Button/remote, Other). Hardware wiring is on the slave, behavior is decided by the master.

## ESP32 firmware (`firmware/esp32`)
C++ core without Arduino in `lib/limen/` (tested on PC with `test/build_host.sh`), Arduino part in `src/main.cpp`.
Configuration: the device's web page or `data/config.txt`. It learns the time from the master/NTP (without an RTC: badges with time windows do not work until the time is known).
Reader: **PN532 over UART** (`src/lettore_nfc.*`, frames in `lib/limen/pn532.*`) reads MIFARE UID, DESFire (`DF-<UID>`) and phones over NFC, and calls `Slave::badge(code, reader)`; BLE phones are served by `src/ble_cred.cpp`; the web test console remains for the bench. Two LEDs (green granted, red denied) are driven through `Hal::setLed`. RS-485 and the reader-only build are not written yet. The all-in-one reader prototype (ESP32-S3 + PN532 + dry-contact relay + 2 LEDs, 12 V in) was built and confirmed working on the bench on 26/11; pins and wiring in `firmware/esp32/README.md`.
- Slave interface in English only: page (`src/pagine.h`), log and serial messages, `config.txt` (keys in English; the old Italian ones are still accepted), README. The internal `/api/status` JSON keeps its field names; the page translates for display the values coming from the core (roles, `negato_…` outcomes).

## Master: parts to know
- Models in `backend/models.py` (Persona, Gruppo, AccessMedia, Varco, FasciaOraria, Policy, Evento, LogSistema, Impostazione, Festivita, ApiAccount/ApiToken…). Lightweight migrations: `COLONNE_AGGIUNTE` in `main.py` (no Alembic).
- Sessions and permissions: `auth.py`; user with `is_admin` (admin) or operator.
- Data: `LIMEN_DATA` (if set) holds the database, `certs/` and `session_secret`; if not set, everything stays in `backend/` as before.
- Log with stable codes (`services/log_codici.py`); data maintenance and retention (`services/manutenzione.py`, cleanup every 6 h, `INTERVALLO_SEC`).
- Real-time system health: `services/salute.py`.
- Inbound API (entirely in English: paths, fields, values; internally it stays Italian): `/api/v1`, accounts separate from the web app's users (`ApiAccount`), full access for every account, tokens expiring after 48 h, passwords with bcrypt, login rate limit (5 failures/15 min per address+user). PDF documentation: source `backend/docs/api_docs.html` → `genera_pdf.py` → `limen-api.pdf`.
- **Interface language (it/en)** — `backend/services/lingua.py`. The source stays Italian: every sentence goes through `t("frase %(x)s", x=…)` (Python and Jinja); `lingue/en.json` maps the Italian sentence → English (key with normalized whitespace; if the translation is missing, the Italian is shown). In JavaScript: `T('frase {0}', a)` with the catalog from `/lingua/catalogo/{codice}.js`. For module-level constants use `pigro("frase")` (translated when written out; does not work for `tojson`, `+`, `.replace`). Request language: the user's session (`User.lingua`) → `lingua` cookie (login) → `lingua_di_sistema` (System properties, admin only); chosen from the login page and from the "Language" drawer in System properties (button at the top right, `?pannello=lingua`; the default only for admins; `GET /lingua/{codice}`). `t()` with an HTML parameter (`Markup`, e.g. macro output) returns HTML and escapes the other parameters. **Not translated**: values in the database, protocol/firmware, holiday names, the support report, `LEGGIMI.txt` in the diagnostic package; the texts in the Log and event details stay in the language in effect when they were written. In templates, do not name a variable `t` (it hides the function).
