// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Tutto cio' che il core chiede alla piattaforma. Sull'ESP32 lo implementa src/main.cpp,
// sul PC (test) lo implementa test/host_main.cpp: la logica del varco e' identica.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>

namespace gk {

struct Hal {
  virtual ~Hal() {}
  virtual uint64_t millis() = 0;                         // tempo monotono in millisecondi
  virtual int64_t unixTime() = 0;                        // ora di sistema (secondi); sotto 1,5 miliardi = non impostata
  virtual void unicast(const std::string& ip, const uint8_t* dati, size_t n) = 0;   // UDP all'indirizzo ip, porta del master (57990)
  virtual void setRelay(int indice, bool eccitato) = 0;
  virtual void setLed(int quale, bool acceso) = 0;       // 0 = LED verde (accesso concesso), 1 = LED rosso (negato)
  virtual bool readInput(int indice, bool& alto) = 0;    // false = ingresso non presente; alto = livello del pin
  virtual bool load(const char* nome, std::string& out) = 0;
  virtual bool save(const char* nome, const std::string& dati) = 0;
  virtual void log(int livello, const std::string& msg) = 0;   // 0 info, 1 avviso, 2 errore
  virtual std::string randomHex(int byte) = 0;
};

}  // namespace gk
