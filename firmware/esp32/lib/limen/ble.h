// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Credenziale BLE del telefono: titolo + segreto derivato dalla chiave dei telefoni dell'impianto.
// Stessi calcoli di backend/services/ble_abbinamento.py.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>

namespace gk {

// Telefoni (BLE), schema del 12/11: il master assegna a ogni telefono un titolo (il codice che sta in cache,
// 23 caratteri base32) e un segreto S = HMAC-SHA256(chiave dei telefoni, "LIMEN-PHONE" | titolo).
// La chiave dei telefoni e' una per impianto: il master la manda agli slave cifrata dentro la cache.
constexpr size_t LUNGHEZZA_TITOLO_BLE = 23;
constexpr size_t LUNGHEZZA_NONCE_BLE = 16;
constexpr size_t LUNGHEZZA_MAC_BLE = 32;        // HMAC-SHA256
constexpr size_t LUNGHEZZA_SEGRETO_BLE = 32;
constexpr size_t LUNGHEZZA_ETICHETTA_BLE = 16;  // autenticazione della credenziale cifrata
constexpr int MAX_ERRORI_ABBINAMENTO_BLE = 5;   // prove sbagliate prima di chiudere la finestra

// S = HMAC(chiave telefoni, "LIMEN-PHONE" | titolo)
void segretoTelefono(const std::string& chiaveTelefoni, const std::string& titolo, uint8_t out[32]);
// HMAC(chiave, prefisso | a | b) con a e b di 16 byte (b puo' essere nullptr)
void macBle(const std::string& chiave, const char* prefisso, const uint8_t* a, const uint8_t* b, uint8_t out[32]);
// Credenziale per il telefono, cifrata con la chiave dell'invito K: (titolo | S) XOR flusso, piu' etichetta (16 byte).
// flusso = HMAC(K, "LIMEN-CRED" | sfida | nonce | i), i = 0,1; etichetta = HMAC(K, "LIMEN-CTAG" | sfida | nonce | cifrato)[:16].
std::string cifraCredenziale(const std::string& k, const uint8_t* sfida, const uint8_t* nonceTel,
                             const std::string& titolo, const uint8_t segreto[32]);
bool titoloBleValido(const std::string& t);     // 23 caratteri A-Z 2-7
std::string esadecimale(const uint8_t* p, size_t n);

}  // namespace gk
