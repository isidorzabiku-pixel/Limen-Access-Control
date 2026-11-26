// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Prove del core DESFire (lib/limen/aes.*, desfire.*) sul PC, AES da OpenSSL:
//   g++ -std=c++17 -Ilib/limen test/test_desfire.cpp lib/limen/aes.cpp lib/limen/desfire.cpp lib/limen/sha256.cpp -lcrypto -o /tmp/td
//   /tmp/td                     → vettori ufficiali (RFC 4493, NXP AN10922) e CRC32
//   /tmp/td proc <lettura|scrittura|pulizia> <chiave impianto hex 64> <uid hex>
//                               → procedura su stdin/stdout, una APDU per riga in esadecimale: la usa
//                                 test/sim_desfire.py, una tessera DESFire simulata scritta a parte in Python.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include "aes.h"
#include "desfire.h"

using namespace gk;

static std::vector<uint8_t> daHex(const std::string& h) {
  std::vector<uint8_t> v;
  for (size_t i = 0; i + 1 < h.size(); i += 2) v.push_back(uint8_t(std::stoi(h.substr(i, 2), nullptr, 16)));
  return v;
}
static std::string inHex(const uint8_t* p, size_t n) {
  static const char* H = "0123456789ABCDEF";
  std::string s;
  for (size_t i = 0; i < n; i++) { s += H[p[i] >> 4]; s += H[p[i] & 15]; }
  return s;
}
static void casuale(uint8_t* p, size_t n) { for (size_t i = 0; i < n; i++) p[i] = uint8_t(rand()); }

static const char* nomeEsito(desfire::Esito e) {
  switch (e) {
    case desfire::Esito::inCorso: return "inCorso";
    case desfire::Esito::valida: return "valida";
    case desfire::Esito::nonValida: return "nonValida";
    case desfire::Esito::assente: return "assente";
    case desfire::Esito::scritta: return "scritta";
    case desfire::Esito::giaNostra: return "giaNostra";
    case desfire::Esito::pulita: return "pulita";
    case desfire::Esito::altroSistema: return "altroSistema";
    case desfire::Esito::errore: return "errore";
  }
  return "?";
}

int main(int argc, char** argv) {
  if (argc == 5 && std::string(argv[1]) == "proc") {
    srand(unsigned(time(nullptr)));
    auto k = daHex(argv[3]);
    uint8_t sito[16];
    desfire::chiaveSito(std::string(k.begin(), k.end()), sito);
    auto uid = daHex(argv[4]);
    std::string modo = argv[2];
    auto p = modo == "scrittura" ? desfire::Procedura::scrittura(sito, uid, casuale)
           : modo == "pulizia" ? desfire::Procedura::pulizia(sito, uid, casuale)
                               : desfire::Procedura::lettura(sito, uid, casuale);
    std::string riga;
    while (p.esito() == desfire::Esito::inCorso) {
      std::cout << inHex(p.apdu().data(), p.apdu().size()) << std::endl;
      if (!std::getline(std::cin, riga)) return 2;
      p.risposta(daHex(riga));
    }
    std::cout << "ESITO " << nomeEsito(p.esito()) << " " << p.motivo() << std::endl;
    return 0;
  }

  // RFC 4493: CMAC con la chiave d'esempio, messaggio vuoto e di 16 / 40 / 64 byte
  auto k = daHex("2b7e151628aed2a6abf7158809cf4f3c");
  auto m = daHex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e5130c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710");
  uint8_t out[16];
  aes::cmac(k.data(), m.data(), 0, out);  assert(inHex(out, 16) == "BB1D6929E95937287FA37D129B756746");
  aes::cmac(k.data(), m.data(), 16, out); assert(inHex(out, 16) == "070A16B46B4D4144F79BDD9DD04A287C");
  aes::cmac(k.data(), m.data(), 40, out); assert(inHex(out, 16) == "DFA66747DE9AE63030CA32611497C827");
  aes::cmac(k.data(), m.data(), 64, out); assert(inHex(out, 16) == "51F0BEBF7E3B9D92FC49741779363CFE");

  // NXP AN10922 §2.2.1, AES-128: K = 00112233..EEFF, UID 04782E21801D80, AID 3042F5, sistema "NXP Abu"
  auto kn = daHex("00112233445566778899AABBCCDDEEFF");
  auto in = daHex("04782E21801D803042F54E585020416275");
  desfire::diversificaAn10922(kn.data(), in.data(), in.size(), out);
  assert(inHex(out, 16) == "A8DD63A3B89D54B37CA802473FDA9175");

  // CRC32 DESFire = CRC-32 standard senza inversione finale ("123456789" → CBF43926 invertito)
  const char* t = "123456789";
  assert(desfire::crc32(reinterpret_cast<const uint8_t*>(t), 9) == ~0xCBF43926u);

  // CBC: cifra e decifra tornano, IV in catena
  uint8_t iv[16] = {0}, iv2[16] = {0}, c[64], d[64];
  aes::cbcCifra(k.data(), iv, m.data(), 64, c);
  aes::cbcDecifra(k.data(), iv2, c, 64, d);
  assert(memcmp(d, m.data(), 64) == 0 && memcmp(iv, iv2, 16) == 0);

  // Chiavi della tessera: diverse per numero e per UID, sempre uguali a parita' di ingresso
  uint8_t sito[16], a[16], b[16], c2[16];
  desfire::chiaveSito(std::string(32, '\x11'), sito);
  auto u1 = daHex("04112233445566"), u2 = daHex("04112233445567");
  desfire::chiaveTessera(sito, u1.data(), 7, 0, a);
  desfire::chiaveTessera(sito, u1.data(), 7, 1, b);
  desfire::chiaveTessera(sito, u2.data(), 7, 1, c2);
  assert(memcmp(a, b, 16) != 0 && memcmp(b, c2, 16) != 0);
  desfire::chiaveTessera(sito, u1.data(), 7, 1, c2);
  assert(memcmp(b, c2, 16) == 0);
  assert(desfire::codice("04112233445566") == "DF-04112233445566");

  std::puts("desfire: ok");
  return 0;
}
