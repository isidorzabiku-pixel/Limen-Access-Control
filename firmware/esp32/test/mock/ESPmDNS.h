// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include "Arduino.h"
struct MDNST { bool begin(const char*) { return true; } void addService(const char*, const char*, int) {} } inline MDNS;
