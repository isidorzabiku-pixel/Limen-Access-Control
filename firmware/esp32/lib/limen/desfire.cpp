// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "desfire.h"
#include <cstdio>
#include <cstring>
#include "aes.h"
#include "sha256.h"

namespace gk {
namespace desfire {

void chiaveSito(const std::string& chiaveImpianto, uint8_t out[16]) {
  static const char ETICHETTA[] = "LIMEN-DESFIRE-v1";
  uint8_t h[32];
  hmacSha256(chiaveImpianto, reinterpret_cast<const uint8_t*>(ETICHETTA), sizeof(ETICHETTA) - 1, h);
  memcpy(out, h, 16);
}

void diversificaAn10922(const uint8_t k[16], const uint8_t* ingresso, size_t n, uint8_t out[16]) {
  // AN10922 §2.2: M = 01 | ingresso, riempito a 32 byte; e' esattamente un CMAC quando M sta fra 17 e 32 byte.
  uint8_t m[32];
  if (n < 16 || n > 31) { memset(out, 0, 16); return; }
  m[0] = 0x01;
  memcpy(m + 1, ingresso, n);
  aes::cmac(k, m, n + 1, out);
}

void chiaveTessera(const uint8_t sito[16], const uint8_t* uid, size_t nUid, uint8_t numero, uint8_t out[16]) {
  uint8_t d[31];
  size_t n = 0;
  for (size_t i = 0; i < nUid && n < 10; i++) d[n++] = uid[i];
  for (uint8_t b : AID) d[n++] = b;
  d[n++] = numero;
  for (const char* p = "LIMEN"; *p; p++) d[n++] = uint8_t(*p);
  while (n < 16) d[n++] = 0x00;                            // UID corti (4 byte): ingresso comunque di 16 byte
  diversificaAn10922(sito, d, n, out);
}

uint32_t crc32(const uint8_t* p, size_t n) {
  uint32_t c = 0xFFFFFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= p[i];
    for (int b = 0; b < 8; b++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
  }
  return c;
}

std::string codice(const std::string& uidHex) { return std::string(PREFISSO_CODICE) + uidHex; }

static std::vector<uint8_t> nativo(uint8_t cmd, const std::vector<uint8_t>& dati) {
  std::vector<uint8_t> a = {0x90, cmd, 0x00, 0x00};
  if (!dati.empty()) { a.push_back(uint8_t(dati.size())); a.insert(a.end(), dati.begin(), dati.end()); }
  a.push_back(0x00);
  return a;
}

uint8_t statoRisposta(const std::vector<uint8_t>& r) {
  return r.size() >= 2 && r[r.size() - 2] == 0x91 ? r.back() : 0xFF;
}

std::vector<uint8_t> selezionaApplicazione(const uint8_t aid[3]) { return nativo(0x5A, {aid[0], aid[1], aid[2]}); }

// Impostazioni 0x0B: chiave di gestione cambiabile, elenco file libero, crea/cancella file solo con la chiave di
// gestione, configurazione cambiabile, per cambiare le chiavi serve la chiave di gestione. 0x82 = AES, 2 chiavi.
std::vector<uint8_t> creaApplicazione() { return nativo(0xCA, {AID[0], AID[1], AID[2], 0x0B, 0x82}); }

std::vector<uint8_t> cancellaApplicazione() { return nativo(0xDA, {AID[0], AID[1], AID[2]}); }

static void ruota(const uint8_t in[16], uint8_t out[16]) {   // rotazione a sinistra di un byte
  for (int i = 0; i < 16; i++) out[i] = in[(i + 1) % 16];
}

Autenticazione::Autenticazione(const uint8_t chiave[16], uint8_t numero, Casuale casuale)
    : numero_(numero), casuale_(casuale) {
  memcpy(chiave_, chiave, 16);
  memset(iv_, 0, 16);
  memset(sessione_, 0, 16);
}

std::vector<uint8_t> Autenticazione::primo() const { return nativo(0xAA, {numero_}); }

std::vector<uint8_t> Autenticazione::secondo(const std::vector<uint8_t>& r) {
  stato_ = statoRisposta(r);
  if (r.size() != 18 || stato_ != ALTRO_FRAME) return {};
  memset(iv_, 0, 16);
  aes::cbcDecifra(chiave_, iv_, r.data(), 16, rndB_);        // iv_ = ek(RndB)
  casuale_(rndA_, 16);
  uint8_t chiaro[32], cifrato[32];
  memcpy(chiaro, rndA_, 16);
  ruota(rndB_, chiaro + 16);
  aes::cbcCifra(chiave_, iv_, chiaro, 32, cifrato);          // iv_ = ultimo blocco mandato
  return nativo(0xAF, std::vector<uint8_t>(cifrato, cifrato + 32));
}

bool Autenticazione::finale(const std::vector<uint8_t>& r) {
  stato_ = statoRisposta(r);
  if (r.size() != 18 || stato_ != OK) return false;
  uint8_t ricevuta[16], attesa[16];
  aes::cbcDecifra(chiave_, iv_, r.data(), 16, ricevuta);
  ruota(rndA_, attesa);
  uint8_t diff = 0;
  for (int i = 0; i < 16; i++) diff |= uint8_t(ricevuta[i] ^ attesa[i]);
  if (diff) return false;
  memcpy(sessione_, rndA_, 4);
  memcpy(sessione_ + 4, rndB_, 4);
  memcpy(sessione_ + 8, rndA_ + 12, 4);
  memcpy(sessione_ + 12, rndB_ + 12, 4);
  return true;
}

std::vector<uint8_t> cambiaChiave(const uint8_t sessione[16], uint8_t numero, const uint8_t nuova[16],
                                  const uint8_t vecchia[16], uint8_t versione, bool stessaChiave) {
  // Dati: (nuova, oppure nuova XOR vecchia) | versione | CRC32(C4 | numero | quei dati) [| CRC32(nuova)],
  // riempiti di zeri a 32 byte e cifrati in CBC con la chiave di sessione e IV a zero.
  uint8_t d[32] = {0}, base[2 + 17];
  size_t n = 0;
  for (int i = 0; i < 16; i++) d[n++] = stessaChiave ? nuova[i] : uint8_t(nuova[i] ^ vecchia[i]);
  d[n++] = versione;
  base[0] = 0xC4; base[1] = numero;
  memcpy(base + 2, d, 17);
  uint32_t c = crc32(base, sizeof(base));
  for (int i = 0; i < 4; i++) d[n++] = uint8_t(c >> (8 * i));
  if (!stessaChiave) {
    uint32_t cn = crc32(nuova, 16);
    for (int i = 0; i < 4; i++) d[n++] = uint8_t(cn >> (8 * i));
  }
  uint8_t iv[16] = {0}, cifrato[32];
  aes::cbcCifra(sessione, iv, d, 32, cifrato);
  std::vector<uint8_t> dati = {numero};
  dati.insert(dati.end(), cifrato, cifrato + 32);
  return nativo(0xC4, dati);
}

// ------------------------------------------------------------------ procedura

Procedura::Procedura(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c, bool scrivi, bool pulisci)
    : scrivi_(scrivi), pulisci_(pulisci), casuale_(c), auth_(zero_, 0, c) {
  chiaveTessera(sito, uid.data(), uid.size(), CHIAVE_GESTIONE, k0_);
  chiaveTessera(sito, uid.data(), uid.size(), CHIAVE_LETTURA, k1_);
  vai(selApp);
}

Procedura Procedura::lettura(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c) {
  return Procedura(sito, uid, c, false);
}
Procedura Procedura::scrittura(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c) {
  return Procedura(sito, uid, c, true);
}

Procedura Procedura::pulizia(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c) {
  return Procedura(sito, uid, c, false, true);
}

void Procedura::fine(Esito e, const std::string& motivo) {
  esito_ = e;
  motivo_ = motivo;
  apdu_.clear();
}

void Procedura::vai(Passo p) {
  static const uint8_t PICC[3] = {0, 0, 0};
  passo_ = p;
  switch (p) {
    case selApp: case selNuova: apdu_ = selezionaApplicazione(AID); break;
    case selPicc: apdu_ = selezionaApplicazione(PICC); break;
    case crea: apdu_ = creaApplicazione(); break;
    case authLettura1: case authVerifica1: case authNostra1:
      auth_ = Autenticazione(k1_, CHIAVE_LETTURA, casuale_); apdu_ = auth_.primo(); break;
    case authPul1:
      auth_ = Autenticazione(k0_, CHIAVE_GESTIONE, casuale_); apdu_ = auth_.primo(); break;
    case cancella: apdu_ = cancellaApplicazione(); break;
    case authZero1: case authZeroBis1: case authMeta1: case authControllo1: case authPulZero1:
      auth_ = Autenticazione(zero_, CHIAVE_GESTIONE, casuale_); apdu_ = auth_.primo(); break;
    case cambiaUno: apdu_ = cambiaChiave(auth_.chiaveSessione(), CHIAVE_LETTURA, k1_, zero_, 1, false); break;
    case cambiaZero: apdu_ = cambiaChiave(auth_.chiaveSessione(), CHIAVE_GESTIONE, k0_, zero_, 1, true); break;
    default: break;                                     // i secondi passi delle autenticazioni li prepara risposta()
  }
}

void Procedura::risposta(const std::vector<uint8_t>& r) {
  if (esito_ != Esito::inCorso) return;
  uint8_t s = statoRisposta(r);
  char hex[8];
  snprintf(hex, sizeof(hex), "%02X", s);
  switch (passo_) {
    case selApp:
      if (s == OK) { vai(pulisci_ ? authPul1 : scrivi_ ? authNostra1 : authLettura1); return; }
      if (s == APP_ASSENTE) { if (scrivi_) vai(selPicc); else fine(Esito::assente); return; }
      fine(Esito::errore, std::string("select app: ") + hex); return;

    // Autenticazioni: primo passo → secondo comando; secondo passo → esito
    case authLettura1: case authVerifica1: case authNostra1: case authZero1: case authZeroBis1: case authMeta1:
    case authControllo1: case authPul1: case authPulZero1: {
      std::vector<uint8_t> a = auth_.secondo(r);
      if (a.empty()) {
        if (passo_ == authPul1) { vai(authPulZero1); return; }
        if (passo_ == authPulZero1) { fine(Esito::altroSistema, "Limen application with keys of another site"); return; }
        if (passo_ == authLettura1) { fine(Esito::nonValida, std::string("auth: ") + hex); return; }
        if (passo_ == authNostra1) { vai(authMeta1); return; }
        if (passo_ == authMeta1) { fine(Esito::altroSistema, "Limen application with keys of another site"); return; }
        if (passo_ == authControllo1) { fine(Esito::giaNostra); return; }    // chiave 0 gia' cambiata: tutto a posto
        fine(Esito::errore, std::string("auth: ") + hex); return;
      }
      apdu_ = a;
      passo_ = Passo(passo_ + 1);                         // ogni passo 1 e' seguito dal suo passo 2
      return;
    }
    case authLettura2:
      fine(auth_.finale(r) ? Esito::valida : Esito::nonValida); return;
    case authNostra2:
      if (auth_.finale(r)) { vai(authControllo1); return; }  // chiave 1 giusta: controlla che la 0 non sia di fabbrica
      vai(authMeta1); return;                               // forse una scrittura interrotta (chiave 0 ancora di fabbrica)
    case authMeta2:
      if (auth_.finale(r)) { vai(cambiaUno); return; }
      fine(Esito::altroSistema, "Limen application with keys of another site"); return;
    case authZero2:
      if (auth_.finale(r)) { vai(cambiaUno); return; }
      fine(Esito::errore, "factory key refused"); return;
    case authControllo2:
      if (auth_.finale(r)) { vai(cambiaZero); return; }      // scrittura interrotta: si finisce
      fine(Esito::giaNostra); return;
    case authPul2:
      if (auth_.finale(r)) { vai(cancella); return; }
      vai(authPulZero1); return;                             // scrittura interrotta: chiave di gestione ancora di fabbrica
    case authPulZero2:
      if (auth_.finale(r)) { vai(cancella); return; }
      fine(Esito::altroSistema, "Limen application with keys of another site"); return;
    case cancella:
      if (s == OK) { fine(Esito::pulita); return; }
      fine(Esito::errore, std::string("delete application: ") + hex); return;
    case authZeroBis2:
      if (auth_.finale(r)) { vai(cambiaZero); return; }
      fine(Esito::errore, "factory key refused"); return;
    case authVerifica2:
      if (auth_.finale(r)) fine(Esito::scritta); else fine(Esito::errore, "check after writing failed");
      return;

    case selPicc:
      if (s == OK) { vai(crea); return; }
      fine(Esito::errore, std::string("select PICC: ") + hex); return;
    case crea:
      if (s == OK || s == DUPLICATO) { vai(selNuova); return; }
      if (s == ERRORE_AUTENTICAZIONE || s == PERMESSO_NEGATO) { fine(Esito::altroSistema, "card locked by another system"); return; }
      if (s == 0x0E) { fine(Esito::errore, "card memory full"); return; }
      fine(Esito::errore, std::string("create application: ") + hex); return;
    case selNuova:
      if (s == OK) { vai(authZero1); return; }
      fine(Esito::errore, std::string("select app: ") + hex); return;
    case cambiaUno:
      // Se la scrittura era stata interrotta dopo questo passo la chiave 1 e' gia' quella giusta e la tessera rifiuta
      // il cambio (il CRC della chiave nuova non torna): si prosegue, la verifica finale dira' se va.
      vai(authZeroBis1); return;
    case cambiaZero:
      if (s == OK) { vai(authVerifica1); return; }
      fine(Esito::errore, std::string("change key 0: ") + hex); return;
  }
}

}  // namespace desfire
}  // namespace gk
