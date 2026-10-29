# Limen · ESP32 slave

C++ firmware for the ESP32-S3 (logic tested on a PC; still to be tested on the board). The slave decides locally from
the cache received from the master, drives 2 outputs and reads 2 inputs, and sends the master heartbeat, events (with
acknowledgement and a flash-backed queue) and status. UDP protocol, unicast only: the ESP32's IP is written only on the
master (Gates page), which contacts it first; the ESP32 learns the master's address from that contact, so it needs no
master configuration and never broadcasts.

> The slave interface (device web page, serial messages, this file, `config.txt`) is **English only**. The master web
> app is available in Italian and English.

**There is no badge reader yet**: instead, the web page has a **test console** where you type a code; it follows the
same path as a real reader (`Slave::badge(code, reader)`). Once the reader technology is chosen, a small driver that
calls that function is all that is needed.

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
  (operation), `sha256.cpp` (HMAC), `hal.h` (what it asks of the platform).
- `src/main.cpp`: the ESP32 platform (Wi-Fi, flash, pins, web page); `src/pagine.h`: the pages.
- `test/host_main.cpp`: the same core on a PC with a real UDP network, to test it against the master
  (`test/build_host.sh`); `test/mock/` fake Arduino headers to check that `main.cpp` compiles;
  `test/test_config_txt.cpp` tests the reading of `config.txt`.

Tested on a PC against the real master: introduction and pairing, cache (also multi-packet, 3000 badges), time learned
from the master with the clock at zero, decisions (time windows, expiry, unknown), events with acknowledgement, opening
and output command from the dashboard, forced door, button, emergency, restart from flash and re-sync from the heartbeat.
**Not yet tested on the board**: build with the ESP32 toolchain, Wi-Fi, pins and the page on the real device.
