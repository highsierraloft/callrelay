# Phone supervisor

This process recovers control of one configured Android handset.
It reads `device.json`, a persistent ADB identity, and the handset's status.
It has no network access and never dials or answers calls.

The selected SIM slot and exact model come from the operator settings.
Unknown devices, locked users, missing keys, and unknown call states fail closed.
During a call, the process only reads status.

The controller can restore Shizuku and a required phone permission while idle.
It cannot select another SIM or enable gateway takeover.
The status heartbeat separates process liveness from actual call readiness.

Run the tests with:

```bash
python3 -m unittest discover -s supervisor
```

Use the [installation guide](../docs/self-hosting.md) for USB access and container settings.
