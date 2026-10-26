# Component licenses

This repository contains separate programs. Each program keeps its license.

| Component | License | Notice |
| --- | --- | --- |
| Python platform, dashboard, setup tools, and supervisor | MIT | [LICENSE](LICENSE) |
| Android gateway in `phone/` | GPL-2.0-or-later | [phone/LICENSE](phone/LICENSE) |
| Gateway native and Java dependencies | Their original licenses | [phone/THIRD_PARTY_NOTICES.md](phone/THIRD_PARTY_NOTICES.md) |
| Asterisk | GPL-2.0 | [Upstream license](https://github.com/asterisk/asterisk/blob/master/COPYING) |
| Fallback prompt text and generator | MIT | `app/reception_audio.py` and the audio manifest |

The root MIT license does not relicense `phone/` or third-party dependencies.
The Android gateway runs as a separate application and communicates through SIP and its control interface.
The Python service does not link Android or PJSIP code into its process.

The gateway retains this upstream exception:

> As a special exception, linking with any library licensed under the Apache License, Version 2.0 is permitted, provided all other code complies with the GPL.

The repository ships gateway source, not a signed release APK.
The native build script identifies dependency versions and source archives.
If you distribute gateway binaries, provide their corresponding source and preserve the required notices.

The [GNU FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#MereAggregation) describes separate programs and combined works.
The component notices remain the source of their license terms.

The included WAV files are synthetic output from the supplied text. The eSpeak NG program is not bundled.
