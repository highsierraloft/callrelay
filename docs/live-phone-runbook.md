# Advanced live-phone tests

The inherited `scripts/live_phone` harness tests the optional Twilio/OpenAI backend.
It is not part of the Android installation procedure.
It requires dedicated test numbers, explicit instance confirmation, and provider credentials.

Do not run it in normal CI or against a personal number.
Use [the handset acceptance procedure](self-hosting.md#9-accept-your-installation) for the Android route.
