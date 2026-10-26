# Gateway provenance

This component derives from [sparkcyf/android-voice-sip-agent](https://github.com/sparkcyf/android-voice-sip-agent).
It retains GPL-2.0-or-later and the upstream Apache-2.0 linking exception.
The original upstream base is `a134b8e5e0424024870b53252eb0d1b9f4d3195e`.

CallRelay changes, dated 26 October 2026, retain explicit SIM selection, account pinning,
selected-SIM inbound admission, native fallback, and media readiness signaling.
This edition also uses its own application ID, version, label, and signing-property prefix.

Modified source files carry a change notice. The inherited modified paths are:

- `app/build.gradle.kts`
- `app/src/main/kotlin/io/github/sparkcyf/voicesipagent/app/AdbControlReceiver.kt`
- `app/src/main/kotlin/io/github/sparkcyf/voicesipagent/app/GatewayRuntime.kt`
- `app/src/main/kotlin/io/github/sparkcyf/voicesipagent/app/SettingsStore.kt`
- `app/src/main/res/values/strings.xml`
- `gateway/src/main/kotlin/io/github/sparkcyf/voicesipagent/gateway/CallBridge.kt`
- `gateway/src/main/kotlin/io/github/sparkcyf/voicesipagent/gateway/GatewayConfig.kt`
- `gateway/src/test/kotlin/io/github/sparkcyf/voicesipagent/gateway/CallBridgeTest.kt`
- `gateway/src/test/kotlin/io/github/sparkcyf/voicesipagent/gateway/Fakes.kt`
- `privileged/src/main/kotlin/io/github/sparkcyf/voicesipagent/privileged/shell/PrivilegedService.kt`
- `sip-pjsua2/native/pjsip-close-idle-device.patch`
- `sip-pjsua2/src/main/kotlin/io/github/sparkcyf/voicesipagent/pjsua2/PjsipCall.kt`
- `telephony/src/main/kotlin/io/github/sparkcyf/voicesipagent/telephony/CellularEndpoint.kt`
- `telephony/src/main/kotlin/io/github/sparkcyf/voicesipagent/telephony/CellularLeg.kt`
- `telephony/src/main/kotlin/io/github/sparkcyf/voicesipagent/telephony/SimAccountSelector.kt`
- `telephony/src/test/kotlin/io/github/sparkcyf/voicesipagent/telephony/CellularOwnershipTest.kt`
- `telephony/src/test/kotlin/io/github/sparkcyf/voicesipagent/telephony/SimAccountSelectorTest.kt`
- `voice-api/src/main/kotlin/io/github/sparkcyf/voicesipagent/voice/CallLeg.kt`

See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The community edition regenerates the wrapper from Gradle 9.6.0 and retains the Gradle license notices.
