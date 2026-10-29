> **Test version.** The Python agent is a test tool for trying the UDP protocol and GPIO wiring on a Raspberry Pi; it is not maintained and not the supported slave. The supported slave is the ESP32 firmware in `firmware/esp32/`.

# Real slave on a Raspberry Pi (no reader): outputs and inputs on the GPIO

For now the gate is controlled from the webapp (Open, mode, outputs) and reads buttons/door sensor
from the inputs; the badge reader will be added later (Wiegand or RS485).
Requires Raspberry Pi OS **Bullseye or Bookworm** (Python 3.9+; Bookworm recommended, which also has `python3-lgpio` for the GPIOs). If `python3` is missing, `install.sh` installs it.

## Installation (on the Pi, once)
```
tar xzf limen-slave-pi.tar.gz && cd agent
./install.sh 1          # 1 = gate number (shown by the webapp in the Device tab)
```
It installs the dependencies, copies everything to `/opt/limen-slave` and creates the `limen-slave` service,
which starts by itself at every power-on. Then, in the webapp (as admin), **"Device waiting"** appears at the top of Gates:
press **Pair**. Key, cache and event queue stay in
`/var/lib/limen-slave`.

- Live log: `journalctl -u limen-slave -f`
- Change pins: `sudo nano /opt/limen-slave/config.yaml` and `sudo systemctl restart limen-slave`

## Wiring (BCM numbering, physical pins in brackets)
| Function | GPIO | Where |
|---|---|---|
| Output 1 (relay) | 17 (pin 11) | IN1 of the relay module |
| Output 2 (relay) | 18 (pin 12) | IN2 of the relay module |
| Relay module | 5 V (pin 2), GND (pin 6) | VCC and GND of the module |
| Input 1 | 27 (pin 13) | contact to GND (pin 14) |
| Input 2 | 22 (pin 15) | contact to GND (pin 14 or 9) |

- The pins work at **3.3 V**: no 5 V on the inputs. An input is a **dry** contact (button,
  magnetic sensor, dry relay) between the pin and ground; the Pi uses its internal pull-up.
- Use a **relay module with optoisolator**: the pins cannot drive the coil. The relay contact (COM/NO)
  goes in series with the door opener or automation control, which stays powered separately.
- If the relay triggers at startup or the opposite of when you command it, the module is "active low":
  set `active_high: false` for that output in the config.
- For an inductive load (lock, electromagnet) use a flyback diode across the load.

## Wiring test (before pairing)
```
sudo systemctl stop limen-slave
cd /opt/limen-slave && python3 gpio_test.py config.yaml
```
It triggers one relay at a time and shows the inputs in real time. Then: `sudo systemctl start limen-slave`.

## What you choose from the webapp
What outputs and inputs do (opens, siren, alarm arming, door sensor, button, "Other") and the
NC/NO contact are set in the gate's **Operation** popup and arrive with "Send data to devices".
