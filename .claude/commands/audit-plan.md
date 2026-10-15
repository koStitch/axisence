---
description: Final audit pass on the plan before handing it to an implementation agent.
disable-model-invocation: true
---

Perform a deep and thorough final iteration on the plan you produced before I hand it off to an implementation agent. Including, but not limited to specification drift: where the plan differs from the specification, requirements that have not been included in the plan, etc.

Tasks:
1. Study the plan.
2. Study relevant code, data, tests, and documentation.
3. Review and audit the plan.
4. Identify correctness gaps, specification drift, requirements the plan has missed, and verification commands that would not actually prove the work.
5. Amend the plan according to what you found.

Calibration:
- Report only correctness gaps, requirement drift, specification violations, or a decision the plan leaves genuinely ambiguous for the implementation agent. A reviewer told to find opportunities will always find some, even when the work is sound. That is a failure mode, not thoroughness.
- If the plan is sound, say so and stop. Do not manufacture improvements to justify the pass. "No changes needed" is a complete and welcome answer.
- Cosmetic rewording, reorganisation, and elaboration are not findings. Do not spend the plan's readability budget on them.
- If the plan carries prose that the implementation agent does not need in order to act, that is a finding: cut it.

Only for the planning agent:
- Write the plan such that the implementation agent should not have to guess or make decisions.
- Follow the document authoring conventions in `CLAUDE.md`.
- Do not create a new plan file, work on the existing one you just created.
- Do not move the plan file somewhere else, leave it where it is.

Do not change anything other than the plan document.
