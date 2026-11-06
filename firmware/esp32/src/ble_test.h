// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Lettore BLE di PROVA (banco): un'app generica (nRF Connect, LightBlue) scrive un codice in una caratteristica
// e il codice passa a Slave::badge come quello della console e della UART. Compilato solo con -DLIMEN_BLE_TEST.
// ATTENZIONE: nessuna autenticazione (chiunque sia a portata puo' scrivere un codice): solo per il banco di prova.
#pragma once
#ifdef LIMEN_BLE_TEST
#include <Arduino.h>
#include <functional>

// Avvia il servizio BLE «Limen-BLE-XXXX». Va chiamata una volta, in setup().
void bleAvvia();

// Da chiamare in loop(): per ogni codice ricevuto chiama `gestore` (nel thread del loop, come la UART)
// e scrive la risposta («OK <esito>», «NO <esito>», «IGNORED», «ERR <motivo>») nella caratteristica «esito».
void bleLoop(const std::function<String(const String&)>& gestore);
#endif
