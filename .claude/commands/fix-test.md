---
description: Diagnose and fix a test failure from its log.
argument-hint: <the test failure log>
disable-model-invocation: true
---

Fix this test failure:

```
$ARGUMENTS
```

Tasks:
1. Study the plan.
2. Study the changes.
3. Study the test failure.
4. Study the failing test.
5. Study the system under failing test.
6. Study relevant code, data, tests, and documentation.
7. Identify the root cause of the issue. There may be multiple root causes.
8. For each discrete root cause:
    - Reason through at least 3 solutions in your thinking.
    - Report ONLY the chosen solution, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the fix is architectural, hard to reverse, or you are genuinely uncertain.
    - Never invent an alternative you would not seriously choose.
9. Apply the chosen solutions.
10. Re-run the failing test, then the full suite, to confirm the fix.

Rules:
- Edit the plan as needed. Make sure the plan file is not left stale.
- Fix the root cause, not the symptom. Do not weaken, skip, or delete the test to make it pass. If the test itself is wrong, say so explicitly and explain why before changing it.
