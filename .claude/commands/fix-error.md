---
description: Diagnose and fix an error or traceback.
argument-hint: <the error message or traceback>
disable-model-invocation: true
---

Fix this error:

```
$ARGUMENTS
```

Tasks:
1. Study the plan.
2. Study the changes.
3. Study the error and its traceback.
4. Study the code that raises or logs the error.
5. Study relevant code, data, tests, and documentation.
6. Identify the root cause of the issue. There may be multiple root causes.
7. For each discrete root cause:
    - Reason through at least 3 solutions in your thinking.
    - Report ONLY the chosen solution, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the fix is architectural, hard to reverse, or you are genuinely uncertain.
    - Never invent an alternative you would not seriously choose.
8. Reproduce the error first where feasible, preferably as a failing test.
9. Apply the chosen solutions.
10. Confirm the error is gone and run the surrounding tests and `ruff check`.

Rules:
- Edit the plan as needed. Make sure the plan file is not left stale.
- Fix the root cause, not the symptom. Silencing or swallowing the error is not a fix.
