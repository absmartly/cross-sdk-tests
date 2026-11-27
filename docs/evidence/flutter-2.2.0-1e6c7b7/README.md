# Exact 2.2.0 release gate

SDK: **1e6c7b7518c2496384c36a0844e904b10576e569**, `absmartly/flutter-sdk#12`.
Harness: branch `verify/flutter-2.2-exact-0fb16e4` (name keeps the first SDK short SHA; the pin is 1e6c7b7), based on #48
`fix/dart-wrappers-absmartly-sdk-2.2` at `9ff745693688617518817533a81075f467ba7ad3`.
Collector verifier imported from consolidated #52 at
`5a3385f03cc37368ba1e65137f64185277711db6` (not its 3.x wrappers).
Run date: 2026-11-27; Linux arm64, Dart 3.14.0, Flutter 3.50.0.

No SDK code or capability policy changed. Both path dependencies resolve to
`/workspace/repos/flutter-sdk/packages/`, at the SHA above. No switchbacks/3.x
source was used. Wrappers are #48's versions, with one harness fix: their
loopback mock-endpoint rewrite honors `PORT` instead of always using 3000.
Default container behavior (PORT unset) stays 3000.

## Results

| Gate | Dart | Flutter |
|---|---|---|
| Conformance | 252 pass, 0 fail, 53 reviewed skips / 305 total | 252 pass, 0 fail, 53 reviewed skips / 305 total |
| Capability violations / errors / consistency failures | 0 / 0 / 0 | 0 / 0 / 0 |
| Local collector | PASS | PASS |
| Raw exposures | 10 expected, 10 received | 10 expected, 10 received |
| Raw goals | 19 expected, 19 received | 19 expected, 19 received |
| Raw purchase revenue | 1000021.6 planned / 1000021.6 received | 1000021.6 planned / 1000021.6 received |

Collector checks are against the received raw HTTP publishes, not wrapper return
values: exact experiment id/name/variant and unit hashes, exposure deduplication,
exact goal/property multiset (nested JSON, null, booleans, Unicode), summed
purchase amounts using Decimal, and transport headers and JSON. No collector
exceptions apply to either lane. Collector/verifier unit tests: 59/59 pass (direct and discovery); full tests/: 203 unittest pass; orchestrator/tests: 152 pytest + 6 subtests pass.
Strict gate additionally checks GET arrivals/agent/app/env/application-version,
PUT application-version, millisecond event timestamps and all ordinary false exposure flags.

The first run failed 235 scenarios per lane because the native wrappers were
launched on 3101/3102 but rewrote local mock endpoint URLs to 3000. After fixing
that harness port rewrite, every tested scenario passed. This was not an SDK
failure. Initial failed logs are retained locally in test-results/exact-1e6c7b7/;
the committed conformance.json is the corrected full run.

## Reproduction commands (from cross-sdk-tests root)

```sh
git -C ../flutter-sdk checkout 1e6c7b7518c2496384c36a0844e904b10576e569
uv venv /tmp/flutter-conformance-venv
uv pip install --python /tmp/flutter-conformance-venv/bin/python -r orchestrator/requirements.txt
(cd dart-wrapper && dart pub get)
(cd flutter-wrapper && flutter pub get)
export ABSMARTLY_E2E_ENDPOINT=http://127.0.0.1:8090/v1
export ABSMARTLY_E2E_API_KEY=local-collector
export ABSMARTLY_E2E_APPLICATION=e2e-tests
export ABSMARTLY_E2E_ENVIRONMENT=production
# Run each server in its own terminal; the Flutter server exercises real widgets.
(cd dart-wrapper && PORT=3101 dart run server.dart)
(cd flutter-wrapper && PORT=3102 flutter test --plain-name='Widget Test Server' test/server_test.dart)
COLLECTOR_PORT=8090 /tmp/flutter-conformance-venv/bin/python local-collector/local_collector.py
# In another terminal, after all servers are listening:
mkdir -p test-results/exact-1e6c7b7
/tmp/flutter-conformance-venv/bin/python docs/evidence/flutter-2.2.0-1e6c7b7/run_conformance.py
SDK_URLS_OVERRIDE=dart=http://127.0.0.1:3101,flutter=http://127.0.0.1:3102 \
 /tmp/flutter-conformance-venv/bin/python orchestrator/local_collector_gate.py \
 --sdk dart,flutter --collector http://127.0.0.1:8090 \
 --report test-results/exact-1e6c7b7/local-collector.json
curl -fsS http://127.0.0.1:8090/publishes > test-results/exact-1e6c7b7/publishes.json
curl -fsS http://127.0.0.1:8090/requests > test-results/exact-1e6c7b7/requests.json
/tmp/flutter-conformance-venv/bin/python -m unittest discover -s tests -p test_local_collector.py
```

The committed JSON files are raw evidence from this run. The conformance runner
uses the same loaders, policy and report verdict as verify_sdk.sh, only native
wrapper URLs replace Docker service discovery. The collector shell/Docker
launcher is also included for parity with #52, but this run used native servers.

## Consolidation / release rule

Owner decision: #48 content must be incorporated through consolidated #52; do
not merge #48 separately. Reviewed exact-head local conformance and collector
passes satisfy this release gate: #52 merging is not itself a prerequisite.
This companion is not a new standalone PR. Its pin is deliberately this exact
2.2 release SHA, not the 3.x pin #52 uses for its existing Dart/Flutter lanes.

The final SDK fixes malformed-surrogate UID hashing to match published JS 1.14.1/Java/Flutter 2.1.2. Both wrappers were restarted before this run. Earlier 0fb16e4 evidence is retained separately, not relabelled.
