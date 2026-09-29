# Ratchet learnings

Every Ratchet agent reads this file before starting work. Entries come from human reviews and repeated gate failures.

- (2026-09-29, gate:adversarial) Adversary reviewers must write their findings JSON file themselves with the Write tool (even when approving); returning text only stalls the round.
- (2026-09-29, gate:adversarial) Do not gate user-consent on keyword heuristics; use an exact-phrase allow-list printed in the question.
