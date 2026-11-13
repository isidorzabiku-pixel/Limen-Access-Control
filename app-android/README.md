# Limen app (Android)

> Tested on the bench on several Android phones with the `esp32s3_blecred` firmware (about 1.5 s from tap to relay).
> Not yet done: encryption of the ESP32 flash and of backups, where the phone key is stored.

The phone as an access credential for Limen gates (ESP32 slave, firmware env `esp32s3_blecred`, `src/ble_cred.*`).

- **Home**: the gates this phone knows, each with a reachability dot (*Nearby* / *Out of range*, from a light
  Bluetooth scan that runs only while the app is in the foreground) and an **Open** button: one tap connects, answers
  the gate's challenge, shows the result and disconnects.
- **+ (New gate)**: the only place where the app looks for devices. First gate: pick it, type the **invite** and a
  name → *Pair*. Other gates of the same site: no invite, just *Add* (the phone's credential is valid on every gate
  of the site where the person has a rule).
- **Gate page (›)**: rename, device name, this phone's credential code, last result, *Pair this phone again*
  (after the admin regenerated the phone key or deleted the credential), *Remove this gate* (removing the last one
  also deletes the credential from the phone).

Kotlin, Jetpack Compose, native `android.bluetooth.le`, no BLE libraries. minSdk 26 (Android 8.0). UI in English,
colours and logo of the Limen webapp. Permissions: Android 12+ `BLUETOOTH_SCAN` and `BLUETOOTH_CONNECT`; Android 8–11
location. Files: `BleClient.kt` (scan + operations), `ChiaveTelefono.kt` (credential and calculations), `Varchi.kt`
(saved gates), `MainActivity.kt` (the three screens).

The old test firmware `esp32s3_ble` (code in clear, no authentication) is **no longer supported** by the app from
0.5: use a generic app such as LightBlue for it.

## Phone credential (`esp32s3_blecred`)

The phone has no key of its own: at pairing the gate gives it a **credential code** (23 characters, the same the admin
sees in the person's credentials) and a **secret** of 32 bytes, different for every phone. The secret is stored in the
Android Keystore (it can be used, not read back). Protocol as in `firmware/esp32/src/ble_cred.h` and `docs/ARCHITECTURE.md`.

- **Open**: reads the challenge (16-byte nonce) and writes `'O' || code (23) || HMAC-SHA256(secret, "LIMEN-OPEN" || nonce)`.
  Reply: `OK <result>`, `NO <result>` or `IGNORED`. A phone that is not paired (or was removed) gets only `NO`: the gate
  logs nothing and sends nothing to the master.
- **Pair**: the invite never travels. `K = PBKDF2-HMAC-SHA256(invite, "LIMEN-INVITE-v1", 20000, 32 bytes)`; the app writes
  `'P' || phone nonce (16) || HMAC-SHA256(K, "LIMEN-PAIR" || nonce || phone nonce)`. Reply: `PAIR SENT`, then `PAIR NO` or
  `PAIR OK <hex>`: code and secret encrypted with K plus a 16-byte tag that only the real gate can compute
  (see `ChiaveTelefono.riceviCredenziale`). If the tag is right the credential is saved.

Pairing steps:

1. Webapp, People → person → Access credentials → **Phone**: the admin picks the gate and presses *Invite a phone*
   (single-use code, 10 minutes; the gate opens its pairing window by itself).
2. App, in front of that gate: **+**, pick the gate, type the invite and a name, **Pair**. No approval step: the phone
   appears in the person's credentials and the master sends the data.
3. Home: **Open**.

If the admin regenerates the phone key (System properties), every phone must be invited again (*Pair this phone again*).

## Build

Needs JDK 17 or newer and the Android SDK (platform 35, build-tools 35). Point Gradle to the SDK with
`ANDROID_HOME`, or with a `local.properties` file containing `sdk.dir=/path/to/Android/Sdk` (not committed).

```sh
cd app-android
./gradlew assembleDebug
```

The APK is in `app/build/outputs/apk/debug/` (`app-debug.apk`). A ready-built debug APK is attached to the
GitHub Release of the repo (check its SHA-256 before installing).

## Install

Enable USB debugging on the phone, connect it, then:

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Or copy the APK to the phone and open it (allow installing from unknown sources). Updating over an older
install keeps the key only if the APK is signed with the same debug key; otherwise uninstall first (the
phone then needs pairing again).

On Android 11 and older, Location must be switched on, or the scan finds nothing.
