// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "ble.h"
#include "sha256.h"

namespace gk {

std::string esadecimale(const uint8_t* p, size_t n) {
  static const char* H = "0123456789abcdef";
  std::string s; s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) { s += H[p[i] >> 4]; s += H[p[i] & 15]; }
  return s;
}

bool titoloBleValido(const std::string& t) {
  if (t.size() != LUNGHEZZA_TITOLO_BLE) return false;
  for (char ch : t) if (!((ch >= 'A' && ch <= 'Z') || (ch >= '2' && ch <= '7'))) return false;
  return true;
}

void segretoTelefono(const std::string& chiaveTelefoni, const std::string& titolo, uint8_t out[32]) {
  std::string msg = "LIMEN-PHONE" + titolo;
  hmacSha256(chiaveTelefoni, reinterpret_cast<const uint8_t*>(msg.data()), msg.size(), out);
}

void macBle(const std::string& chiave, const char* prefisso, const uint8_t* a, const uint8_t* b, uint8_t out[32]) {
  std::string msg = prefisso;
  msg.append(reinterpret_cast<const char*>(a), LUNGHEZZA_NONCE_BLE);
  if (b) msg.append(reinterpret_cast<const char*>(b), LUNGHEZZA_NONCE_BLE);
  hmacSha256(chiave, reinterpret_cast<const uint8_t*>(msg.data()), msg.size(), out);
}

std::string cifraCredenziale(const std::string& k, const uint8_t* sfida, const uint8_t* nonceTel,
                             const std::string& titolo, const uint8_t segreto[32]) {
  std::string chiaro = titolo;
  chiaro.append(reinterpret_cast<const char*>(segreto), LUNGHEZZA_SEGRETO_BLE);
  std::string base = "LIMEN-CRED";
  base.append(reinterpret_cast<const char*>(sfida), LUNGHEZZA_NONCE_BLE);
  base.append(reinterpret_cast<const char*>(nonceTel), LUNGHEZZA_NONCE_BLE);
  std::string cifrato(chiaro.size(), '\0');
  for (size_t blocco = 0; blocco * 32 < chiaro.size(); blocco++) {
    std::string m = base; m += char(blocco);
    uint8_t flusso[32];
    hmacSha256(k, reinterpret_cast<const uint8_t*>(m.data()), m.size(), flusso);
    for (size_t i = 0; i < 32 && blocco * 32 + i < chiaro.size(); i++)
      cifrato[blocco * 32 + i] = char(uint8_t(chiaro[blocco * 32 + i]) ^ flusso[i]);
  }
  std::string t = "LIMEN-CTAG";
  t.append(reinterpret_cast<const char*>(sfida), LUNGHEZZA_NONCE_BLE);
  t.append(reinterpret_cast<const char*>(nonceTel), LUNGHEZZA_NONCE_BLE);
  t += cifrato;
  uint8_t etichetta[32];
  hmacSha256(k, reinterpret_cast<const uint8_t*>(t.data()), t.size(), etichetta);
  return esadecimale(reinterpret_cast<const uint8_t*>(cifrato.data()), cifrato.size()) + esadecimale(etichetta, LUNGHEZZA_ETICHETTA_BLE);
}

}  // namespace gk
