---
description: Implement the plan produced earlier.
disable-model-invocation: true
---

Implement according to the plan.

Tasks:
1. Study the plan.
2. Read the files named in the plan's file manifest. Rely on the plan's study findings rather than rediscovering them. Explore beyond the manifest only where the plan proves insufficient, and say so in your report when that happens.
3. Create, amend, or remove code, tests, and documentation according to the plan.
4. Run the plan's verification commands, the full test suite, and `ruff check`. Fix every failure this work caused. Report, without fixing, any failure it did not cause.
5. Report the verification results, then a detailed, step-by-step list of the manual steps the user must perform outside the repository, such as setting environment variables, obtaining API keys, starting Docker services, or downloading data. Fold repetitive or mechanical steps into a script or management command and name it in the list. If there are none, say so.

Do not read the Decision Record document if one exists. The plan already carries every decision you need.
