# Ratchet learnings

Every Ratchet agent reads this file before starting work. Entries come from human reviews and repeated gate failures.

- (2026-09-29, gate:adversarial) Adversary reviewers must write their findings JSON file themselves with the Write tool (even when approving); returning text only stalls the round.
- (2026-09-29, gate:adversarial) Do not gate user-consent on keyword heuristics; use an exact-phrase allow-list printed in the question.
- (2026-10-04, human) Accepted limit (user decision, CP1 of the trust-holes run): Ratchet does not promise to record a hook crash when the crash-record folder is not a real folder (for example a broken symlink) and every fallback write also fails at that moment. This is documented in schemas.md; reviewers should not block on that case.
