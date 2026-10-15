---
description: Explore a dataset or data source and record what it contains and how it misbehaves. Exploration only, no pipeline code.
argument-hint: <the dataset, file, or API to explore, and the question to answer>
disable-model-invocation: true
---

Explore this data source:

$ARGUMENTS

Tasks:
1. Read `Docs/Domain/DataSources.md` and the `childcare-data` skill.
2. Obtain a sample: a file already under `data/`, or a modest read-only API request of no more than a few thousand rows. Save any downloaded response under `data/raw/<source>/` before analysing it. Keep throwaway analysis scripts under `scratch/`.
3. Profile it: columns and their types, row count, identifiers and whether they are unique and stable, missing values, the distribution of each categorical column, malformed values, geographic coverage, and overlap with the sources already documented.
4. Answer the question in my request, with numbers.
5. Record the durable facts in `Docs/Domain/DataSources.md`: what the source is, how to access it, its columns, and its quirks. Leave out counts that will drift as the source updates.
6. Report the findings, then the open questions and the decisions I have to make, each with your recommendation.

Rules:
- Do not write pipeline or application code. This is exploration.
- If the source contains names, emails, phone numbers, or notes about real people, report only aggregates and never copy those values into documentation or the report.
