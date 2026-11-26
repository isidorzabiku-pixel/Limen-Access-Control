// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Apertura via NFC: il telefono si comporta come una carta (emulazione HCE) e risponde al lettore del varco con la
// stessa credenziale del BLE. Non serve aprire l'app: basta il telefono sbloccato appoggiato al lettore.
// APDU come firmware/esp32/lib/limen/nfc_telefono.h:
//   SELECT AID F0 4C 49 4D 45 4E 01         → 90 00 se abbinato, 6A 82 altrimenti
//   80 10 00 00 10 <sfida>                  → titolo | HMAC(S, "LIMEN-OPEN" | sfida) | 90 00
//   80 12 00 00 01 <01/00>                  → 90 00 (vibrazione breve se concesso)
package org.limen.bletest

import android.nfc.cardemulation.HostApduService
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

class NfcServizio : HostApduService() {
    private var selezionato = false

    override fun onCreate() {
        super.onCreate()
        ChiaveTelefono.inizia(this)
    }

    override fun processCommandApdu(apdu: ByteArray, extras: Bundle?): ByteArray {
        if (apdu.size < 4) return SW_SCONOSCIUTO
        val cla = apdu[0].toInt() and 0xFF
        val ins = apdu[1].toInt() and 0xFF
        if (cla == 0x00 && ins == 0xA4) {
            selezionato = apdu.size >= 5 + AID.size && apdu[4].toInt() == AID.size &&
                apdu.copyOfRange(5, 5 + AID.size).contentEquals(AID) && ChiaveTelefono.abbinato()
            return if (selezionato) SW_OK else SW_ASSENTE
        }
        if (!selezionato || cla != 0x80) return SW_SCONOSCIUTO
        return when (ins) {
            INS_APRI -> {
                if (apdu.size < 5 + LUNGHEZZA_SFIDA || apdu[4].toInt() != LUNGHEZZA_SFIDA) return SW_LUNGHEZZA
                val sfida = apdu.copyOfRange(5, 5 + LUNGHEZZA_SFIDA)
                val titolo = ChiaveTelefono.titolo()
                if (titolo.isEmpty()) return SW_ASSENTE
                runCatching { titolo.toByteArray(Charsets.US_ASCII) + ChiaveTelefono.macApertura(sfida) + SW_OK }
                    .getOrDefault(SW_ASSENTE)
            }
            INS_ESITO -> {
                if (apdu.size >= 6 && apdu[5].toInt() == 1) vibra()
                SW_OK
            }
            else -> SW_SCONOSCIUTO
        }
    }

    override fun onDeactivated(reason: Int) {
        selezionato = false
    }

    private fun vibra() {
        val v: Vibrator? = if (Build.VERSION.SDK_INT >= 31)
            getSystemService(VibratorManager::class.java)?.defaultVibrator
        else getSystemService(Vibrator::class.java)
        v?.vibrate(VibrationEffect.createOneShot(60, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    companion object {
        private val AID = byteArrayOf(0xF0.toByte(), 0x4C, 0x49, 0x4D, 0x45, 0x4E, 0x01)
        private const val INS_APRI = 0x10
        private const val INS_ESITO = 0x12
        private const val LUNGHEZZA_SFIDA = 16
        private val SW_OK = byteArrayOf(0x90.toByte(), 0x00)
        private val SW_ASSENTE = byteArrayOf(0x6A, 0x82.toByte())
        private val SW_LUNGHEZZA = byteArrayOf(0x67, 0x00)
        private val SW_SCONOSCIUTO = byteArrayOf(0x6D, 0x00)
    }
}
