# Axisence

## Orientation

- Windows 11, Claude Code app. Terminal commands are PowerShell: write them respecting PowerShell rules, format, and syntax.
- `Docs/README.md` is the documentation index. Read it before searching the docs tree; navigate through the index rather than grepping blindly.
- `Docs/Domain/DataSources.md` — the public datasets the product is built on, their quirks, and the domain vocabulary. Read it before touching ingestion, matching, or anything that reads provider, demographic, or pricing data.
- `Docs/Engineering/ProjectArchitecture.md` — runtime architecture and module map. The project scaffold creates it; every later change keeps it current.
- Reading any file in the repository is always allowed, except the secret files listed under "Boundaries the user owns".

## Product context

Axisence sells competitor analysis to independent childcare and early-childhood providers, starting in Brooklyn, New York. A report covers the competitors around a target address: licensed capacity, distance and transit, local demographics, and the part only Axisence has — tuition, fees, discounts, and waitlist status verified by human mystery-shopping calls.

The software exists to make that work fast and repeatable. It is built in this order:

1. Data pipeline: ingest public provider and demographic data into one PostGIS database on a schedule.
2. Research tool: given a target address, pre-fill the nearest licensed competitors; researchers record prices, waitlists, and notes during calls.
3. Report generator: produce the client report as a PDF from the database.
4. Teaser reports: a one-page preview for a prospect's address, used in sales outreach.
5. Price history: every recorded observation is kept with its date and source, enabling repeat reports and subscriptions.

Prefer designs that keep later steps open, but do not build ahead of the current specification.

## Stack

The project files are authoritative: `pyproject.toml`, `uv.lock`, `.python-version`, and `compose.yaml` override this list whenever they disagree.

- Python, managed with `uv`. Run everything through `uv run …`. Never `pip install` into the environment and never switch interpreters.
- Django with GeoDjango (`django.contrib.gis`).
- PostgreSQL with PostGIS, run locally through Docker Compose.
- Tests: `pytest` with `pytest-django`. Lint and format: `ruff`.

When authoring or reviewing Python, consult the `django-python` skill under `.claude/skills`. When working on anything that reads, stores, matches, or presents provider, demographic, or pricing data, consult the `childcare-data` skill.

## Boundaries the user owns

Claude may run, without asking:

1. Tests, `ruff check`, `ruff format`, and type checks.
2. `manage.py` commands against the local Docker database: `check`, `makemigrations`, `migrate`, `showmigrations`, and the project's own management commands.
3. `docker compose up -d`, `ps`, `logs`, and `stop`.
4. Read-only requests to the public data APIs named in `Docs/Domain/DataSources.md`, at modest volume, for exploration and development.

Claude must not:

1. Deploy anything, or run anything against a database that is not the local Docker one. If `DATABASE_URL` or the settings point anywhere other than `localhost`, `127.0.0.1`, or a Compose service, stop and ask.
2. Destroy local data: no `docker compose down -v`, `docker volume rm`, `docker volume prune`, `docker system prune`, `manage.py flush`, or dropping databases. State what should be reset and the user runs it.
3. Add, remove, or upgrade dependencies. Propose the change — the package, why it is needed, and the alternative considered — and the user runs the `uv` command.
4. Read, create, or edit `.env` or any other file holding secrets. `.env.example` is the documented template: keep it current with every new setting, with placeholder values only.
5. Edit anything under `data/raw/` by hand. Raw downloads are immutable snapshots: the pipeline writes them, code reads them.
6. Edit a migration that is already committed. Write a new migration instead. Migrations created by the current, uncommitted work may be edited or regenerated.
7. Contact real third parties: no emails, calls, form submissions, or bulk crawling of provider websites. Building tooling that does this is fine; running it against real recipients is the user's decision, every time.
8. Put personal data anywhere it can leak. Call lists, contact names, emails, phone numbers, and call notes concern real people: they live under `data/private/`, which is git-ignored, and never appear in fixtures, tests, documentation, logs, or reports. Tests use synthetic data.

