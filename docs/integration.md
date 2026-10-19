# Backend integration

The backend and its bundled UI use **same-origin** HTTP APIs. The canonical hosted backend is `https://api.fomobill.fun`. A different frontend origin is not automatically authorized by setting a Host allowlist; plan a compatible same-origin proxy/session arrangement rather than disabling protections.

## Public research routes

- `GET /health` — process health only.
- `GET /api/coverage` — supported coverage information.
- `GET /api/token?address=<mint>` — exact Solana token market lookup.
- `GET /api/wallet?address=<wallet>` — bounded signature activity.
- `GET /api/holders?address=<mint>` — available holder sample.
- `GET /api/holder-trades?address=<wallet>&mint=<mint>` — supported holder history.

For continuation, pass the returned opaque `nextCursor` as the URL-encoded `cursor` parameter. Do not substitute a per-address cursor. Deduplicate observations, preserve prior rows and reject stale responses when selection changes. Validate exact addresses; whitespace is not silently trimmed into a valid token.

## Browser identity and consent

Start a cookie-preserving session with `POST /api/session`, JSON body `{}`, `Content-Type: application/json`, `X-Fomobill: 1`, and `Origin` matching the backend's request origin. Retain the returned cookie securely; never accept a caller-supplied owner identifier in place of it.

Authenticated reads: `/api/monitor`, `/api/monitor/wallet?address=<wallet>`, `/api/monitor/evidence?signature=<signature>` and `/api/telegram`.

State-changing requests use the same headers and session cookie:

| Route | JSON body | Effect |
| --- | --- | --- |
| `POST /api/monitor/watch` | `{"address":"<wallet>"}` | Explicitly watch a wallet |
| `POST /api/monitor/unwatch` | `{"address":"<wallet>"}` | Remove a watch |
| `POST /api/telegram/pair` | `{}` | Request a Telegram pairing flow |
| `POST /api/telegram/stop` | `{}` | Stop Telegram delivery for this browser owner |

Only invoke mutation routes following the corresponding user action. A paired bot is not automatically subscribed. `availability: ready` is worker readiness, not proof of actual message delivery. Never run `getUpdates` as a separate health probe.

## Evidence and limitations

Holder-history decoding and background monitoring do not have identical protocol coverage. Preserve API coverage, cache and error metadata in the frontend. A holder ranking may have unknown snapshot age; a retrieval timestamp is not a balance timestamp. Transfer deltas alone are not buys. AMM quote legs are not necessarily complete routed proceeds or net wallet cost. Never synthesize PnL, USD values, relationships or missing trades.

The bundled workspace uses live adapters by default; example JSON is only development fixture material, not a live-data fallback. The Telegram welcome PNG is read locally for multipart delivery, not exposed through a public static route.

Source transfer does not transfer browser identities, subscriptions or ownership keys. No CORS relaxation, DNS changes or production cutover is implied by this repository.
