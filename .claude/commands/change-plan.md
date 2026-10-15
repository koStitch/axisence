---
description: Apply a change request to the existing plan.
argument-hint: <the change to make to the plan>
disable-model-invocation: true
---

Perform the following change on the plan:

$ARGUMENTS

Tasks:
1. Study the plan.
2. Study relevant code, data, tests, and documentation.
3. Identify all discrete requirements in my change request.
4. For each discrete requirement:
    - Reason through at least 3 solutions in your thinking.
    - Record ONLY the chosen solution in the plan, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the decision is architectural, hard to reverse, or you are genuinely uncertain. Put that comparison in the Decision Record document, never in the plan.
    - If the project already has a dominant idiom for this, that IS the decision. State it in one line, cite the file that establishes it, and move on.
    - Never invent an alternative you would not seriously choose.
5. Amend the plan according to the chosen solutions.

Only for the planning agent:
- Write the plan such that the implementation agent should not have to guess or make decisions.
- Follow the document authoring conventions in `CLAUDE.md`.
- Do not create a new plan file, work on the existing one you just created.
- Do not move the plan file somewhere else, leave it where it is.

Do not change anything other than the plan document, and the Decision Record document if one exists and is affected.
