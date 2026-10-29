// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include "Arduino.h"
struct Preferences {
  bool begin(const char*, bool = false) { return true; } void end() {} void clear() {}
  bool isKey(const char*) { return false; }
  uint32_t getUInt(const char*, uint32_t d = 0) { return d; } int getInt(const char*, int d = 0) { return d; } bool getBool(const char*, bool d = false) { return d; }
  String getString(const char*, const String& d = String()) { return d; }
  size_t putUInt(const char*, uint32_t) { return 4; } size_t putInt(const char*, int) { return 4; } size_t putBool(const char*, bool) { return 1; } size_t putString(const char*, const String&) { return 1; }
};
