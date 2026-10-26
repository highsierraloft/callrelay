# Technical reference

Detailed usage, build, and implementation notes for Voice SIP Agent. See the
[README](README.md) for installation, SIP server configuration, supported devices, and build commands.

- [Call access and takeover](#call-access-and-takeover)
- [Audio formats and routing](#audio-formats-and-routing)
- [Native dependencies](#native-dependencies)
- [Native build details](#native-build-details)

## Call access and takeover

With **Take over calls** enabled, tap **Grant Shizuku** in Health and allow access. The app then runs its setup
automatically (appops, Telecom companion override where applicable, Doze allowlist, runtime permissions)
and repeats it after upgrades.
A health check runs every 60 s while takeover and the foreground service are active, and retries setup while
idle if companion call access is unavailable. SIP, bridging, the Shizuku helper, and network monitoring also
start only after the service enters foreground; opening the configuration with takeover saved off leaves them stopped.

By default, SIP stays unregistered until local call access and privileged audio access are ready.
After first readiness, local failures get one 45-second recovery window before SIP unregisters;
registration changes wait for calls and bridge sessions to end. Incoming calls received while the
phone is unavailable are rejected with `480`. Local recovery registers SIP again automatically.
Turning **Unregister while the phone cannot take calls** off allows registration without local readiness;
the call admission check still applies. Local and PBX failures leave takeover enabled and do not change
dialer ownership. PBX registration failures use the SIP stack's retries; the built-in stack also reports
DNS failures and retries them. Unregistration is best effort if the PBX is unreachable, so stale contacts
may remain until expiry.

PJSIP native initialization and DNS run on a serialized startup worker, leaving gateway controls
available while they wait. Stopping an endpoint cancels pending account creation. Native library
teardown is queued on the same worker before replacement startup; an outstanding initialization or
DNS lookup is not interrupted, so native cleanup can finish after the gateway controls return.

An unexpected PJSIP event-loop exception marks SIP unavailable and ends its local call legs, allowing
the gateway to release the cellular call and audio. Deferred native wrappers are deleted on their owner
thread before library destruction. Ordinary command failures only log. Takeover and dialer ownership
remain unchanged; use **Reconnect SIP** or turn takeover off/on to start a new library lifetime.

On Android 11, turn **Take over calls** on (or tap **Enable takeover**) to grant default-dialer call access.
With Shizuku connected, this saves your previous dialer and changes the role directly, without the
system role dialog. Changes wait for current calls to end and can be cancelled while waiting.
Without Shizuku, the explicit switch-on action opens Android's role-selection dialog.
Startup, setup and health checks never acquire the role. On newer versions, Automatic call access keeps companion
integration unless the app already holds the dialer role; Advanced settings can select either mode explicitly.

**Take over calls** is the master switch. In default-dialer mode, switching it on also saves your previous
dialer and acquires the role. Switching off waits for current calls and bridge cleanup, then stops bridging
and companion integration and restores your previous dialer through Shizuku.
If that dialer is unavailable, the app attempts the system fallback. **Choose dialer in Settings**
remains available in Advanced settings and appears on restoration failure for manual recovery.
**Keep this app as the default phone app when gateway is off** in Advanced disables automatic restoration on
the next switch-off. That switch and **Make default phone app** are enabled in default-dialer mode.
**Restore previous dialer** is shown in Takeover when the app holds the role, takeover is on, and Shizuku is
connected. With takeover off or Shizuku unavailable, use **Choose dialer in Settings**.
Role changes made in Settings leave takeover unchanged; a pending
request will not replace a different dialer selected while it was waiting. The saved return dialer
survives process restarts, but pending role changes are never replayed automatically after restart.
Toggling takeover again cancels the pending handoff; existing calls keep their bridge.

On Android 12+, Automatic returns to companion mode after restoration. Select **Default dialer** in Advanced
to acquire the role on each switch-on. **Alongside dialer** is disabled on Android 11. If this app currently
holds the role, restore your usual phone app before using companion mode. Both modes require Shizuku for audio.
Switching takeover off waits for owned calls and bridge cleanup, then unregisters SIP, queues teardown of the native
pjsua2 library, cancels health/dialer polling, unregisters network monitoring, and releases this app's Shizuku
helper after handoff cleanup. The shared Shizuku server keeps running. Switching on starts a new gateway lifetime.
SIP stack changes wait for the old stack's local socket release before starting the replacement. Native
release waits for library destruction; ordinary pjsua2 reconnects reuse the library and transport.
Unregister remains best effort. Cleanup failures and bounded wait timeouts appear in the local log;
timing out does not cancel queued cleanup. A failed socket release leaves the replacement unavailable.
In companion mode, switching takeover off also stops the foreground service and removes its notification;
idle gateway standby keeps both running. In dialer mode, the service keeps a reminder visible while this app
is the default dialer, even with gateway work stopped. Service destruction also releases gateway resources
and cancels pending role changes; cleanup does not acquire or restore a dialer role.

Advanced settings are collapsed by default. They contain call-access and cellular-audio choices, dialer-role
acquisition and retention, system dialer settings, and diagnostics. **Mute call audio on this phone** is in
Takeover, **Echo incoming calls (test)** is in Call, and **pjsip debug log** is in Settings. The log remains
visible below Advanced. While takeover is on, echo testing answers incoming cellular calls and echoes the
caller's audio, bypassing the PBX. Changes wait for current calls to end; **Disable echo test** restores normal bridging.

Health indicators are neutral while off; setup and recovery controls require the gateway to be running.
**Call access** appears in Takeover with dialer status and recovery actions. **Re-run setup** requires the
connected helper; **Reconnect SIP** requires configured SIP credentials. Setup, SIP restart, call-access and
audio-method changes wait for active calls and bridge cleanup before applying.

The SIP form distinguishes **Unsaved changes** from submitted **Changes pending until calls end**. Tap
**Save & apply** to submit edits; active calls and bridge cleanup delay their application. Saved SIP settings
do not start registration while takeover is off. The **pjsip debug log** switch takes effect immediately.

## Audio formats and routing

**Cellular audio** defaults to Automatic, which tries call redirection first, then direct telephony if needed.
Call redirection requires Android 13+ and is disabled on older versions. Audio-method changes made during a
call wait until calls finish and affect the next call.

The default pjsua2 SIP stack prefers mono Opus (16 kHz PCM, 20 ms frames, 20 kbit/s target), with G.711
(PCMU/PCMA) fallback. Android capture/injection and the bridge use 16 kHz mono PCM for pjsua2, and 8 kHz
for the built-in Kotlin/G.711 stack. Each 20 ms socket packet is respectively 640 or 320 bytes. PJMEDIA
resamples between the 16 kHz bridge and G.711. There is no automatic 16-to-8 kHz fallback: Android
open/start/read/write failures are reported and terminate the tap. A 16 kHz stream can still contain
upsampled narrowband audio, depending on the cellular call and device. Opus uses the standard
`opus/48000/2` SDP mapping with `maxplaybackrate=16000` and `sprop-maxcapturerate=16000`. G.722 is disabled.

Opus VAD/DTX is enabled by default to reduce cellular-to-SIP silence payload bandwidth; SDP advertises
`usedtx=1` to request DTX from the SIP peer too. The peer's negotiated DTX preference is honored.
PCMU/PCMA keep VAD disabled and transmit continuously. PCM capture and bridging continue every 20 ms.

Quiet speech, call-progress tones, hold music and prolonged silence with PBX/NAT timeouts still need
device testing. Acoustic echo cancellation belongs at the microphone endpoint.
The 16 kHz tap and Opus calls have not yet been verified on a device.

Direct telephony audio skips the modern redirection helpers and uses voice-call stream mute for handset output,
including on API 30–32; supported redirection taps retain mode-based suppression. The helper restores its
changes when the tap closes and preserves an already-muted stream. Device policy/HAL support and both
audio directions still need testing with suppression enabled. If the helper is forcibly killed, stream
mute can remain active; raising the call volume can unmute it.

For local listening, turn **Mute call audio on this phone** off in **Takeover** before the call.
Downlink output follows the phone's current call audio route (earpiece, speaker, or headset) and volume.
The handset microphone stays muted while bridging, so local listening does not enable local speech.

### Potential optimization: PJSIP audio switchboard

PJSIP's [audio switchboard](https://docs.pjsip.org/en/latest/specific-guides/audio/switchboard.html)
(`PJMEDIA_CONF_USE_SWITCH_BOARD`) may be an optimization for this gateway. It remains disabled because
G.711's 8 kHz PCM needs conversion to the fixed 16 kHz bridge; enabling it would require explicit resampling
or coordinated per-call tap, media-port and clock rates.

## Native dependencies

The native build script builds OpenSSL, libopus, and PJSIP's Java/JNI bindings from pinned,
SHA-256-verified upstream tarballs and applies the patches in `sip-pjsua2/native/`.

Tarballs and shared dependency source trees live under `sip-pjsua2/build/pjproject/` (override with
`PJ_WORK`). Each ABI has `openssl-build/`, `opus-build/`, `prefix/{openssl,opus}/` and its own PJSIP
source/build tree, since PJSIP generates target-specific configuration and headers inside its source tree.
Set `ABIS="arm64-v8a x86_64"` to build multiple targets sequentially. APK packaging follows Gradle's ABI
filters; build native libraries for every ABI selected by the app.

Each requested ABI directory is recreated and rebuilt from scratch, including dependency installations,
PJSIP patches and JNI output. Shared dependency sources and verified tarballs are retained; there are no
build fingerprints or incremental object caches. SWIG generates the Java bindings and C++ wrapper once per
invocation; the wrapper is compiled and linked separately for each ABI. New bindings are staged and published
only after all requested ABIs succeed, preserving the previous output on a build failure. Do not run builds
sharing the same work/output directories concurrently.

## Native build details

See [building from source](README.md#building-from-source) for prerequisites and build commands.
JVM modules target Java 17 bytecode using the JDK running Gradle; a separate JDK 17 toolchain is not required.

Native C/C++ code uses ThinLTO, hidden visibility and section garbage collection. The final export map
exposes only PJSUA2 JNI entry points; OpenSSL, Opus and PJSIP symbols are local. The build checks exports
and writes `pjsua2.exports.txt` under the ABI work directory. Exported JNI wrappers and OpenSSL's internal
algorithm registries still retain reachable code; Java shrinking alone does not remove native wrappers.

`native/pjsua2-java-profile.i` trims the Java/JNI surface by feature group while keeping upstream
`pjsua2.i`: video, buddies, file playback/recording, AI media ports, tone generators and JSON persistence
are excluded. `Endpoint` uses its native default callbacks; `Account`, `Call`, `AudioMediaPort` and
`LogWriter` retain Java directors, including their default callbacks. SIP/TLS/SRTP configuration, codec
controls, `AudDevManager.setNullDev()` and bulk PCM copies remain available. Restore an excluded group
in that file if needed, or the `Endpoint` director if subclassing it. On PJSIP upgrades, check the
exclusions against upstream names, rebuild the bindings and app (including release), and verify SIP
callbacks and audio on a device. This profile
reduces JNI reachability; it does not remove all underlying native implementations.

Release builds enable R8 with consumer rules preserving SWIG bindings/director callbacks and Shizuku's
reflectively loaded service constructor. Other app and dependency code can be optimized. Validate a release
APK's Shizuku startup, SIP callbacks and TLS/SRTP audio before relying on new shrinking rules.

Release builds are signed only when `vsa.signing.storeFile`, `vsa.signing.storePassword`, `vsa.signing.keyAlias`
and `vsa.signing.keyPassword` are set in `~/.gradle/gradle.properties`; otherwise `assembleRelease` produces an
unsigned APK.
