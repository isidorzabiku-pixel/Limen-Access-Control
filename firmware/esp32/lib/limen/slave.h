// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Logica dello slave Limen: stessa dell'agent Python (agent/services/*), in un unico oggetto
// cooperativo (nessun thread): il programma chiama onDatagram() per ogni pacchetto ricevuto e
// loop() spesso (ogni 10-20 ms). Tutto l'hardware passa dall'interfaccia Hal.
//
// Protocollo (UDP, porta 57990, vedi backend/services/udp_agent.py):
//   Solo unicast: l'IP dello slave e' scritto sul master, che lo tocca per primo con HI; lo slave non scrive mai per primo.
//   master -> slave: HI tocco (firmato), PA abbinamento (porta la chiave), cache (JSON + firma), AP apri, UC comando uscita, AE conferma
//   slave -> master (all'IP imparato dal tocco): PR presentazione, HB battito, EV/IN/SU/SP eventi con conferma, EA, CA, RC
#pragma once
#include <deque>
#include <map>
#include <string>
#include <vector>
#include "cache.h"
#include "hal.h"

namespace gk {

constexpr uint16_t PORTA_UDP = 57990;
constexpr int FINESTRA_REPLAY_SEC = 30;
constexpr int MAX_EVENTI_IN_CODA = 300;
constexpr int LED_NEGATO_SEC = 2;          // il LED rosso resta acceso cosi' a lungo dopo un badge negato

struct EsitoBadge {
  bool ignorato = false;            // lettore non usato nel funzionamento del varco
  bool concesso = false;
  std::string esito;
};

class Slave {
 public:
  Slave(Hal& hal, uint32_t varcoId, int intervalloBattitoSec = 3);

  void begin();                                           // carica chiave, cache, coda e stato uscite dal flash
  void onDatagram(const uint8_t* dati, size_t n, const std::string& ip);   // un pacchetto UDP ricevuto, ip = mittente
  void loop();                                            // da chiamare spesso

  // Un badge (da un lettore vero o dalla console di prova): decide in locale, apre, riporta l'evento.
  EsitoBadge badge(const std::string& codice, int lettore);

  // Stato per la pagina web e i test.
  std::string statoJson();
  bool abbinato() const { return !chiave_.empty(); }
  void dimenticaAbbinamento();
  const std::string& masterIp() const { return masterIp_; }
  uint32_t varcoId() const { return id_; }
  bool uscitaEccitata(int i) const;
  bool cacheRicevuta() const { return cacheRicevuta_; }
  bool cacheFresca() const { return cacheFresca_; }
  size_t eventiInCoda() const { return coda_.size(); }
  const Cache& cache() const { return cache_; }

  // Per i test: azioni dirette.
  bool comandoUscita(int indice, char azione);
  void apriPorte(int durataOverride = 0);

 private:
  struct Evento { int64_t seq; std::string tipo; int64_t ts; std::vector<std::string> campi, extra; };
  struct Assemblaggio { int64_t versione = 0; int parti = 0; std::map<int, Pezzo> pezzi; std::string metaJson; };
  struct StatoIngresso { bool haStabile = false, stabile = false, cand = false; int conta = 0; bool haUltimo = false, ultimo = false; };

  // tempo
  int64_t ora() const { return hal_.unixTime() + deriva_; }
  bool oraValida() const { return sincronizzato_ || hal_.unixTime() > 1500000000LL; }
  struct Locale { int anno, mese, giorno, ora, minuto, giornoSettimana; std::string data; };
  Locale oraLocale() const;

  // invio
  void mandaFirmato(const std::vector<std::string>& campi);
  void mandaPresentazione(const std::string& ip);
  void impostaMaster(const std::string& ip);
  void mandaBattito();
  void mandaCacheAck(int64_t versione);
  void mandaRichiestaCache();
  std::string statoPortaTesto() const;
  std::string statoUsciteTesto() const;

  // coda eventi
  void accoda(const std::string& tipo, const std::vector<std::string>& campi, const std::vector<std::string>& extra = {});
  void pompa();
  void confermaEvento(int64_t seq);
  void salvaCoda();
  void caricaCoda();
  int64_t prossimoSeq();

  // ricezione
  void gestisciCache(const uint8_t* dati, size_t n);
  void applicaCache(const Assemblaggio& a);
  void gestisciAbbinamento(const std::string& testo, const std::string& ip);
  void gestisciTocco(const std::string& testo, const std::string& ip);
  void gestisciPiccolo(const std::string& testo);
  void salvaCache();

  // decisione
  std::pair<bool, std::string> valutaAccesso(const std::string& codice, int lettore = 0);
  bool inFasce(const std::vector<Riga>& righe) const;
  bool portaLibera() const;

  // uscite
  void apri(const std::vector<int>& indici, int durataOverride);
  std::vector<int> indiciLettore(int lettore) const;
  int durataUscita(int i, int override_) const;
  void sirena();
  void impostaStato(int indice, bool acceso);
  void tickUscite();
  void aggiornaRelay();
  void aggiornaLed();
  void salvaStatoUscite();
  void ingressoCambiato(int indice, bool attivo);

  // ingressi e porta
  void campionaIngressi();
  bool leggiIngresso(int i, bool nc, bool& attivo);
  int indiceSensore() const;
  int sogliaAperta() const;
  void portaLettura(bool aperta);
  void portaTick();
  void portaAutorizzata(int sec);

  Hal& hal_;
  uint32_t id_;
  int intervalloBattitoMs_;
  std::string chiave_, nonce_;
  std::string masterIp_;                              // imparato da un tocco firmato (o dall'abbinamento), salvato nel flash
  std::string toccoIp_;                               // chi ci ha toccato per ultimo senza che la firma si potesse verificare
  uint64_t toccoAlle_ = 0;

  Cache cache_;
  bool cacheRicevuta_ = false, cacheFresca_ = false, sincronizzato_ = false;
  int64_t deriva_ = 0;
  Assemblaggio asm_;

  std::deque<Evento> coda_;
  int64_t ultimoSeq_ = 0, inviatoSeq_ = -1;
  uint64_t inviatoAlle_ = 0;
  int tentativi_ = 0;

  uint64_t impulsoFino_[N_USCITE] = {0, 0};
  bool mantenuto_[N_USCITE] = {false, false};
  bool eccitato_[N_USCITE] = {false, false};
  bool statoUscita_[N_USCITE] = {false, false};
  uint64_t sirenaFino_ = 0, ledRossoFino_ = 0;
  bool ledVerde_ = false, ledRosso_ = false;
  StatoIngresso ing_[N_INGRESSI];

  int8_t portaAperta_ = -1;                  // -1 nessuna lettura, 0 chiusa, 1 aperta
  uint64_t apertaDal_ = 0, autorizzataFino_ = 0;
  bool avvisata_ = false;

  uint64_t ultimoBattito_ = 0, ultimoCampione_ = 0, ultimoTick_ = 0, ultimaRichiesta_ = 0;
  bool primoBattito_ = true, haRichiestoCache_ = false;
};

}  // namespace gk
