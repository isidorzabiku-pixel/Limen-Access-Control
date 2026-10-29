> **Test version.** The Python agent is a test tool for trying the UDP protocol and GPIO wiring on a Raspberry Pi; it is not maintained and not the supported slave. The supported slave is the ESP32 firmware in `firmware/esp32/`.

# Test slave (Raspberry Pi Zero 2 W, no hardware)

It is used to check that the webapp (master) and the slave really talk to each other: pairing, cache, badge
granted/denied, opening from the webapp, inputs and door sensor. Readers, relays and inputs are
simulated; the rest is identical to `agent.py`. No packages to install (only Python 3.9+,
i.e. Raspberry Pi OS Bookworm or later).

## On the Pi
```
tar xzf limen-slave-prova.tar.gz && cd agent
python3 test_slave.py --id 1
```
`--id` is the gate number (shown by the webapp in the Device tab, Pairing section).
The Pi must be on the same network as the master (the channel is UDP unicast, port 57990: the Pi's IP must be entered in the master's Gates page, which contacts it first).

## In the webapp (as admin)
1. **Gates → New gate**: create it (its number must match `--id`).
2. At the top of the page "Device waiting … from <Pi IP>" appears: press **Pair**.
   The slave prints `*** ABBINATO ***`; the gate's IP is registered automatically.
3. **Send data to devices** if the cache does not arrive by itself (`*** CACHE dal master ***`).
4. Create a person with a badge, a policy on that gate and send the data again.

## Commands to the slave
- `<code>` swipes a badge on reader 1 (`badge <code> 2` for reader 2)
- `porta aperta|chiusa`, `ingresso 1|2 on|off`: door sensor and inputs (their function is chosen in the Operation popup)
- `stato`, `dimentica` (deletes the key: it must be paired again), `aiuto`, `quit`

Press **Open** or send commands to the outputs from the webapp: the slave prints "USCITA n: IMPULSO".
Key, cache and event queue stay in `stato_test/`: on restart it resumes without pairing again.
`--verboso` also shows the protocol logs.
