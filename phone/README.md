# CallRelay Android gateway

This component bridges one selected cellular SIM to SIP.
It derives from [Android Voice SIP Agent](https://github.com/sparkcyf/android-voice-sip-agent).
It retains the upstream GPL license and linking exception.

CallRelay uses `io.github.sparkcyf.voicesipagent.callrelay` as its application ID.
It has separate app data and signing settings. Takeover starts off.

## Build

Install Java 17+, Android SDK platform 37, NDK `28.2.13676358`, CMake, SWIG, and standard native build tools.
Set `ANDROID_HOME` to your Android SDK directory.

Run the pure JVM tests:

```bash
./gradlew :gateway:test :sip:test --no-daemon
```

Build the native SIP library, then the APK:

```bash
./sip-pjsua2/native/build-pjsua2.sh
./gradlew :app:assembleDebug --no-daemon
```

The native script downloads versioned source archives and checks their SHA-256 hashes.
Generated bindings and native libraries stay outside Git.
The default APK targets `arm64-v8a`.

For release signing, supply your own `callrelay.signing.storeFile`, `storePassword`, `keyAlias`, and `keyPassword` Gradle properties.
Keep the keystore and property values outside Git.
Without those properties, the release APK remains unsigned.
The build does not read the older gateway signing-property prefix.

## Connect

Use the SIP address and password from your own installation settings.
Grant Shizuku access and phone permissions on the handset.
Select the intended SIM before enabling takeover.

The ADB receiver requires the shell's DUMP permission.
For an explicit SIM selection, use:

```bash
adb shell am broadcast \
  -n io.github.sparkcyf.voicesipagent.callrelay/io.github.sparkcyf.voicesipagent.app.AdbControlReceiver \
  --es op select-sim --ei slot 1 --es country us
```

Replace the slot and country with your intended SIM values.
Do not guess the slot from a label in another application.
The gateway pins the selected phone account and fails closed if that account changes.

Check status:

```bash
adb shell am broadcast \
  -n io.github.sparkcyf.voicesipagent.callrelay/io.github.sparkcyf.voicesipagent.app.AdbControlReceiver \
  --es op status
```

The original Java/Kotlin class namespace remains intact.
The application ID in the first part of the component name identifies this community edition.
Use the phone interface for SIP credentials. Do not put passwords in shell history.

## Inbound behavior

Only the selected SIM can enter the gateway bridge.
The gateway offers an incoming call to the private PBX before answering the handset.
A failed admission before answer returns the call to native handling.
After both audio legs connect, the gateway sends the media-ready marker.

The Python service keeps inbound answering off until business setup and handset acceptance are complete.
Keep loopback echo off for normal calls.

## Compatibility and notices

Audio control depends on the phone model and Android firmware.
An active SIP registration does not prove that two-way cellular audio works.
Test a spare handset before unattended use.

See [UPSTREAM.md](UPSTREAM.md), [LICENSE](LICENSE), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
The upstream license is GPL version 2 or later.
It permits linking with Apache-2.0 libraries under the exception recorded in the upstream notice.
