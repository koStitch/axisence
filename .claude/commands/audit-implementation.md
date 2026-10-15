---
description: Final audit pass on the implementation work.
disable-model-invocation: true
---

Perform a deep and thorough final review, audit, and iteration on the work you have done.

Including, but not limited to:
- Specification drift: work that differs from the plan or the specification, work from the plan or specification that has not been performed, etc.
- Test drift: Is everything covered by tests? Are there any outdated tests?
- Documentation drift: Is everything covered in documentation? Is there any outdated documentation?
- Simplification opportunities: Did these changes surface any opportunities to make simplifications, either to these changes or to anything that closely surrounds them?
- Data safety: personal data, secrets, and raw snapshots are handled as `CLAUDE.md` requires.
- Deep flow: Is everything wired up as it should be? Are happy paths OK? Are unhappy paths OK?

Tasks:
1. Study the plan.
2. Study the changes.
3. Study relevant code, data, tests, and documentation.
4. Review and audit the changes.
5. Identify correctness gaps, drift from the plan or specification, and missing or outdated tests and documentation.
6. Create, amend, or remove code, tests, and documentation according to what you found.
7. Run the full test suite and `ruff check`. Fix every failure this work caused.
8. Report the verification results, then a detailed, step-by-step list of the manual steps the user must perform outside the repository, such as setting environment variables, obtaining API keys, starting Docker services, or downloading data. Fold repetitive or mechanical steps into a script or management command and name it in the list. If there are none, say so.

Calibration:
- Report only correctness gaps, drift from the plan or specification, missing or outdated tests and documentation, or a real simplification. A reviewer told to find opportunities will always find some, even when the work is sound. That is a failure mode, not thoroughness.
- If the work is sound, say so and stop. Do not manufacture improvements to justify the pass. "No changes needed" is a complete and welcome answer.
- Do not churn working code for stylistic preference. The boy scout principle asks you to leave a place better than you found it, not different from how you found it.

Rules:
- Do not limit your scope to our recent conversations. This is a general, all-purpose pass that encompasses our entire conversation and your entire work.
