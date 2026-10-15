---
description: Diagnose and fix a bug from a description.
argument-hint: <the bug description>
disable-model-invocation: true
---

Fix this bug:

$ARGUMENTS

Tasks:
1. Study relevant code, data, tests, and documentation.
2. Identify all discrete bugs in my request.
3. For each discrete bug, identify the root cause of the issue. There may be multiple root causes.
4. For each discrete root cause:
    - Reason through at least 3 solutions in your thinking.
    - Report ONLY the chosen solution, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the fix is architectural, hard to reverse, or you are genuinely uncertain.
    - Never invent an alternative you would not seriously choose.
5. Write a test that fails because of each bug before fixing it.
6. Create, amend, or remove code, tests, and documentation according to the chosen solutions.
7. Run the new tests, the surrounding test modules, and `ruff check` to confirm the fix.
8. At the end, report the verification results, then a detailed, step-by-step list of the manual steps the user must perform outside the repository, such as setting environment variables, obtaining API keys, starting Docker services, or downloading data. Fold repetitive or mechanical steps into a script or management command and name it in the list. If there are none, say so.

Rules:
- Fix the root cause, not the symptom.
