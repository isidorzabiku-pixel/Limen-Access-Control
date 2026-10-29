# Third-party components

Limen's own code is © 2026 Isidor Zabiku, licensed under the AGPL-3.0 (see `LICENSE`). It uses the components below, all under permissive
licenses that allow use, modification and redistribution with any license chosen for Limen, provided their notices are kept.
Licenses listed from each project's published metadata; verify again before each release.

## Master (`backend/`)
| Component | Version | License |
|---|---|---|
| FastAPI | 0.115.0 | MIT |
| Starlette (via FastAPI) | | BSD-3-Clause |
| Pydantic (via FastAPI) | | MIT |
| Uvicorn | 0.30.6 | BSD-3-Clause |
| SQLAlchemy | 2.0.35 | MIT |
| Jinja2 / MarkupSafe | 3.1.4 | BSD-3-Clause |
| python-multipart | 0.0.9 | Apache-2.0 |
| bcrypt | 4.2.0 | Apache-2.0 |
| itsdangerous | 2.2.0 | BSD-3-Clause |
| python-dotenv | 1.0.1 | BSD-3-Clause |
| htmx (`backend/static/htmx.min.js`) | 2.0.3 | 0BSD |
| Inter font (`backend/static/fonts/`) | | SIL OFL 1.1 (`OFL-Inter.txt`) |
| JetBrains Mono font (`backend/static/fonts/`) | | SIL OFL 1.1 (`OFL-JetBrainsMono.txt`) |

Development only (not shipped): Playwright (Apache-2.0), used to render the API PDF.

## ESP32 firmware (`firmware/esp32/`)
| Component | License |
|---|---|
| ArduinoJson 7 | MIT |
| Arduino-ESP32 core (PlatformIO `espressif32`) | LGPL-2.1 |
| ESP-IDF (inside the core) | Apache-2.0 |

The SHA-256 / HMAC code in `lib/limen/` is written for this project from the FIPS 180-4 specification. The Arduino-ESP32
core is LGPL: if you distribute **compiled firmware binaries**, include the core's license text and let recipients relink
(distributing the source of this repository is enough for the source form).

## Python test agent (`agent/`, not maintained)
pyserial (BSD-3-Clause), PyYAML (MIT), gpiozero (BSD-3-Clause).

## Container image
Built on the official `python:3.12-slim` image (Python Software Foundation License; Debian packages under their own licenses).
