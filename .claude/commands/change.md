---
description: Apply a change request after the implementation has been done.
argument-hint: <the change to make>
disable-model-invocation: true
---

Perform the following change:

$ARGUMENTS

Tasks:
1. Study the plan.
2. Study the changes.
3. Study relevant code, data, tests, and documentation.
4. Identify all discrete requirements in my change request.
5. For each discrete requirement in the change request:
    - Reason through at least 3 solutions in your thinking.
    - Report ONLY the chosen solution, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the decision is architectural, hard to reverse, or you are genuinely uncertain.
    - If the project already has a dominant idiom for this, that IS the decision. State it in one line, cite the file that establishes it, and move on.
    - Never invent an alternative you would not seriously choose.
6. Create, amend, or remove code, tests, and documentation according to the chosen solutions.
7. Run the relevant tests and `ruff check`. Fix every failure this work caused.
8. At the end, report the verification results, then a detailed, step-by-step list of the manual steps the user must perform outside the repository, such as setting environment variables, obtaining API keys, starting Docker services, or downloading data. Fold repetitive or mechanical steps into a script or management command and name it in the list. If there are none, say so.

Rules:
- Edit the plan as needed. Make sure the plan file is not left stale.