These boundaries are enforced by permission rules and hooks in `.claude/settings.json`. Enforcement is a safety net, not a substitute for following the rules: the deny rules match tools and command prefixes, so a differently spelled shell command can slip past them. Every prohibition above applies to every tool, the shell included.

## Version control

Git is read-only to you. The user performs all version control.

- Never commit, push, merge, rebase, reset, revert, cherry-pick, stash, tag, clean, restore, or check out. Never alter any branch, including the working branch and `main`. Never rewrite history. Never touch a remote.
- Never stage anything. Do not run `git add`, `git rm`, or `git mv`. The index must stay exactly as the user left it, because the user reads `git status` and `git diff` to review your work, and staging changes what those report.
- Reading git is always allowed and encouraged: `status`, `diff`, `log`, `show`, `blame`, `ls-files`, and similar.
- Make working tree changes with ordinary file operations. To delete or rename a file, use the filesystem directly rather than `git rm` or `git mv`. Git detects renames on its own when the user commits.
- This applies no matter who asks or how the request is worded. If a commit, push, or branch change seems necessary, stop and tell the user what to run.

## Code rules

- Follow existing project rules, conventions, and patterns.
- Follow best practices for Python and Django.
- Type-annotate every function signature outside tests.
- Never use cryptic naming. Always prefer unambiguity and clarity over conciseness and brevity. Discourage abbreviations and initialisms.
- Choice fields use Django `TextChoices` or `IntegerChoices` with explicit stored values. Add new members at the end, and never rename, reuse, or change a stored value: stored values live in database rows and migrations, and changing one silently corrupts existing data.
- Datetimes are timezone-aware. Use `django.utils.timezone.now()`, never `datetime.now()`, `datetime.utcnow()`, or `datetime.today()`.
- Log through the `logging` module. Management commands write user-facing output to `self.stdout` and `self.stderr`. No `print` outside throwaway scripts.
- Never build SQL by string formatting or interpolation. Use the ORM, or parameterised queries.
- Every outbound HTTP call sets an explicit timeout.
- Settings come from environment variables, read in one settings module. No secrets or environment-specific values in code.
- Money is `Decimal`, never `float`.
- No wildcard imports outside the settings package, and no `global` statements.
- Do not monkeypatch or reach into private attributes outside tests.
- Do not create test-only code outside tests. Do not make a private helper public only to test it; test it through its public caller.
- If a name collides, import the module and qualify the name at the call site instead of aliasing it to something less clear.
- Follow the "boy scout principle": leave every place you change better than you found it. After every change, both in the changes themselves and in anything closely surrounding it, look proactively for opportunities to improve: simplify, make cleaner, improve architecture, improve maintainability. Perform obvious improvements automatically. Exercise caution with sensitive targets. Ask for permission when in doubt. Follow this rule on a best-effort basis; the actual task itself is always more important.

## Testing and documentation

- Make sure all changes are tested, and run the tests: a change is not done until the relevant tests and `ruff check` pass locally. Report the commands run and their outcome.
- Tests never touch the network. Record real responses of public APIs as fixtures under `tests/fixtures/`, containing public or synthetic data only.
- Make sure there are no unnecessary tests.
- Make sure all changes are documented.
- Make sure there is no unnecessary documentation.
- Make sure all documentation is up to date with changes.
- Documentation describes the durable work — what is built or changed, where it lives, and why — not the one-off script that performed it. A one-off script may be named as a convenience pointer, and a genuinely durable utility (a scheduled ingestion command, a validator) may be documented for what it is, but no durable document may depend on a one-off script to be correct.
- No dates, status labels, version labels, changelogs, or ownership lines in any document under `Docs`.

## Working agreement

