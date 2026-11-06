# Limen UART reader (Flipper Zero app)

Bench tool: the Flipper reads a 125 kHz tag and sends `PROTOCOL:HEX` (e.g. `EM4100:0A1B2C3D4E`) to a Limen ESP32 over
UART, then shows the device's answer (`OK …`, `NO …`, `IGNORED`, `ERR …`). Test only, not a product reader.

## Install

1. Update the Flipper to the latest official firmware with qFlipper, then close qFlipper.
2. `pip install ufbt`
3. In this folder, with the Flipper connected over USB: `ufbt launch` (downloads the SDK, builds, installs and starts
   the app). Afterwards it is under **Apps → GPIO → Limen UART reader**.

## Wiring

| Flipper | ESP32 |
|---|---|
| pin 13 (TX) | GPIO16 (RX) |
| pin 14 (RX) | GPIO17 (TX) |
| pin 8 or 18 (GND) | GND |

115200 8N1, 3.3 V logic on both sides. The app turns off the Flipper's expansion-module service while it runs.
