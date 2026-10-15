---
description: Plan a deep review and audit of the current branch before merging it to main.
argument-hint: <branch name>
disable-model-invocation: true
---

I will merge the branch `$ARGUMENTS` to `main`. Perform a deep and thorough final review, audit, and iteration work on the branch.

Tasks:
1. Study the changes.
2. Study relevant code, data, tests, and documentation.
3. Review and audit the changes.
4. Identify correctness gaps, drift from the plan or specification, missing or outdated tests and documentation, and real consolidation opportunities.
5. For each finding:
    - Reason through at least 3 solutions in your thinking.
    - Record ONLY the chosen solution in the plan, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Expand into a full comparison only when the decision is architectural, hard to reverse, or you are genuinely uncertain. Put that comparison in the Decision Record document, never in the plan.
    - Never invent an alternative you would not seriously choose.
6. Create, amend, or remove code, tests, and documentation according to the chosen solutions.
7. Include in the plan the verification commands that prove the work, and the manual steps the user must perform outside the repository.
8. Include a summary at the top of the plan.

Calibration:
- Report only correctness gaps, drift, missing or outdated tests and documentation, or a real consolidation. A reviewer told to find opportunities will always find some, even when the work is sound. That is a failure mode, not thoroughness.
- If the branch is sound, say so and stop. An empty plan is a complete and welcome answer.
- Do not churn working code for stylistic preference on the eve of a merge.

The plan document must contain, in this order:
1. Summary.
2. Study findings: the context the implementation agent must rely on, with exact file paths.
3. File manifest: the exact list of files the implementation agent must read, and nothing more.
4. The findings, each with its locked resolution stated in one line.
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
