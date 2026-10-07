# TraveAI integration examples

Small, copyable scripts showing a full integration. They only need `httpx` (the webhook
receiver needs nothing beyond the Python standard library).

Start a local server with the simulator, then point the scripts at it with a test key from
`.env.example`:

```bash
TRAVEAI_SIM_ENABLED=true TRAVEAI_SIM_SPEED=10 TRAVEAI_WEBHOOKS_ENABLED=true \
  uvicorn traveai.main:app
export TRAVEAI_API_KEY=sk_test_...
```

| Script | Shows |
|---|---|
| `pharmacy_prescription.py` | Quote a cold-chain prescription, book it, follow the drone, release the package with the patient's PIN, read the proof and chain of custody. |
| `restaurant_meal.py` | Quote a hot meal, book it, follow it live with Server-Sent Events. `--fail high_wind` forces a failure to show the abort-and-retry path. |
| `webhook_receiver.py` | Receive webhooks, verify the `TraveAI-Signature`, ignore duplicates, react to events. |

```bash
python examples/pharmacy_prescription.py
python examples/restaurant_meal.py --fail high_wind
TRAVEAI_WEBHOOK_SECRET=whsec_... python examples/webhook_receiver.py
```

Each script's `run(...)` function is also exercised by the test suite
(`tests/test_examples.py`), so these examples stay in sync with the API.
