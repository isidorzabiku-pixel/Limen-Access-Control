# Features and protocol

Detailed reference of what the master web app does and how master and slave talk to each other. Overview and installation:
`README.md`. Documentation for developers: `ARCHITECTURE.md`, `DEVELOPMENT.md`.

## How master and slave communicate

Slaves talk to the master **over UDP only** (port 57990, unicast): they receive a cache of the credentials authorized on that
gate and decide locally, badge by badge, without ever asking the master for permission in real time; they send a report
*after* the access has happened. The device address is entered **only on the master** (Gates page): the master is the one that
"touches" each silent device first, with a signed `HI` packet to the registered IP; from that, the device learns the
master's IP and replies (heartbeat, or a pairing request `PR` if it does not have the key yet). The slave
never writes first and does not broadcast; the master accepts packets only from the IP registered for that gate, and the cache
is sent in unicast to each gate.

Every cache sent has a real delivery confirmation: the slave replies with a `CA` packet right after having
processed it, reporting the same version. The "Send data to devices" button waits up to 2 seconds for this
confirmation and shows how many gates actually confirmed (UDP alone does not guarantee delivery). Remote opening
from the web app goes only through the `AP` UDP command, so the gate must have an IP address on the Gates page.

## Web app features

- **Dashboard**: gates online/offline, today's accesses and last 7 days, "Inside now" (if enabled), denied accesses by reason (clickable through to Monitoring).
- **Monitoring** (formerly Monitoring + Register): a single page for passages at the gates. Without a period it is **live** (refreshes every 3 s); choosing "From / To" or browsing the pages turns it into the **history** (100 per page) with filters (outcome, gate, person/credential) and CSV export (also for attendance reports). `/registro` redirects here. Alarms are taken over from the **Alerts** page. Every rejection reports the **precise reason**, saved in the event (see below), and the › button, on the right of each row, opens a **side panel** with a summary and full explanation ("Why?") without moving the table; the "Precise outcome" filter searches by reason; a truly unknown badge can be assigned to a person from the row.
**Consistency between configuration and displayed state.** The web app never shows a state that the current configuration does not justify: the door sensor state appears only if an input is set as "Door sensor"; the on/off state of an output disappears if the output changes role; the clock and outputs of an offline device are marked as "last known state"; a newly created gate is "waiting for first connection" (a warning, not a fault). Commands (opening, outputs) to an **offline** device are not sent and do not leave a non-existent opening in Monitoring. A time window used by any rule cannot be deleted (it would make them valid 24 hours a day). **Anti-passback** per gate is "Alarm only" (reports) or "Block access" (whoever is already inside does not open the entry readers; exit always passes): see `docs/ARCHITECTURE.md`. If the gates that count presence are no longer enough to know who is inside, the remembered positions are reset.

- **Log** (tab of **System info**, `/sistema/info?scheda=log`; `/log` redirects there): system journal for troubleshooting. Each row has **level**, **origin**, **gate**, a stable **event code** (`DEV_ONLINE`, `DEV_OFFLINE`, `PKT_FIRMA`, `PKT_IP_ERRATO`, `CACHE_CONFERMATA`, `AUTH_LOGIN_KO`, `OP_<ambito>` for operator actions…), the **user and address** of whoever performed the action, and **structured details**. At the top is the summary of the last 24 hours (errors/warnings, clickable); filters by level, period (quick or From/To), gate, origin, event, user, and search in the text/IP/details; live refresh and CSV export (with UTC time, code, user, IP, details). The `›` button on each row opens a **drawer** with all the fields, the meaning of the code and what to check, "What happened around it" (±5 minutes on the same gate) and "Copy for a report". It records, with IP and duration, **online/offline** (one row per state change), pairings, cache sending/confirmation, **discarded packets with the reason** (repetitions merged every 5 minutes with the count), master start/stop.
  - **Diagnostic mode** (admin): for 15–120 minutes every packet received and sent goes into the log at "diagnostic" level (visible only with the filter of the same name); it turns itself off and those rows are deleted after 24 hours.
  - **Diagnostic package** (admin): zip with the log of the last 7 days and the system and gates status (without keys or passwords), to attach to a support request.
