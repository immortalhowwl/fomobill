# Public-chain regression fixtures

These are **actual public-chain/provider responses**, fetched during implementation. Do not expose this directory as static content. They contain no wallet keys or signed operations. Runtime monitoring does not read these files.

- `pump-idl.json`: official https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump.json, fetched 2026-10-12; SHA256 `6a19bad7758fb822cee79a91575e51a7bd60391c670200408676643e00a79a6d`.
- `probe-*.json`, `live-signatures.json`: finalized Pump-program signature discovery receipts.
- `real-version-error.json`: genuine version-support JSON-RPC error.
- `real-pump-transaction.json`: same real transaction retried with version 1 support; no decoded trade asserted.
- `live-tx-1.json`: genuine wrapper Pump sell transaction; raw response retained as `body` with SHA256.
- `live-ingestion.json`: first actual engine pass, including exact JSON-RPC requests and bodies; records initial decoder gap for `buy_exact_quote_in`, subsequently resolved with the real receipt and official schema. Do not rewrite this historical run's errors as success.
- `live-ingestion-final.json`: later successful real ingestion run, 6 RPC requests / 5 retained transactions / 1 sell / 2 transfers. Counts are sampled observations, not complete wallet history.
- `publicnode-probe.json`, `publicnode-signature-probe.json`: later successful health/signature probes. Initial request to this provider returned HTTP 403 (tool output); availability recovered on subsequent probes.

Synthetic mutation/analytics tests live in `test_monitoring*.py`, are clearly labeled, and only create temporary SQLite databases. No synthetic transaction was placed in a production fixture or live-ingestion file. Tests never call the network.
