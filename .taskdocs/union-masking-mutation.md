# Union-masking fix: mutation proof

Scenarios 146 and 147 each gained a second experiment (`exp_only_three_arm_normal_path` /
`exp_only_two_arm_normal_path`) covered by only the non-suppressing holdout. To prove these new
assertions actually bite if the non-suppressing arm were wrongly implemented as suppressing, the
expected `result` and exposure `variant` were temporarily changed to `0` (the value a
wrongly-suppressing SDK would produce) and the scenario re-run against the live `java` SDK on
`http://localhost:3004`. The edit was reverted immediately after capturing output; it was never
committed.

## 146 — mutated `exp_only_three_arm_normal_path` expectation to result=0 / variant=0

```
=== 146 - Holdout Arms - Union Two-Arm Holds Out While Applicable Three-Arm Does Not (uid e791e240fcd3df7d238cfc285f475e8152fcc0ec)
  java                 ✗ FAIL (2 failures)
      {'step': 2, 'action': 'treatment', 'field': 'result', 'expected': 0, 'actual': 1}
      {'step': 2, 'action': 'treatment', 'field': 'events[0].data.variant', 'expected': 0, 'actual': 1}
```

## 147 — mutated `exp_only_two_arm_normal_path` expectation to result=0 / variant=0

```
=== 147 - Holdout Arms - Union Three-Arm Variant 1 Holds Out While Applicable Two-Arm Does Not (uid e791e240fcd3df7d238cfc285f475e8152fcc0ec)
  java                 ✗ FAIL (2 failures)
      {'step': 2, 'action': 'treatment', 'field': 'result', 'expected': 0, 'actual': 1}
      {'step': 2, 'action': 'treatment', 'field': 'events[0].data.variant', 'expected': 0, 'actual': 1}
```

In both cases the real assigner output is `1`, not `0` — the mutated (suppressing) expectation
fails, confirming the new assertions are load-bearing and would catch a wrongly-suppressing
implementation of the non-suppressing holdout arm.

After recording this output, both files were reverted to their correct expectations: `result: 1` /
`variant: 1` for 146's `exp_only_three_arm_normal_path`, and `result: 1` / `variant: 1` for 147's
`exp_only_two_arm_normal_path` — the real assigner output for each, confirmed directly against the
live `java` SDK (`POST /context/{id}/treatment`) before the mutation and matched again after
restoring.

The full suite was re-run after restoring and reproduced the exact pre-existing baseline: 88 pass /
8 fail (scenarios 08, 17, 19, 43, 44, 66, 130, 140 — all pre-existing, none touched), with
132-139 and 141-147 all passing.