- **Inside now**: **"Counts presence"** option on each gate (off by default, in the gate's detail). If there is at least one entry gate **and** one exit gate that use it, the dashboard shows the "Inside now" card and, by clicking it, a side panel with the list (search, CSV for an evacuation, "Mark as out" for those who left without swiping). With entries only it is impossible to know who has left, so the card does not appear. For historical reports (hours, entries, exits) use Monitoring with its CSV.
- **People / Groups / Access rules / Permissions**: guests with validity, CSV import, "what can this person do" view. On **Groups** and **Access rules** the **Edit** button opens the row for editing: for groups name, people and rules; for rules name, **one or more gates** (same time window on all of them), time window and enable/disable (takes effect immediately for everyone who uses them; it reaches the gates with "Send data").
- **Rejection reasons**: the slave decides with the cache it receives, and the cache contains only the badges with an active rule on that gate; to it a badge without permissions is "unknown". The master, which knows the whole configuration, replaces that generic outcome with the real reason and writes it into the event (outcome + detail, it stays even if the configuration later changes): person suspended, badge disabled or expired, **no rule** (no group and no direct permissions), **not authorized on this gate** (has rules but for other gates), **rule disabled**, **outdated data** (has the permission but the device does not know it: "Send data"); for out-of-window rejections the detail reports the allowed hours. Only a code that belongs to no person remains "not recognized". It also works with slaves already installed (no protocol change).
- **Credentials on the person's row**: each person can have several access credentials (badge, UHF, BLE, PIN, QR). The list shows them as chips (type + code, PIN masked `••••`, "+N" beyond 3, struck through if disabled/expired, "expires dd/mm"), with filters by type (with counts), Detail/Compact view and search also by code (PINs are not searchable). The PIN can be read in clear text only in the person's card. Limits: UHF and BLE can already be selected but no readers that use them exist yet; PINs share the global uniqueness of the code and appear in clear text in the events Register.
- **Time windows**: half-hour grid with ready-made templates, copy of a day, hours/week summary; **Holidays** (import of the Italian ones) for the windows that exclude them.
- **Gates**: mode per gate (normal, always closed, emergency, scheduled unlock), anti-passback with timeout, door state, "outdated data" banner if the configuration has changed and has not yet been sent to the devices (with a "Send data" button); a send not confirmed by an online gate appears on the Alerts page. Only admins can delete a gate (Device tab → Danger zone): the confirmation lists the linked policies and the Register entries that will be lost; the gate is removed from all its rules; those left without gates are **not** deleted: they remain "incomplete" and grant no access until another gate is chosen. Passages in Monitoring and in the Log remain available.
- **System info** (`/sistema/info`): at the top the health status (UDP service, database, disk, memory, clock, backup and, for each gate, heartbeat, clock, cache, door open for too long, event queue; it refreshes itself every few seconds), below the device data. The old `/stato` page redirects here. **Administration** (users, backup, restart, data retention) is for admins only; operators can only change their own password.

Explanations are not written in small print on the pages: they sit behind an ⓘ icon (on mouse hover, keyboard focus or touch). The interface is usable from a phone (card-style tables, bottom navigation bar, confirmation dialogs with the list of consequences) and loads nothing from the internet: htmx and fonts are served from `/static`.

## UDP protocol (port 57990)

The small packets are `TYPE|GATE_ID|TS|fields...|signature`, with an HMAC-SHA256 signature (first 32 hex characters) computed with the gate's `api_key`; those with a timestamp outside a 30 s window are discarded. The cache, instead, is a JSON followed by its own signature.

| Direction | Packet | Use |
|---|---|---|
| slave → master | `HB` | heartbeat every 3 s (only after the first touch: the slave never writes first) (slave clock and drift, queued events, current state of door and outputs); offline after 12 s of silence |
| slave → master | `EV` | access event (badge, outcome), with sequence number and gate time |
| slave → master | `SP` | door sensor state (open/closed/forced/left open), with sequence like `EV` |
| slave → master | `IN` | input activated (button, remote control, "Other" input), with sequence like `EV` |
| slave → master | `SU` | on/off state of an output (alarm arming, toggle "Other"), with sequence like `EV` |
| slave → master | `CA` | confirmation of a received cache |
| slave → master | `RC` | cache request (at startup, until one arrives) |
| slave → master | `EA` | outcome of a remote opening |
| slave → master | `PR` | presentation for pairing (unsigned, no secrets): gate id and a nonce |
| master → slave | `HI` | touch `HI\|id\|ts\|signature` (unicast to the gate's IP, every 3 s while it is silent, then every 15 s): the slave learns the master's IP and replies with `HB` (or `PR` if it does not have the key or the key is no longer valid) |
| master → slave | `PA` | pairing (unicast, only after the admin's "Pair"): the gate's key, bound to the nonce |
| master → slave | `AP` | remote opening (unicast) |
| master → slave | `UC` | command to an output from the panel (unicast): pulse, turn on, turn off |
| master → slave | `AE` | confirmation of an event (ack, with the sequence) |
| master → slave | cache | credentials, in blocks of at most 1350 bytes |

**Slave:** the ESP32 firmware (`firmware/esp32/`) is the supported implementation. The Python agent in `agent/` is a test version, useful for trying out the protocol without hardware.

**Automatic pairing.** On the slave you only need to configure the network (IP, gateway, subnet) and the gate ID; the key does not need to be copied. The slave's IP is entered only on the master (Gates page): the master touches it with `HI` and the slave replies with `PR` at every touch until it has the key; the Gates page (admin only) shows "Device waiting … from <IP>" with the **Pair** button. On click the master sends `PA` in unicast to that IP, the slave accepts the `PA` only from whoever just touched it and saves the key and the master's IP in its own permanent memory. The master accepts a `PR` only from the IP registered for that gate, ignores those of an online gate and those with an invalid id or nonce; unconfirmed requests expire after 10 seconds and "Pair" tolerates a 30 s margin. If a silent gate has a key that the master does not recognize (gate deleted and recreated, key reset), the touch signed with the new key does not verify on the slave, which immediately presents itself again with a `PR` if the touch comes from the master it knows; the request appears within a few seconds. A new "Pair" is then enough; as soon as it is paired the slave immediately receives the cache. Two gates cannot have the same IP (creation, IP change and pairing reject it). Limit: the key travels once in clear text on the LAN at the time of pairing, so the devices' network must be kept closed; only administrators see the key and create gates.

Events go through a **persistent queue** in the slave: they are retransmitted until the master replies `AE`, and the master discards duplicates by comparing the sequence with `Varco.ultimo_evento_seq`. This way a master that is switched off or a lost packet do not lose accesses, and the event time remains that of the gate.

The cache carries with it: opening duration, gate enabled/disabled, master time and time zone (to correct the slave's clock and handle daylight saving time), holidays, gate mode with any unlock window, and the badges with their time windows and any validity (from/to, for guests).

### Gate operation ("Operation" popup)

What is connected to a gate and what it does is chosen from the web app: the gate's **Control** tab → **Configure**. On the device only how the hardware is wired remains (relay and input pins, network): see `firmware/esp32/README.md`. For now 1-2 readers, 2 outputs (relays) and 2 inputs.

- **Readers**: technology (125 kHz, MIFARE protected, MIFARE number only; NFC and BLE later), direction of each reader (for anti-passback and "inside now") and which outputs it opens with a valid badge. Several readers on the same gate share the permissions; for doors with different authorized people, separate gates are needed.
- **Outputs**: *Opens* (lock or gate, with its opening time), *Siren* (sounds when a warning is triggered), *Alarm arming* (armed/disarmed from the panel; level or 1 s pulse), *Other* (pulse or on/off, from the panel or from an input).
- **Inputs**: *Door sensor* (door forced and left open beyond N seconds), *Button or remote control* (opens the "Opens" outputs), *Other* (logs, or drives an output). NC/NO contact in Advanced.
- **Modes** (Normal, Scheduled unlock, Maintenance, Emergency): chosen at the top of the popup and a change reaches the device immediately. The behavior is fixed: in Emergency and Unlock the "Opens" outputs stay energized and the siren is silent; in Maintenance the badge does not open but the button does. In Maintenance and Emergency the rest of the popup (readers, outputs, inputs, warnings) is greyed out and cannot be edited until you return to Normal (or to Unlock).

The operation travels in the cache (compact field `fn`) and the slave applies it locally, even with the master switched off. Without a saved operation (gates created earlier) everything stays as before: one reader and output 1 for the opening duration. The new events (opening from an external command, input activated, output driven from the panel, reader number) appear in Monitoring. **Update the master first and then the slaves**: an old master does not know the `IN` and `SU` packets.

### Limits to know about

- Slaves decide locally: the anti-passback block works because the master marks in the cache who is inside; the cache must therefore be sent again after every passage (the master does this).
- The cache has no replay protection (known, to be addressed together with possible encryption).
- A daylight saving time change is picked up at the next cache.
- The alarm arming does not replace the intrusion alarm panel, and the release for regulation-compliant evacuation remains hardware: the software does not replace it. The command to an output from the panel is fire-and-forget (like remote opening) and the state shown is the last one reported by the device.
- "Inside now" is reliable only if every counted entry gate has a corresponding counted exit gate (or an exit reader on the same gate); whoever leaves without swiping stays "inside" until they pass through an exit or you remove them by hand.
- In the time window grid on touch there is no painting by dragging: you tap the first and the last cell.

## Updating an existing database

At startup the backend creates the missing tables and automatically adds the new columns to the tables already present (`COLONNE_AGGIUNTE` in `backend/main.py`): a database created with an earlier version is updated without losing data.
