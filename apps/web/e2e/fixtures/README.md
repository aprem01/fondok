# Test fixtures

Files in this directory are used by the Playwright E2E suite.

## sample-t12.pdf

A minimal placeholder PDF (95 bytes — just the `%PDF-1.4` header + `%%EOF`).
This satisfies the wizard's extension allowlist (`.pdf`) so the
"Next is enabled after a financial is uploaded" gate test can fire, but
it will **not** parse cleanly through the worker pipeline.

If you want to exercise the real upload → parse → extract pipeline
(against a deployed worker), drop a real T-12 PDF here with the same
filename. The wizard-flow spec checks for the file's existence and
skips upload assertions when it's missing — but the minimal byte file
shipped in-repo is enough for the gate test.

Sam's real T-12 fixtures live under `apps/worker/tests/fixtures/` — feel
free to copy one over locally if you want a richer test signal.

## tiny-unsupported.zip

29-byte ZIP file header — used by the unsupported-file-type rejection
test to confirm the wizard surfaces the `unsupported file type` toast
without staging the file.
