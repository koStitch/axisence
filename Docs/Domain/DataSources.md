# Data Sources

The product combines public data about licensed childcare providers and neighborhoods with proprietary observations collected by Axisence researchers. Public data says who exists, where, and how many children they may serve. Observations say what they charge and whether they have room.

## Licensed providers in New York

New York regulates childcare through two agencies, and the split matters: no single dataset lists every provider in New York City.

| Provider kind | Regulator | Dataset |
| --- | --- | --- |
| Day care centers in New York City | NYC Department of Health and Mental Hygiene (Bureau of Child Care) | NYC Health dataset |
| School-based child care in New York City | NYC Department of Health and Mental Hygiene | NYC Health dataset |
| Family and group family day care, school-age child care, in New York City | NY State Office of Children and Family Services | NY State dataset |
| Every regulated program outside New York City | NY State Office of Children and Family Services | NY State dataset |

### NY State dataset: Child Care Regulated Programs

Published daily by the NY State Office of Children and Family Services on data.ny.gov, dataset identifier `cb42-qumz` ([catalog entry](https://catalog.data.gov/dataset/child-care-regulated-programs)). One row per regulated program, statewide: about 16,800 rows.

Access:

1. Full CSV export, with display names as headers: `https://data.ny.gov/api/views/cb42-qumz/rows.csv?accessType=DOWNLOAD`.
2. Metadata, including the last update time `rowsUpdatedAt` in Unix seconds: `https://data.ny.gov/api/views/cb42-qumz.json`. The metadata lists three computed region columns, `NYS Municipal Boundaries 2`, `New York Zip Codes 2`, and `Counties 2`, that the export does not contain.

Columns: `Facility ID`, `Program Type`, `Region Code`, `County`, `Facility Status`, `Facility Name`, `Facility Opened Date`, `License Issue Date`, `License Expiration Date`, `Address Omitted`, `Street Number`, `Street Name`, `Additional Address`, `Floor`, `Apartment`, `City`, `State`, `Zip Code`, `Phone Number Omitted`, `Phone Number`, `Phone Extension`, `Provider Name`, `School District Name`, `Capacity Description`, `Infant Capacity`, `Toddler Capacity`, `Preschool Capacity`, `School Age Capacity`, `Total Capacity`, `Program Profile`, `Latitude`, `Longitude`, `Georeference`.

Program types:

| Code | Meaning |
| --- | --- |
| `DCC` | Day Care Center |
| `SDCC` | Small Day Care Center |
| `GFDC` | Group Family Day Care, run in a residence |
| `FDC` | Family Day Care, run in a residence |
| `SACC` | School-Age Child Care |

Quirks:

1. New York City rows (`Region Code` `NYCDOH`) contain only `GFDC`, `FDC`, and `SACC` programs. Day care centers in New York City are absent because the city regulates them; they come from the NYC Health dataset.
2. `Facility ID` is the stable identifier and the upsert key.
3. Capacity columns can contain thousands separators (`1,100`). Parse them as text, strip separators, then convert.
4. `Capacity Description` is free text. The numeric capacity columns are authoritative.
5. Some rows have no `Latitude` and `Longitude`; they need geocoding from the address.
6. `Address Omitted` and `Phone Number Omitted` mark providers, usually home-based, whose address or phone is withheld. Respect the omission: never geocode, display, or try to recover an omitted address or phone number.
7. Facility names carry stray quotation marks (`"""Almost Home Child Care"""`, `"LITTLE GENIUS" DAY CARE`). Keep the original and derive a cleaned display name and a normalized matching key.
8. Dates are `MM/DD/YYYY`.
9. `Facility Status` includes `License`, `Registration`, `Pending Revocation`, `Pending Revocation and Denial`, `Suspended`, and blank values.
10. Free-text fields are inconsistently cased and spelled (`Long Island CIty`).
11. In New York City rows, `County` holds the borough name (`Brooklyn`, `Manhattan`, `Staten Island`, `Bronx`, `Queens`), not the county name (`Kings`, `New York`, `Richmond`).
12. `Zip Code` holds five digits in most rows, nine digits run together (`122071413`) in a few hundred, and rarely an invalid value such as a four-digit code.
13. `Street Number` holds digits, Queens-style hyphenated numbers (`35-59`, `5-36`), letters (`12A`, `12-A`, `12 A`), fractions (`12 1/2`), direction words (`425 W`, `12 East`), and rarely other text (`Lot 4`, `*`, `-`).
14. `Street Name` sometimes carries suite, building, floor, P.O. box, or city text, mostly after a comma (`, SUITE 3H`, `, BLDG. A`, `, P.O. BOX 404`, `, ALBANY, NY`) and sometimes without one (`Suite 2`, `ROOM 132`). Runs of double spaces are common.
15. Rows with an omitted address have a blank street and blank coordinates, but keep `City` and `Zip Code`.
16. `Phone Number` is formatted `(999)999-9999` or blank. `Phone Extension` holds digits, and rarely filler such as `__________` or a word.
17. Names contain U+2019 RIGHT SINGLE QUOTATION MARK in place of an apostrophe, and occasional accented letters.
18. `Georeference` repeats `Latitude` and `Longitude` as `POINT (<longitude> <latitude>)`, and is not read.
19. `Program Profile` is the program's public OCFS profile page, `https://hs.ocfs.ny.gov/dcfs/Profile/Index/<Facility ID>`.
20. A row can publish `0` for both `Latitude` and `Longitude`, a placeholder for unknown coordinates. The point lies outside the state, so ingestion stores no coordinates, with a field warning, and geocodes the published address.

### NYC Health dataset: Active NYC Health Code Regulated Child Care Programs

Published daily by the NYC Department of Health and Mental Hygiene on NYC Open Data, dataset identifier `gy3q-4tzp` ([dataset page](https://data.cityofnewyork.us/Health/Active-NYC-Health-Code-Regulated-Child-Care-Progra/gy3q-4tzp)), under a public domain license. One row per permitted program, citywide.

Access:

1. Rows as JSON through the Socrata API: `https://data.cityofnewyork.us/resource/gy3q-4tzp.json`, filtered and aggregated with SoQL parameters (`$where`, `$select`, `$group`, `$order`). A request returns 1,000 rows unless `$limit` says otherwise, so a full read sets `$limit` above the row count or pages with `$offset` under a stable `$order`. A blank value is an absent key, not an empty string.
2. Full CSV export, with display names as headers: `https://data.cityofnewyork.us/api/views/gy3q-4tzp/rows.csv?accessType=DOWNLOAD`. It holds the same values as the API.
3. Metadata, including column definitions and the last update time (`rowsUpdatedAt`): `https://data.cityofnewyork.us/api/views/gy3q-4tzp.json`.

Columns. Every column is text in the API except `latitude` and `longitude`, which are numbers.

| Column | API field | Content |
| --- | --- | --- |
| `DCID` | `dcid` | Program identifier: `DC` followed by two to five digits. |
| `Permit Number` | `permit_number` | Health Department permit number for a group child care program; `N/A` for every school-based program. |
| `Program Name` | `program_name` | Name of the program. |
| `Facility Type` | `facility_type` | `GCC` (Group Child Care, Article 47) or `SBCC` (School-Based Child Care, Article 43). |
| `Program Type` | `program_type` | Age group served: `INFANT TODDLER` or `PRESCHOOL`, with rare one-off values such as `P/S Night Care`. |
| `Street Address` | `address` | House number and street in one uppercase field. |
| `Borough` | `borough` | Uppercase borough name. |
| `Zip Code` | `zipcode` | Five-digit postal zip code. |
| `Phone Number` | `phone` | Formatted `(999) 999-9999`. |
| `Child Age Range` | `age_range` | `0 YEARS - 2 YEARS`, `2 YEARS - 5 YEARS`, `3 YEARS - 5 YEARS`, or `NO DATA`. |
| `Children Allowed in Care` | `capacity` | Maximum capacity as an integer in text, or `NO DATA`. |
| `Administer Medication` | `administer_medication` | `Yes` or `No`: whether staff are qualified to administer medication. |
| `BIN` | `bin` | Department of Buildings Building Identification Number, seven digits. |
| `BBL` | `bbl` | Borough, block, and lot, ten digits. |
| `Community Board` | `community_board` | Three digits: the borough digit, then the district number. |
| `Council District` | `council_district` | City Council district, two digits, zero-padded. |
| `Census Tract` | `census_tract` | 2020 Census tract, six digits, without state or county code. |
| `NTA Code` | `nta_code` | Neighborhood Tabulation Area in the 2010 four-character format (`BK09`), not the 2020 six-character format (`BK0101`). |
| `Latitude` | `latitude` | Decimal degrees, from NYC's Geosupport geocoder. |
| `Longitude` | `longitude` | Decimal degrees, from NYC's Geosupport geocoder. |

The borough digit used by `BBL` and `Community Board` is `1` Manhattan, `2` Bronx, `3` Brooklyn, `4` Queens, `5` Staten Island.

Quirks:

1. Covers active Group Child Care programs (NYC Health Code Article 47) and School-Based Child Care programs (Article 43).
2. Programs with a preliminary, suspended, or closed status are excluded. A provider disappearing from the dataset means it is no longer active, so ingestion records disappearance rather than deleting the provider.
3. "Children Allowed in Care" is the maximum capacity the space allows. Actual capacity depends on staffing, so reports call it licensed capacity, never enrollment.
4. A row is one permitted program, not one location. A center commonly holds separate infant-toddler and preschool programs at the same address, each with its own `DCID` and capacity, and a school-based program can share a building with a group program. Programs at one address carry identical coordinates.
5. `DCID` is unique per row and is the stable identifier and the upsert key. The same `DCID` and permit pairs appear in the DOHMH Childcare Center Inspections (Historical) dataset, `dsg6-ifza` on NYC Open Data, whose rows are years old because its publisher froze it during a system upgrade, so a `DCID` persists for years. `Permit Number` is not an identifier: every school-based program shares the value `N/A`.
6. `Children Allowed in Care` is `NO DATA` for most school-based programs. Store it as unknown, never as zero.
7. `Child Age Range` is `NO DATA` in some rows. Otherwise it agrees with `Program Type`: infant-toddler programs list 0 to 2 years, preschool programs 2 to 5 or 3 to 5 years.
8. There is no geometry column: the point is built from `Latitude` and `Longitude`. A few rows have no coordinates and no `BIN`, `BBL`, `Community Board`, `Council District`, `Census Tract`, or `NTA Code`, because Geosupport could not match the address. They are centers with published addresses, so they are geocoded from the address.
9. Some rows have coordinates but no `BIN` and a `BBL` holding only the borough digit, for example where the street address is a house number range or has a floor appended. A `BIN` of a borough digit followed by six zeros (`3000000`) is a placeholder for an unknown building and is treated as blank.
10. Street addresses mix spelled-out and abbreviated street types (`STREET`, `ST`, `BLVD.`), usually omit ordinal suffixes on numbered streets (`150 STREET`), sometimes append a floor, and contain runs of double spaces. Matching normalizes all of these.
11. `Borough` is blank in rare rows. The first digit of `BBL` or `Community Board` gives the borough. The zip code is only a fallback, because a few zip codes cross borough lines.
12. `Census Tract` repeats across boroughs, so a join to Census data adds the county code derived from the borough. The tract is the 2020 vintage, while `NTA Code` follows the 2010 boundaries, so `NTA Code` is not used: a neighborhood, when a report needs one, comes from the point and the 2020 boundaries.
13. `Program Type` can carry values beyond the documented ones, in a different case. Ingestion accepts and records an unknown program type rather than failing.
14. Program names contain U+2019 RIGHT SINGLE QUOTATION MARK in place of an apostrophe, and are sometimes mixed case.
15. One row has a blank `Phone Number`.
16. Hyphenated house numbers sometimes have spaces around the hyphen (`45 - 12 BELL BLVD`).
17. `BIN` is blank in some rows, beyond the rows without any Geosupport match.

### How the pipeline reads both datasets

1. Each provider run makes two requests to its publisher: the metadata, for `rowsUpdatedAt`, and the full CSV export. The export's headers are the columns' display names.
2. Before anything is written, the export must contain every expected header: all the NY State columns except `Georeference`, and all the NYC Health columns except `NTA Code`. A missing header fails the run; extra columns are ignored.
3. Rows whose identifier is blank, repeated, or longer than 20 characters are skipped and logged by row number. A cell that cannot be used is stored as unknown, with a field warning naming the source, identifier, and column, logged at most once per column and row. Unusable cells include unparseable values and values too long for their column: 20 characters for `Zip Code` and for the NY State `Program Type` and `Region Code`, 30 for `Phone Number`, and 255 for any other text. Counts above 2,147,483,647 are unusable too.
4. Without an app token, requests share Socrata's anonymous throttling pool. When `SOCRATA_APP_TOKEN` is set, it is sent in the `X-App-Token` header.
5. Socrata's SODA3 endpoints require an app token, while the export endpoint does not, so the pipeline reads the export.

### Overlap between the two

Overlap is measured, not assumed, by matching on normalized address and zip code, then normalized name, then coordinates.

1. The two datasets never list the same program: their program types are disjoint.
2. They do share locations. An operator often holds a city permit for its infant-toddler or preschool programs and a state registration for a school-age (`SACC`) program at the same address, usually under the same or a similar name. Family and group family day care homes also occasionally share a street address with a center under a different name, so an address match alone does not establish a shared operator.
3. Each publisher geocodes independently, so the coordinates of one location differ between the datasets by tens of meters and occasionally by more than 100 meters. Coordinates support a match made on address and name; they never decide one alone.

### Providers and competitors

A competitor is what a report presents: the providers one operator runs at one location, drawn from either dataset.

1. A provider keeps the regulator's unit and identifier: one NY State `Facility ID` or one NYC Health `DCID`. A center with an infant-toddler and a preschool program is two providers and one competitor.
2. Every provider kind in the table above is in scope. School-based programs are competitors for the ages they serve and are labelled as school-based, because their capacity is often unpublished.
3. A provider holds its source's current published values only. A provider absent from its source's latest export is marked missing from that run on, never deleted, and is reactivated when it reappears. Only a run of the provider's own source changes its activity.
4. A provider run refuses to mark more than 10% of its source's active providers missing, before writing anything, unless it is run with `--allow-mass-disappearance`.

#### Matching keys

Grouping compares three keys, recomputed from the published values on every run. `axisence/providers/normalization.py` holds the exact rules and tables.

1. Display name: the published name without double quotation marks, with whitespace collapsed.
2. Name key: the name uppercased, `DAYCARE` split into `DAY CARE`, apostrophes (`'`, U+2018, U+2019, `` ` ``) and periods deleted, every other punctuation mark turned into a space, the legal-form words `INC`, `LLC`, `CORP`, `CORPORATION`, `LTD`, and `CO` dropped, and a leading `THE` dropped. `The Kids' Daycare, Inc.` becomes `KIDS DAY CARE`.
3. Address key: the house number and street, uppercased and cut at the first comma, with apostrophes and periods deleted. A Queens-style house number keeps its hyphen without spaces (`45 - 12` becomes `45-12`). Unit text is removed: P.O. boxes, suite, apartment, unit, room, building, and store designators with their numbers, `#` numbers, floors, and basements. Remaining punctuation becomes spaces, ordinal suffixes are dropped (`4TH` becomes `4`), and street types and directions take their USPS Publication 28 abbreviations (`STREET` becomes `ST`, `WEST` becomes `W`). `150 West 4th Street, 2nd Floor` becomes `150 W 4 ST`.
4. Zip key: a five-digit zip code, or the first five digits of a nine-digit one, with or without its hyphen. Anything else is no zip key.
5. Two name keys are similar when one contains the other, or when their `difflib.SequenceMatcher` ratio is at least 0.8.

Ordinal words (`FIRST`), `&` versus `AND`, and `SAINT` versus `ST` are not normalized: measured on both datasets, doing so would change at most three groups.

#### Grouping rules

| Rule | Meaning |
| --- | --- |
| A | A provider without a competitor joins the one competitor that has a member with equal address, zip, and name keys. Without one, it gets a competitor of its own. A provider missing any key, as every omitted address is, always gets a competitor of its own. |
| B | A placed provider keeps its competitor on every later run. Only a researcher's decision moves it. |
| C | The rule is the same within one dataset and across both. A provider a researcher moved stays where it was moved, even when its keys still equal its old competitor's members. |
| D | When a placed provider's published name, street address, or zip code changes and its keys change with it, the provider keeps its competitor and a changed-keys flag asks a researcher to confirm. A key change caused by new normalization alone raises no flag. |
| E | A pair of competitors a researcher declared different operators is never proposed again. |
| F | A competitor is active while any of its providers is active. A competitor left without providers by a researcher's decision is retired, naming the competitor that received its providers. |
| G | Automation never merges existing competitors. Only a researcher's decision does. |

A provider's keys may equal members of several competitors, for example after a researcher moved one. It then gets a competitor of its own, with an equal-keys flag towards each of them. A provider whose name is similar to, but not equal to, a member at the same address and zip raises a similar-name flag. Flags are raised only when a provider is placed, and at most one open proposal exists per pair of competitors.

#### Review flags and researcher decisions

| Flag reason | Question | Decisions |
| --- | --- | --- |
| Similar name at one address | Are the flagged and the proposed competitor one operator? | Same operator merges the flagged competitor into the proposed one; different operators separates the pair for good. |
| Equal keys in several competitors | Does the provider's own competitor belong with the proposed one? | As for a similar name. |
| Changed keys | Does the provider still belong to its competitor? | Provider stays; or the researcher moves the provider, which resolves the flag. |

A researcher can also move any provider to another competitor or to a new one of its own, and merge any competitor into another. Every decision is logged with its researcher and time. When a merge or move retires a competitor, its open flags are re-pointed to the receiving competitor, and a proposal that would pair a competitor with itself or repeat another open proposal is closed as superseded.

#### Competitor summary

A competitor shows its representative provider's display name, street address, borough, and location. The representative is the first member when sorted by: active before missing, a published street address first, a location first, NYC Health before NY State, then source identifier. The competitor is school-based when any member is.

### Geocoding

Providers that publish an address but no coordinates are geocoded with the US Census Geocoder batch endpoint, `https://geocoding.geo.census.gov/geocoder/locations/addressbatch`, with benchmark `Public_AR_Current`. It needs no key and takes up to 10,000 addresses per request.

1. The request is a multipart form: the field `benchmark` and the file field `addressFile`, a CSV without a header of identifier, street, city, state, and zip code. The street is the published house number and street, cut at the first comma and without unit text, otherwise spelled as published. The city is the NY State `City`, or the borough name for NYC Health.
2. The response is CSV without a header, every field quoted. A `Match` line holds the identifier, the input address, `Match`, the match type (`Exact` or `Non_Exact`), the matched address, `"<longitude>,<latitude>"` in one field, the TIGER line identifier, and the side of the street. A `No_Match` or `Tie` line holds three fields. Lines are matched to addresses by identifier, never by position.
3. Only a `Match` inside the NY State bounding box counts as a location. Positions are interpolated along street address ranges, so they are tens of meters less precise than the building-level positions publishers provide.
4. Queens-style hyphenated house numbers may fail to match.
5. A provider is a candidate when its regulator publishes no coordinates and it has a house number and street: for NY State a `Street Number` and a `Street Name`, for NYC Health a `Street Address` of at least two words that starts with a digit. An omitted address is never a candidate. A failed attempt is retried on every run; a match is attempted again only when the published address changes.
6. Publisher coordinates always win over a geocoded location. A geocoded location is kept only while the published address that produced it is unchanged.
7. Each provider records where its location came from (publisher, geocoder, or none), the address sent, the matched address, the time of the latest attempt, and its outcome.

#### Single-address lookup of target addresses

The research tool looks a research job's target address up with the single-address endpoint, `https://geocoding.geo.census.gov/geocoder/locations/onelineaddress`, with three parameters: `address` (the whole address on one line), `benchmark=Public_AR_Current`, and `format=json`. It needs no key.

1. The response is one JSON object. `result.input` echoes the address and the benchmark, and `result.addressMatches` lists the matches. Each match holds `matchedAddress` (text), `coordinates` with `x` (longitude) and `y` (latitude) as JSON numbers, `addressComponents`, and `tigerLine`. The numbers are read as decimals.
2. The tool sends one request per lookup, with a timeout of 5 seconds to connect and 10 to read and no retry, because a researcher waits on the page. It stores no raw response.
3. One match inside the NY State bounding box is proposed as the target point, and several are listed for the researcher to pick. No match, matches outside the state only, or a failed request propose nothing.
4. Measured behaviour, with public landmarks:
    1. A Queens-style hyphenated house number returns no match: Queens Borough Hall, `120-55 Queens Blvd`.
    2. A vague input returns a single wrong match without any sign of doubt: `100 Main St, NY` matches a point on Governors Island.
    3. A match can carry a different zip code from the input: Brooklyn Museum, `200 Eastern Pkwy`, matches in `11225`.
    4. An input without a city returns no match.
5. A proposal is therefore never trusted as it comes: the researcher confirms the point on the map, or places the pin, before the search and the research.
6. The target addresses of clients and prospects are personal data: they are never logged, and never appear in documentation or fixtures.

## Demographics

US Census Bureau American Community Survey 5-year estimates, through the Census Data API.

1. Geography for a zip-code view is the ZIP Code Tabulation Area. Tabulation areas approximate postal zip codes but are not identical; reports label them as zip codes only with that caveat available.
2. Data profile tables: `DP05` demographics and age, `DP03` income and employment, `DP02` education, `DP04` housing.
3. Age brackets are fixed by the Census: "Under 5 years" and "5 to 9 years". A report figure such as "children 0–5" states which bracket it uses.
4. Every figure carries its table, survey vintage, and margin of error, so a report can cite it.
5. NY State has 1,824 tabulation areas. Their codes can start with zero (`06390`), so they are stored and compared as text.

### Census Data API

1. Every request needs a key. A request without one is redirected to `https://api.census.gov/data/missing_key.html`, and one with an invalid key to `/data/invalid_key.html`; both answer HTTP 200 with an HTML page, so a 200 alone does not prove success.
2. The data profile endpoint of a vintage is `https://api.census.gov/data/<vintage>/acs/acs5/profile`.
3. `ucgid=pseudo(0400000US36$8600000)` selects every NY State tabulation area in one request. Adding `for=zip code tabulation area:*` changes nothing. A response is a JSON array of arrays: a header row, then one row per area, ending with `GEO_ID`, `NAME`, and `ucgid`. `ucgid` and `GEO_ID` hold `860Z200US<code>`, and `NAME` holds `ZCTA5 <code>`.
4. `get=group(<table>)` returns a whole table: eight columns per variable code, `<code>E` (estimate), `<code>EA`, `<code>M` (margin of error), `<code>MA`, `<code>PE` (percent), `<code>PEA`, `<code>PM` (percent margin of error), and `<code>PMA`. The `A` columns are annotations and are not stored.
5. Codes read like `DP05_0005`. Vintage 2024 has 154 codes in `DP02`, 137 in `DP03`, 143 in `DP04`, and 108 in `DP05`: 542 codes and 988,608 figures for NY State per vintage.
6. Values are JSON strings, parsed as decimals. Sentinels stand for unavailable values: `-999999999`, `-888888888`, `-666666666`, `-555555555`, `-333333333`, and `-222222222`, also spelled with decimals (`-666666666.0`, `-222222222.00`), so they are compared numerically. Sentinels, empty strings, and nulls are stored as unknown. Some values are capped, such as a median published as `4001` with the annotation `4,000+`. A median in the lowest interval is published as its floor minus one, such as `2499` with the annotation `2,500-`. The margin of error of a median in either open-ended interval is `-333333333`, so it is stored as unknown.
7. Labels come from `https://api.census.gov/data/<vintage>/acs/acs5/profile/groups/<table>.json`: `DP05_0005E` is `Estimate!!SEX AND AGE!!Total population!!Under 5 years`, and `DP05_0005PE` is the same label under `Percent`.
8. The Census Bureau releases a new 5-year vintage each December and can renumber codes between vintages, so variables and labels are kept per vintage, and vintages are kept side by side.

### Tabulation-area boundaries

1. Boundaries come from the 2020 cartographic boundary file `https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip`: 1:500,000 scale, clipped to shorelines, 33,791 areas nationwide. Every ACS release from 2020 to 2029 uses these 2020 areas.
2. The zip holds one shapefile layer, `cb_2020_us_zcta520_500k`, with the fields `ZCTA5CE20` (the code), `AFFGEOID20` (`860Z200US<code>`), `GEOID20`, `NAME20`, `LSAD20` (`Z5`), `ALAND20`, and `AWATER20`. Features are polygons or multipolygons.
3. The coordinate system is NAD83, SRID 4269. Boundaries are stored in WGS 84, SRID 4326.
4. The boundaries are simplified, so a point within tens of meters of an edge can fall in the neighbouring area.

## Transit

The MTA's static GTFS schedules give the subway stations and bus stops of New York City and the lines scheduled to stop at each. They cover New York City only: no LIRR, Metro-North, PATH, ferry, or NJ Transit service.

### Feeds

Seven public zip files at `https://rrgtfsfeeds.s3.amazonaws.com/<file>`, without a key:

| File | Covers |
| --- | --- |
| `gtfs_subway.zip` | The subway and the Staten Island Railway |
| `gtfs_b.zip`, `gtfs_bx.zip`, `gtfs_m.zip`, `gtfs_q.zip`, `gtfs_si.zip` | New York City Transit buses, one file per borough: Brooklyn, the Bronx, Manhattan, Queens, and Staten Island |
| `gtfs_busco.zip` | MTA Bus Company buses, across boroughs |

1. The MTA also publishes a supplemented subway feed, `gtfs_supplemented.zip`, which adds the planned service changes of the coming days. Those are temporary diversions, not the regular service a report describes, so it is not read.
2. The host sends `ETag`, with its surrounding double quotes, and `Last-Modified` headers. A request whose `If-None-Match` repeats the ETag verbatim is answered with HTTP 304 and no body while the file is unchanged.
3. Every zip holds `agency.txt`, `calendar.txt`, `calendar_dates.txt`, `feed_info.txt`, `routes.txt`, `shapes.txt`, `stop_times.txt`, `stops.txt`, and `trips.txt`; the subway's also holds `transfers.txt`. The pipeline reads these members and columns, and ignores every other member and column:

    | Member | Columns read | Feeds |
    | --- | --- | --- |
    | `stops.txt` | `stop_id`, `stop_name`, `stop_lat`, `stop_lon` | all |
    | `stops.txt` | `location_type`, `parent_station` | subway |
    | `routes.txt` | `route_id`, `route_short_name`, `route_type` | all |
    | `routes.txt` | `route_sort_order` | subway |
    | `trips.txt` | `route_id`, `service_id`, `trip_id` | all |
    | `calendar.txt` | `service_id`, `monday` to `friday`, `end_date` | all |
    | `stop_times.txt` | `trip_id`, `stop_id`, `arrival_time`, `departure_time`, and `pickup_type` and `drop_off_type` when present | all |
    | `transfers.txt` | `from_stop_id`, `to_stop_id` | subway |

4. Column order differs between feeds, so columns are found by header name. The subway's `stop_times.txt` has no `pickup_type` or `drop_off_type` column: every subway stop time lets riders on and off.
5. `stop_times.txt` is by far the largest member and the only source of the lines that stop at a stop. It is streamed from the zip, never extracted.

### Stations and stops

1. A subway station is a `stops.txt` row with `location_type` `1`. Its platforms are rows with a blank `location_type` and the station's ID in `parent_station`, such as `232N` and `232S` of station `232`. Stop times refer to platforms; each platform counts for its station, and only stations are stored. Rows of any other location type are ignored.
2. `transfers.txt` lists pairs of stations joined by an in-system transfer, including a row from every station to itself, which joins nothing. Some complexes join stations of one name, such as Borough Hall `232` (2, 3) and `423` (4, 5), or Jay St-MetroTech `A41` (A, C, F) and `R29` (R, W). Others join differently named stations, such as Borough Hall and Court St `R28`.
3. A station group is the stations of one name joined by transfers, directly or through each other. It is presented as one station, with the lines of all its stations; differently named stations stay apart. Its key is its stations' IDs sorted as text and joined by `+`, such as `232+423`; a station alone has its own ID.
4. A bus stop is one row of a bus feed, usually one per side of the street, without a parent. Its `location_type` is `0` or blank.
5. Bus feeds write coordinates with leading spaces (`  40.693578`), so every cell is trimmed before use. A row whose coordinates are blank, unparseable, or outside the NY State bounding box is skipped with a field warning, and its stop stays as stored.
6. A bus stop served by routes of several feeds appears in each under the same ID. The New York City Transit feeds agree on its name and position; `gtfs_busco.zip` publishes a position and name text of its own for the same ID, such as `306533` at Cadman Plaza West and Montague St. The stored stop takes its name and position from the first feed holding it, in the order `gtfs_b`, `gtfs_bx`, `gtfs_m`, `gtfs_q`, `gtfs_si`, `gtfs_busco`, and its lines from every feed.
7. Feeds cross borough lines: a borough's feed lists every stop its routes serve, wherever the stop lies, so the Queens and Manhattan feeds list stops in the Bronx. Many stop IDs begin with a digit naming a borough (`1` the Bronx, `2` Staten Island, `3` Brooklyn, `4` Manhattan, `5` Queens), but the digit does not prove where a stop lies: the Q58's stop `304841` lies in Ridgewood, Queens.

### Routes and lines

1. Each New York City Transit bus feed's `routes.txt` lists the routes of the whole city, while its stops and trips cover only its own routes. Lines come from trips, never from the route list.
2. Route types: `1` subway, `2` the Staten Island Railway, `3` bus, and `711`, a shuttle bus in the extended route types, listed in the New York City Transit bus feeds. Shuttle bus routes are not lines and are never counted.
3. Route IDs can differ from the names riders know: `B44+` is `B44-SBS`, `BX12+` is `Bx12-SBS`, `Q06` is `Q6`, `BXM1` is `BxM1`, and the subway's `GS`, `FS`, and `H` are all the shuttle `S`, while `SI` is `SIR`. A line is named by its route's `route_short_name`.
4. A subway route whose short name is another subway route's short name followed by `X` is an express variant of that line: `FX`, `6X`, and `7X` count as the F, 6, and 7. The three shuttles keep `S`. Select Bus Service names (`B44-SBS`) and express buses (`BM1`, `BxM1`) stay as published.
5. Subway lines are ordered by `route_sort_order`, the MTA's own order (A, C, E, B, D, F, M, G, J, Z, L, N, Q, R, W, S, 1 to 7, SIR); a line takes the smallest order of its routes. Bus lines are in natural order: by their text ignoring case, with numbers compared by value, so `B2` comes before `B11`, `B44` before `B44-SBS`, and `B103` before `BM1`.

### Weekday daytime service

A line counts at a stop when one of its trips is scheduled to stop there on weekday daytime service:

1. The trip's service in `calendar.txt` runs on at least one weekday and its `end_date` is on or after the day of the run. `calendar_dates.txt`, which holds holidays and one-off changes, is not read.
2. The stop time's departure, or its arrival when the departure is blank, lies from 07:00:00 to before 19:00:00. GTFS writes times after midnight as 24:00:00 and later, so they never count.
3. A stop time that neither picks up nor drops off (`pickup_type` and `drop_off_type` both `1`) does not count, and shuttle bus routes never do.

The rule matches when families travel to and from childcare. A line that stops only late at night, such as the N at Court St, or only on weekends, would mislead.

The subway calendar's services end every few months and the bus calendars' a few months later, and the MTA republishes the feeds with new periods. A feed whose weekday services have all ended fails the run, naming the feed, until the MTA republishes it; reports keep the last succeeded run meanwhile.

## Mystery-shopping observations

Proprietary data collected by Axisence researchers by phone, website, and email, and recorded in the research tool.

### Contacts and observations

1. A contact is one call, website visit, or email with one competitor. It records the contact date, the method (call, website, email), the researcher, the time it was entered, the role of the person spoken to (director, front desk, owner, other), and optional notes. It never records the name, email, or phone number of a person. A contact without observations records an unanswered call.
2. An observation is one typed fact within a contact, and takes its date, method, and researcher from the contact.
3. Age groups and schedules are the provider's own definitions, recorded as observations. An age group has the provider's name for it and its youngest and oldest age in whole months, rounded down, with a blank oldest age when it has no upper limit. A schedule has the provider's name for it, its days per week, and optionally its hours per day. A contact can define new ones and retire current ones.
4. A price is an amount as stated, in US dollars, with its period (hourly, daily, weekly, monthly, annual), for one age group on one schedule, and records whether the amount already includes discounts. Nothing derived from it, such as a monthly equivalent, is stored.
5. Availability is recorded per age group: openings, limited openings, a waitlist, or full without a waitlist, with an optional earliest start date and expected wait in months.
6. Discounts (sibling, multi-day, upfront payment, referral, other) and other charges (registration, deposit, late pickup, other) are recorded by kind, with an optional value and an optional description. A discount's value is a percent or a dollar amount; a charge's value is a dollar amount or months of tuition, with an optional frequency (once, per year, per occurrence, per hour). A kind can be recorded as none offered.
7. Single facts, at most one of each per contact: the operating status (operating, temporarily closed, permanently closed), weeks open per year, weekday opening hours, the inspection status as the source states it with an optional inspection date, the places as the provider states them (never licensed capacity), the minimum booking in days per week, the month of the last fee rise, a description or unique selling point, parking (on-site, street only, none) with a note, the website address, and whether the provider offers 3-K for All seats, offers Pre-K for All seats, and accepts child care vouchers. The last three are New York City's free public seats for three- and four-year-olds and its child care subsidies: a competitor that offers them competes for families who pay little or nothing, so its stated tuition alone understates its pull.
8. A reported closure is an operating-status observation. It labels the competitor wherever it is listed, but never changes the activity of its providers, which only their regulators' datasets decide.
9. Online presence (website traffic, social media, review ratings) and strengths, weaknesses, opportunities, and threats summaries are not recorded.

### Slots and current values

A slot is what one value answers: the price of one age group on one schedule, the availability of one age group, one kind of discount or other charge, or one single fact.

1. An observation counts while neither it nor its contact is retracted.
2. An age group or schedule is current until a counting retirement points at it, and a value counts only while every definition it refers to is current.
3. In each slot, the newest contact among the counting values holds the current value, by contact date, then entry time, then contact number. Every counting value of the slot from that contact is current, so two quotes from one call are current together, and a "none offered" from the newest contact supersedes the older values of its kind.
4. A retracted value or contact falls back to the next newest contact.
5. A competitor's current values include the observations of every competitor merged into it, each with its own date.

### Retraction and retirement

1. Contacts and observations are never changed or deleted. A mistake is retracted with a reason, which records who retracted it and when, and the retraction is final. The correct value is then recorded in a new contact.
2. Retracting a contact retracts every observation of it.
3. Retiring an age group or schedule is an observation of a later contact. It records that the provider no longer uses the definition, takes the definition's prices and availability out of the current values, and keeps them in the history.

### Stated values and history

1. A value states what is compared between contacts: a price its amount, its period, and whether the amount includes discounts; availability its status; a discount whether none is offered, its amount, and its percent; an other charge whether none is offered, its amount, its months of tuition, and its frequency. Descriptions, earliest start dates, and expected waits are not part of it.
2. One contact's stated value of a slot is the set of its counting values there, and two are equal when the sets are equal. A re-confirmation records a copy of the current value, so it appears as an equal stated value.
3. The call sheet's history groups each slot's counting values by contact, in contact order, and joins consecutive contacts with equal stated values into a run, shown with its first recorded and its last confirmed contact. Retracted values are left out, and the slots of retired definitions stay.

### Rules for observations

1. Every observation records its date, method, and researcher through its contact. Prices change, so observations are never overwritten; a newer contact supersedes an older one for display while both are kept.
2. Prices keep their stated period and the provider's own age-group and schedule definitions. Age bands differ between providers, so "infant" in one center is not assumed to match "infant" in another.
3. Call notes and the target addresses of clients and prospects are personal data. Notes appear only on the call sheet and on the contact's own admin page, and never name, email, or phone a person. Neither notes nor target addresses ever appear in reports, fixtures, documentation, or logs.
4. Inspection status is an observation like any other, with its date and method. The NYC Health inspection dataset (`dsg6-ifza`) is frozen, so it is not a source of current status.
5. A new age group or schedule identical to a current one of the competitor, or of a competitor merged into it, is refused: the same name, ignoring case and extra spaces, and the same youngest and oldest age, or the same days per week and hours per day, where a blank limit or blank hours match only a blank. A new one identical to another new one of the same contact is refused too. A duplicate would split a slot and keep the old price current beside the new one. Duplicates recorded before the rule stay until a researcher retires them.

## Reports

A report presents a research job's competitors with their public data and research values, the transit near them and the target location, and the Census figures of the target location's tabulation area. A teaser presents the market around a prospect's location from public data only.

### Research values in a report

1. A report's research values are the current values as of its cut-off, the moment it was generated: an observation counts when its contact was entered by then and neither it nor its contact had been retracted by then. The other rules of current values apply unchanged.
2. Every value carries its contact date and method, whichever job or call sheet recorded it. The changes section of a repeat report is the exception: its value cells show values without their contact, and each continuing competitor's "Verified again" line names its newest contact since the compared report.
3. Licensed capacity totals sum the published capacities of a competitor's active providers. A total states the regulators' maximum, never enrollment or availability; a competitor whose providers publish only some capacities is marked incomplete.

### Prices and the monthly equivalent

Prices appear as stated: the amount, the period, and the provider's own age group and schedule, with a star when the amount includes discounts. The monthly equivalent is computed when a report is rendered and is never stored:

| Period | Monthly equivalent |
| --- | --- |
| Monthly | As stated |
| Weekly | × 52 ÷ 12 |
| Daily | × the schedule's days per week × 52 ÷ 12 |
| Hourly | × the schedule's hours per day × days per week × 52 ÷ 12, only when the schedule states its hours; otherwise "not computable" |
| Annual | ÷ 12 |

The result is rounded to the nearest whole dollar, halves up.

### Tuition comparison

The comparison sets competitors side by side at the reference ages 12, 24, 36, and 48 months, without assuming one provider's age bands match another's.

1. An age group contains an age when its youngest age is at most the age and its oldest age is blank or at least the age.
2. Each cell holds the monthly equivalents of every current price of a containing age group on a schedule of 5 days per week: one amount when they agree, otherwise the lowest and the highest. A star marks the cell when any price whose amount it shows includes discounts.
3. An empty cell says why: "not offered" when no age group contains the age, "no 5-day price" when one does but has no price on a 5-day schedule, and "not computable" when the only such prices are hourly on schedules without stated hours.
4. A competitor without a current price reads "No verified prices".
5. The 36-month cell notes "3-K for All seats" and the 48-month cell "Pre-K for All seats" when the competitor's current answer to that question is Yes.

### Transit access

A draft or final report shows the transit near the target location and near each competitor of its main list, from the latest succeeded transit run finished by its cut-off, which the report records.

1. The "Transit access" section follows the tuition comparison. Its table lists the target location first, labelled "Target location" and never by its address or coordinates, then each competitor of the main list with its number. Reported-closed competitors have no row, so when every competitor is reported closed the table holds the target location alone.
2. Each competitor's profile holds a "Transit" block under its public data, with the same two texts on two lines.
3. Subway: the two nearest station groups within 0.5 mi, nearest first, ties by name, each as `<name>: <lines> (<distance>)`. A group stands at the distance of its nearest station and lists the lines of all its stations, in subway order, even a station of the group that lies beyond 0.5 mi.
4. Bus: every bus line with a stop within 0.25 mi, once, at the distance of its nearest stop, as `<line> (<distance>)`, nearest first, ties in natural order. Each entry is kept whole on one line, so a long list wraps only between entries.
5. Only active stops with at least one line count. Distances are straight lines on the ellipsoid, rounded to hundredths of a mile, and both limits include a stop exactly at them. 0.5 mi and 0.25 mi are the standard walking catchments of transit planning.
6. Nothing in range reads "None within 0.5 mi" or "None within 0.25 mi"; a competitor without a location reads "No location".
7. Generation refuses, after every other refusal, while no transit run has succeeded by the cut-off: "Transit data has not been loaded: run ingest_mta_transit first."
8. Sources and method names the MTA schedules, New York City only, with the local date the run started, and defines the catchments and the weekday daytime rule. Transit outside New York City, walking routes, and schedules are not covered. Teasers show no transit.

### Repeat reports

1. A repeat job is prepared from a Complete client report job and records it as its predecessor. Each of its draft and final reports is compared with the predecessor's latest final report with listed competitors at generation, its compared report, which is recorded with the report.
2. Every draft and final report records the competitors it lists: its main list, numbered, and its reported-closed list. Reports generated before this record have none, are never compared reports, and make no job repeatable.
3. Both sides are the current values as of each report's cut-off, computed with the merges as they are at this generation, so a competitor merged since the compared report is compared with the combined values of the competitors merged into it.
4. Matching: each entry of the compared report's main list resolves through merges, and a competitor of this report's main list continues when an entry resolves to it. An entry whose own competitor differs from the continuing competitor it resolves to is gone as merged.
5. An added competitor is "Newly listed by its regulator since report #N" when it has at least one provider and every provider was first seen after the compared report's cut-off, and otherwise "Newly included". A gone competitor gives the first reason that applies: merged, reported permanently closed as of this cut-off, no longer listed by its regulator, or not included.
6. Search settings: a changed radius, provider kinds added or removed, and a target moved at least 0.005 mile, stated as a distance only.
7. Verified again: the newest contact entered between the two cut-offs that has a counting observation.
8. Value classes: Changed, Re-confirmed, Not re-checked, New, and Ended, by stated value and by whether this side's value comes from a contact entered after the compared cut-off. A kind declined on both sides counts as unchanged; declined on one side and missing on the other, it is not compared.
9. Tuition rows compare the comparison cells at the reference ages, so they bridge a provider's redefined age groups and schedules. The redefined slots themselves show as Ended and New, because nothing links a retired definition to its replacement.

### Census figures

A report shows the figures of the newest ingested vintage that has a figure list, for the target location's tabulation area. Before every report and teaser, the list's expected labels are compared with the stored labels of that vintage, whatever the target's area, and a mismatch refuses the report or teaser.

The vintage 2024 figure list:

| Heading | Report label | Code | Shown |
| --- | --- | --- | --- |
| Children | Total population | `DP05_0001` | Count |
| Children | Children under 5 years (Census age bracket) | `DP05_0005` | Count, and percent of total population |
| Children | Children 5 to 9 years (Census age bracket) | `DP05_0006` | Count, and percent of total population |
| Working parents | Own children of the householder under 6 years | `DP03_0014` | Count |
| Working parents | With all parents in the family in the labor force | `DP03_0015` | Percent of own children under 6 years |
| Households and schooling | Total households | `DP02_0001` | Count |
| Households and schooling | Households with one or more people under 18 years | `DP02_0014` | Percent of households |
| Households and schooling | Enrolled in nursery school or preschool | `DP02_0054` | Count |
| Households and schooling | Speak a language other than English at home | `DP02_0114` | Percent of population 5 years and over |
| Income and poverty | Median household income (2024 dollars) | `DP03_0062` | Dollars |
| Income and poverty | Median family income (2024 dollars) | `DP03_0086` | Dollars |
| Income and poverty | Families with children under 5 only: below the poverty level | `DP03_0121` | Percent |
| Income and poverty | Workers commuting by public transportation | `DP03_0021` | Percent of workers 16 years and over |
| Housing | Renter-occupied housing units | `DP04_0047` | Percent of occupied housing units |
| Housing | Median gross rent | `DP04_0134` | Dollars |

1. Base rows (`DP05_0001`, `DP02_0001`, `DP03_0014`) repeat their count in the percent column, without a percent margin, so a report shows only their count.
2. `DP03_0121` holds only a percent and its margin; the three medians hold only an estimate and its margin.
3. Each value shows its margin of error at 90% confidence after "±". A value the Census Bureau did not publish reads "Not published"; a value without a usable margin shows none.

### Coded medians

A median in the highest interval is published as the cap plus one, and a median in the lowest interval as the floor minus one. A report prints them as "$<cap> or more" and "$<floor> or less", without a margin.

| Code | Cap | Floor |
| --- | --- | --- |
| `DP03_0062` | 250,000 | 2,500 |
| `DP03_0086` | 250,000 | 2,500 |
| `DP04_0134` | 3,500 | none |

These are the vintage 2024 values. The caps and the floor of `DP03_0062` appear in the published annotations; `DP03_0086` takes the floor its income family shares. The Census Bureau can renumber codes and change caps between vintages, so a new vintage is used only after its figure list, labels, caps, and floors are checked by hand.

### Teasers

1. A teaser is a one-page preview of the market around a prospect's location, printed as "Childcare market preview" and generated from a Prospect research job. It holds public data only. The only research fact it reads is a reported permanent closure current at its cut-off.
2. Counted competitors are the job's search candidates at generation: active, current, located within the search radius, with an active provider of one of the job's kinds, the own competitor excluded. Candidates reported permanently closed as of the cut-off are left out. The selection and the research statuses are never read, and neither is any other research value, a temporary closure included. A competitor left out for a closure appears nowhere on the page.
3. Counted competitors are ordered by straight-line distance, nearest first, ties by competitor number. The nearest eight are named with their kinds, distance, and licensed capacity, and "School-based" is their only label. Every other counted competitor is an unnumbered dot on the map. A named competitor's number sits on its position; where it would cover another number, another named competitor's position, the target, or the scale bar, or run off the map, it sits beside the position, joined to it by a visible line that runs clear of the other numbers and of every position but those touching its own. Such a number prefers a spot plainly nearest its own position, then one that leaves the plain dots visible. In very tight clusters, such as eight competitors within about 0.08 miles of one another, a line can still cross another or pass close to a position. A ZIP Code Tabulation Area's code sits at its area's own label point, or at another spot inside the area, where it fits inside the map clear of the numbers, their lines and positions, the target, the scale bar, and the codes of areas with a larger part on the map, preferring spots clear of the plain dots and the circle; a code that fits nowhere is left out.
4. The count per kind covers the job's kinds, zeros included. A competitor counts under each of the job's kinds that its active providers hold, so a competitor with providers of several of them counts under each, and the page then says so. Kinds outside the job's kinds, such as a center's school-age program, are not counted, though the named list shows them among a competitor's kinds. The capacity total sums the published capacities of the counted competitors' active providers, beside the number whose capacity is unpublished or incomplete.
5. The job's own competitor is never counted, named, or drawn. The page says "Excludes your own program." and gives no fact of it.
6. The headline figures, in this order:
    1. `DP05_0005`, "Children under 5 years (Census age bracket)": count, and percent of total population.
    2. `DP03_0015`, "With all parents in the family in the labor force": percent of own children under 6 years.
    3. `DP02_0014`, "Households with one or more people under 18 years": percent of households.
    4. `DP02_0054`, "Enrolled in nursery school or preschool": count.
    5. `DP03_0062`, "Median household income (2024 dollars)": dollars.
    6. They are declared per vintage beside the figure list (`TEASER_FIGURE_CODES`), and the whole list is checked as for every report.
7. A program whose address its regulator withholds has no location, so no search finds it. When the job's kinds include family or group family day care, the page says "Programs whose address the regulator withholds are not counted."
8. The target address never appears. The page calls the target "your location" and names only the prospect's organization. "Public data as of <date>" in the footer gives the earlier of the two provider datasets' as-of dates, and is left out when either dataset has no run by the cut-off.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Provider | One licensed or permitted childcare program, as listed by a regulator under its own identifier. Several providers can share a location. |
| Operator | The business that runs one or more providers. |
| Target address | The location a report is about: the client's center or a site the client is considering. |
| Competitor | The providers one operator runs at one location within the report radius of the target address. |
| Research job | One competitor analysis for a target address, for a client report or a prospect, from its draft to its completion or cancellation. |
| Contact | One call, website visit, or email with one competitor, with its date, method, and researcher. |
| Observation | One recorded fact within a contact, with the contact's date, method, and researcher. |
| Age group | A provider's own age band, such as "Infants, 6–18 months", with its limits in whole months. |
| Schedule | A provider's own attendance pattern, such as "Full time, 5 days per week". |
| Slot | What one value answers: the price of one age group on one schedule, the availability of one age group, one kind of discount or charge, or one single fact. |
| Licensed capacity | The maximum number of children a provider may serve, as published by its regulator. |
| Report | One generated competitor analysis of one research job: its record and its PDF, with a number. |
| Teaser | A one-page preview of the market around a prospect's location, generated from a Prospect research job: a report record of kind Teaser and its PDF. |
| Market preview | The teaser's printed name, used on the page, in its footer, its file name, and its PDF title. |
| Cut-off | The moment a report or teaser was generated; only contacts entered and retractions made by then count in it. |
| Monthly equivalent | A price converted to an approximate amount per month when a report is rendered, never stored. |
| Reference age | An age in months at which the tuition comparison compares competitors. |
| Repeat job | A client report job prepared from an earlier one to report again on the same client and target. |
| Compared report | The predecessor's latest final report with listed competitors, which a repeat job's report is compared with. |
| Stated value | What a value says for comparison: a price's amount, period, and discount flag; availability's status; a discount's or charge's value and frequency, or none offered. |
| Run | Consecutive contacts that recorded one stated value of a slot. |
| Subscription | A client's standing order for repeat reports on a cadence, starting from one job. |
| Due date | A date on which a subscription's next report is due, a whole number of cadences after its start date. |
| Transit stop | A stored subway station or bus stop. |
| Station group | Subway stations that share a name and are joined by in-system transfers, presented as one station. |
| Line | A route as riders know it, by its rider-facing name. |
| Catchment | The distance within which transit counts: 0.5 mi for stations and 0.25 mi for bus stops. |
