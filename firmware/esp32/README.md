# Limen · ESP32 slave

C++ firmware for the ESP32-S3 (logic tested on a PC; still to be tested on the board). The slave decides locally from
the cache received from the master, drives 2 outputs and reads 2 inputs, and sends the master heartbeat, events (with
acknowledgement and a flash-backed queue) and status. UDP protocol, unicast only: the ESP32's IP is written only on the
master (Gates page), which contacts it first; the ESP32 learns the master's address from that contact, so it needs no
master configuration and never broadcasts.

> The slave interface (device web page, serial messages, this file, `config.txt`) is **English only**. The master web
> app is available in Italian and English.

**Reader:** a PN532 NFC module (see *NFC reader* below) reads MIFARE badges, DESFire cards and phones; BLE phones are
served by the firmware itself. The web page also has a **test console** where you type a code; it follows the same path
as a real reader (`Slave::badge(code, reader)`).

## Getting started

1. **Build and upload** with PlatformIO (VS Code extension): open the `firmware/esp32` folder, *Build*, *Upload*.
   Serial monitor at 115200 baud (if you see nothing, try the board's other USB port).
2. **First boot.** Without a configuration the ESP32 creates the Wi-Fi network **`Limen-XXXX`** (password
   `limen-setup`). Connect and open **http://192.168.4.1**: set
   - the **gate ID** (the same one you will use in the master),
   - the **Wi-Fi** name and password,
   - the **fixed IP** (192.168.1.90 is suggested), subnet and gateway,
   - an **admin password** (user `admin`), and the pins if the suggested ones don't suit you.

   After "Save and restart" it joins your network with that IP and the same page is available there (also at
   `http://limen-<ID>.local`).
3. **In the master**: *Gates → New gate* with the same **ID** and **IP** (the IP is mandatory: the master contacts the
   ESP32 at that address), then **Pair** when the device shows up as waiting. The key arrives by itself; then the slave
   asks for the cache.
4. **Try it**: on the device page type a code and press "Swipe badge".

**Alternatively, a text file.** Copy `data/config.txt.esempio` to `data/config.txt`, fill in the ID, Wi-Fi name and
password, IP and (optionally) pins, then run **Upload Filesystem Image** in PlatformIO. On restart the device reads the
file, saves the settings and **deletes** it (the Wi-Fi password does not stay in clear text in the flash). A file with
errors is ignored and stays there, with the reason on the serial monitor. `data/config.txt` is excluded from git.
Warning: "Upload Filesystem Image" erases the whole data flash, so "Pair" has to be done again in the master. The
setup page works too: use whichever you prefer. Keys are in English (`wifi_name`, `admin_password`, `relay1`, `input1`,
`led_green`…); the old Italian keys are still accepted.

If you get the Wi-Fi wrong, after 25 seconds the device also opens the `Limen-XXXX` network again. For factory
settings hold the **BOOT** button for 5 seconds (erases settings, pairing and cache).

**One new device at a time**: unconfigured devices all show up with the `Limen-XXXX` network (no conflict), but
two devices with the same fixed IP do conflict: give each its own IP.

## Default pins (changeable from the setup page)

| Function | GPIO | Notes |
|---|---|---|
| Relay 1 (output 1) | 4 | tick "active high", or untick for active-low modules |
| Relay 2 (output 2) | 5 | |
| Input 1 | 6 | internal pull-up: contact or button to ground |
| Input 2 | 7 | |
| Green LED | 8 | lights up when an output opens the door (valid badge, opening from the dashboard, button) |
| Red LED | 9 | lights up for 2 seconds after a denied badge |

**Simulating open and denied.** Connect a relay (or an LED) to output 1 and two LEDs with a 330 Ω resistor to ground to
the LED pins: green = granted, red = denied. The same two LEDs appear as lamps next to the result on the device page,
so the test works even with nothing wired. To not use them, set -1.

What each output and input does (opens, siren, arming, door sensor, button, "Other") is decided by the master from the
*Operation* popup, as with the other slaves. Avoid pins 19, 20 (USB) and 26-37 (flash/PSRAM), and better not to use 0,
3, 45, 46 (used at boot). Relays must be driven through a suitable module (transistor/optocoupler), not directly from
the pin.

## All-in-one reader prototype (bench-tested 26/11)

One board that is reader and slave: Freenove ESP32-S3 (8 MB) + PN532 (HSU) + 12 V → 5 V buck + 3 V relay module (dry
contact COM/NO) + green and red LED. A TTL↔RS-485 module is wired for the future reader head, but there is no RS-485
driver yet. Same firmware as every other board (`pio run -e esp32s3`); only the pins differ, set on the device page or
with `data/config.txt.prototipo`:

| Function | GPIO | Note |
|---|---|---|
| PN532 | 16 (RX ← PN532 SDA/TX), 17 (TX → PN532 SCL/RX) | fixed, both module switches OFF |
| Relay 1 | 21 | trigger jumper on **H** (active high); dry contact only, the lock supply never goes through the board |
| Green / red LED | 1 / 2 | 330 Ω to GND each |
| RS-485 module (wired, no driver) | 42 TX, 41 RX | auto-direction module, no DE/RE pin |
| Relay 2, inputs | -1 | not connected |

Power: 12 V → PTC fuse → Schottky diode (reverse polarity) → buck set to **5.0 V** (check with a meter first) → the
board's 5V pin and the PN532. The relay and RS-485 modules run from the board's 3V3 pin. **Do not connect USB and the
buck's 5 V at the same time**: whether the board has a diode between USB and its 5V pin is not verified. To flash,
switch the 12 V off and use the UART (CH343) USB port. Avoid GPIO 0, 3, 45, 46 (boot), 35-37 (PSRAM), 19/20 (USB).

## NFC reader (PN532)

The firmware drives a **PN532** NFC module over HSU (UART): **RX = GPIO16 ← PN532 TX (SDA pin), TX = GPIO17 → PN532 RX
(SCL pin), VCC → 3V3, GND → GND**, 115200 8N1. Set **both mode switches on the module to OFF** (HSU) before powering
it. Pins 16 and 17 are fixed and reserved: the setup page refuses them for other functions.

It looks for ISO 14443A cards (MIFARE Classic/Ultralight, DESFire, NFC tags) about ten times per second and reads the
**UID only**, as uppercase hex without separators (e.g. `DEADBEEF`, `04112233445566`): register the badge on the master
with that code (codes are case-sensitive). The UID goes to reader 0 with the same rules as the console. A card held on
the reader counts once; it must be away for 1 s to count again. 4-byte UIDs starting with `08` are random (phones
without the Limen app, some DESFire cards) and are ignored. The device page shows the reader state (`PN532 1.6` or `not found`); if the PN532
does not answer the firmware retries every 2 s. The serial monitor shows `[nfc]` lines.

**Android phones (NFC).** When the target speaks ISO 14443-4 (SAK bit `0x20`: phones, DESFire), the reader first
selects the Limen app (AID `F04C494D454E01`). If the phone answers, it sends a fresh 16-byte challenge and checks the
reply (credential code + HMAC) exactly like the BLE phone credential (`Slave::verificaAperturaBle`), then calls
`Slave::badge(code, 0)`: same rules, same events, nothing changes on the master. A wrong credential gets no reply and
no event. Otherwise the target is treated as a badge (UID). Protocol in `lib/limen/nfc_telefono.h`. This works in
every build once the gate has the site phone key (it arrives with the cache).

**DESFire cards (AES).** Cards written by this site carry a Limen application (AID `4C 4D 01`) with two AES keys
derived per card from the site key and the UID (NXP AN10922). The reader selects the application and authenticates with
the read key: success means a genuine card of this site and the code `DF-<UID>` goes to `Slave::badge`. A copy (other
UID) or a card of another site gets no reply and no event. Blank or foreign DESFire cards, and every other card, are
read by UID as before. When the master opens a writing window (`DW`), the first blank DESFire card placed on the reader
is written instead (an interrupted writing is completed the next time) and the result goes back with `DR`; the card
then does not also open the door. Code in `lib/limen/desfire.*` (AES from mbedTLS, `lib/limen/aes.*`). The site key
arrives with the cache: without it DESFire cards are read by UID.

UID-only is a weak credential (a UID can be copied): use DESFire cards where it matters.

## BLE phone reader

Included in the single firmware (`pio run -t upload`; `esp32s3_blecred` is an alias of the same env). `src/ble_cred.*`, device name `Limen-XXXX`. Each phone has a credential code and a secret received at pairing; to open it answers the reader's
challenge with an HMAC. An unpaired phone only gets `NO` and leaves no trace. Pairing is started from the master
(person → Access credentials → + Add credential → Mobile → Create invite). The protocol is described at the top of `src/ble_cred.h`. Tested on the
bench with several Android phones.

A ready-built `limen-esp32s3-blecred-*.bin` is attached to each GitHub Release (check its SHA-256). It is the application
image only: to update a board already flashed with PlatformIO, run
`esptool.py --chip esp32s3 write_flash 0x10000 limen-esp32s3-blecred-0.6.0.bin` (default partition table; settings and
cache in the flash are kept). For a new board use `pio run -e esp32s3_blecred -t upload`.

With nothing wired an input reads "high": from the page you can **force the level** of each input to test the door
sensor and the button (for an NC contact, pin high = contact open = door open).

## What it does and doesn't do

- Local access decision (time windows, holidays, badge validity, disabled, maintenance, emergency/unlock: free door),
  even with the master off; cache, key and event queue survive a restart in the flash.
- Clock: the ESP32 has no battery. It learns the time from the master (it arrives with the cache; the master also sends
  it by itself if the slave's is wrong) and, if there is internet, from NTP too. **Until the time is known**, "always
  valid" badges work, while those with time windows or an expiry are denied. After a restart without the master the
  time has to be re-synced.
- Heartbeat every 3 s with door and output status; the master marks it offline after 12 s of silence.
- **Wi-Fi** only for now (the logic does not depend on the network: for wired Ethernet only the part in `src/main.cpp`
  needs changing).
- Memory: the cache lives in RAM (about 32 bytes per badge, double during an update). Tested with 3000 badges; with
  more badges a board with PSRAM is better.
- Security: the device page requires the `admin` password; the initial setup network is protected only by the factory
  password `limen-setup` and is open only while the device is unconfigured (or the Wi-Fi is unreachable). The pairing key
  travels in clear text once on the LAN, as for the other slaves.

## Structure

- `lib/limen/`: the core, with no Arduino dependencies. `slave.cpp` (gate logic), `cache.cpp`, `fn.cpp`
  (operation), `sha256.cpp` (HMAC), `pn532.cpp` (PN532 frames), `nfc_telefono.cpp` (phone over NFC), `desfire.cpp` + `aes.cpp` (DESFire cards), `hal.h` (what it asks of the platform).
- `src/main.cpp`: the ESP32 platform (Wi-Fi, flash, pins, web page); `src/pagine.h`: the pages.
- `src/lettore_nfc.cpp`: the PN532 reader on the UART.
- `test/host_main.cpp`: the same core on a PC with a real UDP network, to test it against the master
  (`test/build_host.sh`); `test/mock/` fake Arduino headers to check that `main.cpp` compiles;
  `test/test_config_txt.cpp` tests the reading of `config.txt`; `test/test_pn532.cpp` tests the PN532 frames; `test/test_desfire.cpp` the AES/CMAC/AN10922 vectors, and with `test/sim_desfire.py` (a DESFire card simulated in Python) reading and writing.

Tested on a PC against the real master: introduction and pairing, cache (also multi-packet, 3000 badges), time learned
from the master with the clock at zero, decisions (time windows, expiry, unknown), events with acknowledgement, opening
and output command from the dashboard, forced door, button, emergency, restart from flash and re-sync from the heartbeat.
**Not yet tested on the board**: build with the ESP32 toolchain, Wi-Fi, pins and the page on the real device.
