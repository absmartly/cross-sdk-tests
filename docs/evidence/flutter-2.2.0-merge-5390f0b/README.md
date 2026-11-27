# Post-merge 2.2.0 verification — 2026-11-27

SDK merge **5390f0bb9b6825785c0fc3cf3e783acc6a909dcf**. Its tree is
**e2b0414a55502c41d803d286d23e503a427811db**, identical to gated/approved
candidate **1e6c7b7518c2496384c36a0844e904b10576e569**. Both pubspecs and
SdkVersion are 2.2.0. Hosted absmartly_dart 2.2.0 verified via registry API,
archive SHA256 f3f1eac57aa4b5cc1da80ca8831ccd4e6939ed9fa57db68dfa84b3bd420ab61c.
No tag or publication performed by this worker.

Harness run source **0fd507f67163240d212f3550e2b9b1933d09c06c**, branch
verify/flutter-2.2-exact-0fb16e4; SDK checkout detached at exact merge before
starting both wrappers. #48-compatible wrappers, no switchbacks/3.x source.
Pin update after successful run points to the merge commit, not candidate.

## Measured results (UNVERIFIED = FAIL)

- SDK unit suites: Dart 592, Flutter 183, compiled-JS 15, compatibility 1, all pass.
- Dart conformance: 252 tested/pass, 0 fail, 53 reviewed skips / 305.
- Flutter conformance: 252 tested/pass, 0 fail, 53 reviewed skips / 305.
- Both lanes: 0 capability-policy violations, 0 consistency failures.
- Strict collector each lane: PASS, 10 exposures, 19 goals, received and planned
  purchase revenue 1000021.6. No lane exception. Raw PUT and GET evidence attached.
- Collector rejects malformed/duplicate units and checks selected wire headers,
  timestamp/flag metadata, exact exposure/goal multisets and Decimal revenue.
  This is not backend analysis or exhaustive attributes-history validation.

Skips unchanged: 203–208, 210–227, 253, 302, 305, 323, 400–420, 422–425.
Capability policy unchanged.

## Commands

Use the isolated venv /tmp/flutter-conformance-venv (requirements from
orchestrator/requirements.txt; pytest added in that venv, never globally).

```sh
git -C ../flutter-sdk checkout --detach 5390f0bb9b6825785c0fc3cf3e783acc6a909dcf
# Start real wrappers with ABSMARTLY_E2E_ENDPOINT=http://127.0.0.1:8090/v1,
# API_KEY=local-collector, APPLICATION=e2e-tests, ENVIRONMENT=production:
(cd dart-wrapper && PORT=3101 dart run server.dart)
(cd flutter-wrapper && PORT=3102 flutter test --plain-name='Widget Test Server' test/server_test.dart)
COLLECTOR_PORT=8090 /tmp/flutter-conformance-venv/bin/python local-collector/local_collector.py
/tmp/flutter-conformance-venv/bin/python docs/evidence/flutter-2.2.0-merge-5390f0b/run_conformance.py
SDK_URLS_OVERRIDE=dart=http://127.0.0.1:3101,flutter=http://127.0.0.1:3102 \
 /tmp/flutter-conformance-venv/bin/python orchestrator/local_collector_gate.py \
 --sdk dart,flutter --collector http://127.0.0.1:8090 --report test-results/merge-5390f0b/local-collector.json
```

Raw conformance.json, local-collector.json, publishes.json, requests.json are
from this merge run; earlier candidate evidence remains separately unchanged.
All companion changes fold through consolidated #52, no standalone harness PR.
