// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Tessere MIFARE DESFire (EV1/EV2/EV3, comandi EV1 compatibili) con AES-128.
//
// Schema (see docs/ARCHITECTURE.md):
//  - chiave DESFire del sito = HMAC-SHA256(chiave d'impianto, "LIMEN-DESFIRE-v1")[0..15]
//  - sulla tessera un'applicazione Limen (AID 4C 4D 01) con 2 chiavi AES: 0 = gestione, 1 = lettura.
//    Ogni chiave e' diversa per ogni tessera: NXP AN10922 (CMAC) con ingresso UID(7) | AID(3) | n.chiave | "LIMEN".
//  - lettura al varco: seleziona l'applicazione e si autentica (AES, mutua) con la chiave 1 della tessera.
//    Riesce solo una tessera scritta da questo impianto con quell'UID: l'UID e' quindi autentico e il codice
//    della credenziale e' "DF-" + UID. Nessun file sulla tessera.
//  - scrittura (tessera vergine): crea l'applicazione, si autentica con la chiave 0 di fabbrica (AES tutta a zero),
//    cambia la chiave 1, si riautentica, cambia la chiave 0, verifica con la chiave 1.
// Comandi nativi DESFire dentro APDU ISO 7816 (CLA 90). Le funzioni qui non parlano col lettore: producono la
// prossima APDU e leggono la risposta, cosi' si provano sul PC contro una tessera simulata.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace gk {
namespace desfire {

constexpr uint8_t AID[3] = {0x4C, 0x4D, 0x01};       // byte come viaggiano (LSB prima)
constexpr uint8_t CHIAVE_GESTIONE = 0, CHIAVE_LETTURA = 1;
constexpr const char* PREFISSO_CODICE = "DF-";

// Chiave DESFire del sito dalla chiave d'impianto (32 byte).
void chiaveSito(const std::string& chiaveImpianto, uint8_t out[16]);
// NXP AN10922, AES-128: CMAC(K, 0x01 | ingresso) per ingressi di 16..31 byte.
void diversificaAn10922(const uint8_t k[16], const uint8_t* ingresso, size_t n, uint8_t out[16]);
// Chiave n della tessera con quell'UID (7 byte).
void chiaveTessera(const uint8_t sito[16], const uint8_t* uid, size_t nUid, uint8_t numero, uint8_t out[16]);
// CRC32 DESFire: polinomio 0xEDB88320, partenza 0xFFFFFFFF, senza inversione finale (little-endian).
uint32_t crc32(const uint8_t* p, size_t n);
std::string codice(const std::string& uidHex);         // "DF-" + UID in esadecimale maiuscolo

// Autenticazione AES (comando AA, 3 passaggi). Generatore di casuali dato da fuori (esp_fill_random sull'ESP32).
class Autenticazione {
 public:
  using Casuale = void (*)(uint8_t* p, size_t n);
  Autenticazione(const uint8_t chiave[16], uint8_t numero, Casuale casuale);
  std::vector<uint8_t> primo() const;                  // 90 AA 00 00 01 n 00
  // Risposta al primo (ek(RndB) + 91 AF) → secondo comando; vuoto se la risposta non va.
  std::vector<uint8_t> secondo(const std::vector<uint8_t>& r);
  // Risposta al secondo (ek(RndA') + 91 00): true se la tessera ha dimostrato di avere la chiave.
  bool finale(const std::vector<uint8_t>& r);
  const uint8_t* chiaveSessione() const { return sessione_; }
  uint8_t stato() const { return stato_; }             // ultimo byte di stato della tessera (91 xx)

 private:
  uint8_t chiave_[16], rndA_[16], rndB_[16], iv_[16], sessione_[16];
  uint8_t numero_, stato_ = 0;
  Casuale casuale_;
};

// ChangeKey (C4) cifrata con la chiave di sessione, IV a zero (subito dopo l'autenticazione).
// stessaChiave = si cambia la chiave con cui ci si e' autenticati (allora non serve la vecchia).
std::vector<uint8_t> cambiaChiave(const uint8_t sessione[16], uint8_t numero, const uint8_t nuova[16],
                                  const uint8_t vecchia[16], uint8_t versione, bool stessaChiave);

std::vector<uint8_t> selezionaApplicazione(const uint8_t aid[3]);
std::vector<uint8_t> creaApplicazione();               // AID Limen, impostazioni 0x0B, 2 chiavi AES
std::vector<uint8_t> cancellaApplicazione();           // DA + AID Limen (dopo l'autenticazione con la chiave di gestione)
uint8_t statoRisposta(const std::vector<uint8_t>& r);  // xx di «91 xx», 0xFF se non e' una risposta DESFire
constexpr uint8_t OK = 0x00, ALTRO_FRAME = 0xAF, APP_ASSENTE = 0xA0, ERRORE_AUTENTICAZIONE = 0xAE,
                  PERMESSO_NEGATO = 0x9D, DUPLICATO = 0xDE;

// Procedura completa, una APDU alla volta: apdu() da mandare, risposta() con quello che torna.
enum class Esito {
  inCorso,
  valida,          // lettura: tessera di questo impianto
  nonValida,       // lettura: applicazione Limen presente ma chiave sbagliata (copia, altro impianto)
  assente,         // lettura: nessuna applicazione Limen (tessera vergine o di un altro sistema)
  scritta,         // scrittura riuscita
  giaNostra,       // scrittura: tessera gia' di questo impianto (non riscritta)
  pulita,          // pulizia: applicazione Limen cancellata dalla tessera
  altroSistema,    // scrittura: tessera bloccata da un altro sistema o con un'applicazione Limen altrui
  errore,          // risposta inattesa (tessera tolta, memoria piena...): dettaglio in motivo()
};

class Procedura {
 public:
  static Procedura lettura(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c);
  static Procedura scrittura(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c);
  // Pulizia: si autentica con la chiave di gestione della tessera e cancella la sola applicazione Limen (DA).
  // Esiti: pulita, assente (nessuna applicazione Limen), altroSistema (chiavi di un altro impianto), errore.
  static Procedura pulizia(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c);
  const std::vector<uint8_t>& apdu() const { return apdu_; }
  void risposta(const std::vector<uint8_t>& r);
  Esito esito() const { return esito_; }
  const std::string& motivo() const { return motivo_; }

 private:
  enum Passo {
    selApp, authLettura1, authLettura2,                         // lettura (e verifica finale)
    selPicc, crea, selNuova, authZero1, authZero2, cambiaUno,  // scrittura
    authZeroBis1, authZeroBis2, cambiaZero, authVerifica1, authVerifica2,
    authNostra1, authNostra2, authMeta1, authMeta2,             // scrittura su tessera con app gia' presente
    authControllo1, authControllo2,                             // chiave 0 ancora di fabbrica? (scrittura interrotta)
    authPul1, authPul2, authPulZero1, authPulZero2, cancella,   // pulizia
  };
  Procedura(const uint8_t sito[16], const std::vector<uint8_t>& uid, Autenticazione::Casuale c, bool scrivi, bool pulisci = false);
  void vai(Passo p);
  void fine(Esito e, const std::string& motivo = "");

  bool scrivi_, pulisci_;
  Passo passo_ = selApp;
  Esito esito_ = Esito::inCorso;
  std::string motivo_;
  std::vector<uint8_t> apdu_;
  uint8_t k0_[16], k1_[16], zero_[16] = {0};
  Autenticazione::Casuale casuale_;
  Autenticazione auth_;
};

}  // namespace desfire
}  // namespace gk
