---
description: Create the implementation plan from the specification produced earlier.
disable-model-invocation: true
---

Create the plan according to the specification.

Tasks:
1. Study relevant code, data, tests, and documentation. Start from `Docs/README.md` rather than searching the docs tree blindly.
2. Study the specification.
3. Identify all discrete requirements in the specification.
4. For each discrete requirement in the specification, spell out the locked decision and rationale.
5. For any requirement that is not part of the specification, including but not limited to implementation details:
    - Reason through at least 3 solutions in your thinking.
    - In the plan, record ONLY the chosen solution, plus a one-line rejection note naming each discarded option and the single reason it lost.
    - Write the full comparison with pros, cons, and rationale ONLY when the decision is architectural, hard to reverse, or you are genuinely uncertain between the candidates. When you do, it goes in the Decision Record document, never in the plan.
    - If the project already has a dominant idiom for this, that IS the decision. State it in one line, cite the file that establishes it, and move on.
    - Never invent an alternative you would not seriously choose. A "con" that you would describe as not a real cost means the option was never a candidate: leave it out entirely.
6. Create, amend, or remove code, tests, and documentation according to the chosen solutions.
7. Include in the plan the verification commands that prove the work, and the manual steps the user must perform outside the repository.
8. Include a summary at the top of the plan.

The plan document must contain, in this order:
1. Summary.
2. Study findings: the context the implementation agent must rely on, with exact file paths and the established idioms it must follow. This section is the most valuable part of the plan. It is cached exploration: everything you learned so the implementation agent does not have to rediscover it. Be specific and concrete.
3. File manifest: the exact list of files the implementation agent must read, and nothing more. The implementation agent reads these instead of exploring.
4. Discrete requirements, each with its locked decision stated in one line.
5. Deliverables: the code, tests, and documentation the implementation agent writes, by exact path.
6. Verification: the exact commands the implementation agent runs to prove the work, and the expected outcome of each.
7. Manual steps: the step-by-step list of things the user must do outside the repository, such as environment variables, API keys, Docker services, or data downloads. Repetitive or mechanical steps are folded into a script or management command named here. "None" when there are none.
8. Out of scope, risks, and assumptions.

Only for the planning agent:
- Write the plan such that the implementation agent should not have to guess or make decisions.
- Follow the document authoring conventions in `CLAUDE.md`.
- The `Type` for the plan document is `Plan`.
- If, and only if, any decision met the bar in task 5 for a full comparison, write those comparisons to a second document with `Type` of `Decision Record`, sharing the same `Scope Name` and `Subject Name` as the plan. The implementation agent never reads the Decision Record; it exists for me and for audit. If no decision met the bar, do not create it.

Do not change anything other than the new plan document, and the Decision Record document if one is warranted.
