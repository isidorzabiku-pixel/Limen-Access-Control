// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include <functional>
#include "Arduino.h"
enum HTTPMethod { HTTP_GET, HTTP_POST };
enum HTTPAuthMethod { BASIC_AUTH, DIGEST_AUTH };
class WebServer {
 public:
  WebServer(int) {}
  typedef std::function<void(void)> THandlerFunction;
  void on(const char*, HTTPMethod, THandlerFunction) {}
  void onNotFound(THandlerFunction) {}
  void begin() {} void handleClient() {}
  String arg(const char*) { return String(); }
  bool hasArg(const char*) { return false; }
  bool authenticate(const char*, const char*) { return true; }
  void requestAuthentication(HTTPAuthMethod = BASIC_AUTH, const char* = nullptr, const String& = String()) {}
  void send(int, const char* = nullptr, const String& = String()) {}
  void send_P(int, const char*, const char*) {}
  void sendHeader(const String&, const String&) {}
};
