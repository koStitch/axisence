# Axisence Documentation

The index of project documentation. Read this before searching the docs tree, and add every new document here.

| Document | Covers |
| --- | --- |
| [Domain/DataSources.md](Domain/DataSources.md) | The public provider, demographic, and transit datasets; their columns and quirks; how providers are normalized, grouped into competitors, flagged for review, and geocoded; the single-address lookup of target addresses; Census demographics and tabulation-area boundaries; mystery-shopping observations: contacts, observations, slots, and current values; stated values and the call sheet's history; the duplicate-definition rule; how reports present prices, the tuition comparison, and Census figures; how repeat reports compare with their compared report; what teasers show; MTA subway and bus feeds, transit stops, station groups, lines, and weekday daytime service; how reports present transit access; domain vocabulary. |
| [Engineering/ProjectArchitecture.md](Engineering/ProjectArchitecture.md) | Runtime architecture: the host and container split, Compose services, package map, settings and environment variables; the data pipeline's flow, raw snapshot layout, and run records; the transit pipeline and stop list; the research tool's pages, permissions, and logging rules; the report and teaser generator, its storage, rendering, and permissions; the subscriptions app: repeat jobs, their preparation, due dates, and permissions; commands, and the test and type-checking setup. |
