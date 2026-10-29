#!/bin/sh
# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
# Compila il core su PC con il compilatore di sistema (serve ArduinoJson 7: ARDUINOJSON_DIR o /tmp/aj/src).
set -e
AJ="${ARDUINOJSON_DIR:-/tmp/aj/src}"
cd "$(dirname "$0")"
g++ -std=c++17 -O1 -Wall -Wextra -DARDUINOJSON_USE_LONG_LONG=1 -I"$AJ" -I../lib/limen \
  host_main.cpp ../lib/limen/*.cpp -o "${1:-/tmp/host_slave}"
