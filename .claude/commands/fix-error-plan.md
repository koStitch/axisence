---
description: Plan the fix for an error or traceback, without applying it.
argument-hint: <the error message or traceback>
disable-model-invocation: true
---

Fix this error:

```
$ARGUMENTS
```

Tasks:
1. Study the error and its traceback.
2. Study the code that raises or logs the error.
3. Study relevant code, data, tests, and documentation.
4. Identify the root cause of the issue. There may be multiple root causes.
5. For each discrete root cause:
    - Reason through at least 3 solutions in your thinking.
    - Record ONLY the chosen solution in the plan, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the fix is architectural, hard to reverse, or you are genuinely uncertain. Put that comparison in the Decision Record document, never in the plan.
    - Never invent an alternative you would not seriously choose.
6. Create, amend, or remove code, tests, and documentation according to the chosen solutions.
7. Include in the plan the verification commands that prove the work, and the manual steps the user must perform outside the repository.
8. Include a summary at the top of the plan.

The plan document must contain, in this order:
1. Summary.
2. Study findings: the context the implementation agent must rely on, with exact file paths, including the root cause and the evidence for it.
3. File manifest: the exact list of files the implementation agent must read, and nothing more.
4. The root causes, each with its locked fix stated in one line.
5. Deliverables: the code, tests, and documentation the implementation agent writes, by exact path.
6. Verification: the exact commands the implementation agent runs to prove the work, and the expected outcome of each.
7. Manual steps: the step-by-step list of things the user must do outside the repository, such as environment variables, API keys, Docker services, or data downloads. Repetitive or mechanical steps are folded into a script or management command named here. "None" when there are none.
8. Out of scope, risks, and assumptions.

Only for the planning agent:
- Write the plan such that the implementation agent should not have to guess or make decisions.
- Follow the document authoring conventions in `CLAUDE.md`.
- The `Type` for the plan document is `Plan`.
- If, and only if, any decision met the bar in task 5 for a full comparison, write those comparisons to a second document with `Type` of `Decision Record`, sharing the same `Scope Name` and `Subject Name` as the plan. If no decision met the bar, do not create it.

Do not change anything other than the new plan document, and the Decision Record document if one is warranted.