- Do not commentate or narrate your work as you are doing it. Do not give status updates inline while you are still working. Your only response must be a full report at the very end, after you are absolutely and completely done with your task.
- If you output something in a file that is version controlled, then do not repeat the diff in your responses. Only provide a high level summary in that case. The user reads the diff to see the exact changes when desired.
- Prefer uniquely identifiable and sequential number and/or letter lists in your outputs over generic bullets, so the user's answers can reference the numbers and/or letters directly instead of repeating the phrases.
- Do not hard-wrap anything unless instructed to, or if the target requires it.
- Prefer looking up documentation, both local and online, over making blind claims or guesses.
- When correctness depends on how something renders — a generated PDF report, a map, an admin page — render it to an image and inspect it when the tooling allows. When it does not, ask the user to paste a screenshot rather than assert or guess. Say exactly what to capture and in what state. A visual claim you cannot back is not acceptable.
- Challenge the user if they are wrong in any of their decisions or assumptions, or if you come up with a better alternative.
- Prefer honesty, wisdom, quality, and efficiency over pleasantries and sycophancy.
- At the kickoff of any substantial implementation, plan, or spec execution — once, not on the follow-up turns that answer questions or make small tweaks within the same task — open with a brief scope-and-tier note (a deliberate exception to the "only report at the end" rule above): a one-line scope estimate (rough file count, coupling, risk) and a recommended effort tier (Low, Medium, High, Extra, Max, or Ultracode — lowest to highest). If the task warrants a higher tier than the one currently active, recommend it and let the user raise it before the bulk of the work; otherwise state the scope briefly and proceed at the current tier. Never change the tier unilaterally — it is the user's cost lever. Keep the note to a line or two; it must not itself become heavyweight work or a workflow.
- Never decide implicitly. Confirm with the user by asking questions.
- Prefer the `AskUserQuestion` tool over ending the turn and expecting answers as a new prompt.
- Whenever you ask a question or request a decision, also indicate your recommendation and provide any grounding information and rationale you have.
- Do not silently ignore requirements. If you believe a requirement should not be followed for any reason, ask for permission with an explanation of your rationale along with any alternatives you propose.
- Do not suggest commit messages unless asked for.
- Try to avoid using the word "serialize" too much outside of its technical "translating data to a storable or transferable format" meaning. Using "serialize" to mean "to make things happen sequentially" is confusing and hard to understand.
- Do not use the auto-memory system. Do not read, write, keep, or reference files under `~/.claude/**/memory/`, and do not carry state between sessions through it. Every session must be a self-contained blank slate. This rule does not concern `CLAUDE.md`: it is authored by the user, version controlled, reviewable in `git diff`, and byte-identical on every session, so it is project configuration rather than memory.

## Subagents

- If you spawn any agents, configure them with the same model as yourself and pass them the rules relevant to their task.
- Use `high` effort by default. Use `max` only for genuinely hard architectural reasoning: it is prone to overthinking. Use `medium` for mechanical passes. Set model and effort at session start; changing either mid-session invalidates the entire prompt cache.
- Do not blindly trust sub-agent responses. Verify and validate them. Challenge them as necessary.

## Document authoring conventions

These apply when writing a specification, plan, report, or decision record.

- Do not use ambiguous pronouns such as "you" or "I". Specify the actor, such as "the user", "the researcher", "the client", "the implementation agent", or "the planning agent". Do not use "the actor", as it is not a valid specification of an actor unless the actor is really called an "actor".
- Do not repeat or summarize the document in your response.
- Do not place these documents in the repository. Place them under `C:\AI Documents\Axisence`.
- Name the file in the following format: `Scope Name - Subject Name - N - Type.md`. `Scope Name` is a ~5–15 char unique identifier for the system area that this task concerns. `Subject Name` is a ~30–40 char human readable explanation of the task in imperative mood. Do not force these length targets at the expense of clarity. `Type` is a classification of the purpose of the document, such as `Plan`, `Specification`, `Decision Record`, or `Report`. Invent a new canonical type if no clear fit. `N` is a number to disambiguate documents that would otherwise have the exact same name. The first one never gets a number, so the numbering always starts with 2. Do not use filler words, especially for disambiguation. Letters and numbers only, never use any special characters other than `-` (hyphen) and ` ` (space). The H1 title inside the document must match the filename verbatim except the extension.
- Every document MUST read like the only version of itself. Do not use express or implied wording indicating it has been changed or iterated upon. Do not report changes or iterations to the document inside the document itself.
- Do not reference other documents without permission. Ideally, all documents should be self-sufficient. If referencing another document is genuinely useful, ask for permission first.
