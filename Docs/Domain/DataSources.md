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

Published by the NY State Office of Children and Family Services on data.ny.gov ([catalog entry](https://catalog.data.gov/dataset/child-care-regulated-programs)). One row per regulated program, statewide.

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

### Overlap between the two

Overlap is measured, not assumed, by matching on normalized address and zip code, then normalized name, then coordinates.

1. The two datasets never list the same program: their program types are disjoint.
2. They do share locations. An operator often holds a city permit for its infant-toddler or preschool programs and a state registration for a school-age (`SACC`) program at the same address, usually under the same or a similar name. Family and group family day care homes also occasionally share a street address with a center under a different name, so an address match alone does not establish a shared operator.
3. Each publisher geocodes independently, so the coordinates of one location differ between the datasets by tens of meters and occasionally by more than 100 meters. Coordinates support a match made on address and name; they never decide one alone.

### Providers and competitors

A competitor is what a report presents: the providers one operator runs at one location, drawn from either dataset.

1. A provider keeps the regulator's unit and identifier: one NY State `Facility ID` or one NYC Health `DCID`. A center with an infant-toddler and a preschool program is two providers and one competitor.
2. Providers join one competitor only when their normalized address, zip code, and normalized name all match. The rule is the same within one dataset and across both.
3. Providers at one address whose names are similar but not equal are flagged for a researcher to confirm before they join. Providers at one address under unrelated names stay separate competitors, as when a school-based program and an unrelated center share a building.
4. Every provider kind in the table above is in scope. School-based programs are competitors for the ages they serve and are labelled as school-based, because their capacity is often unpublished.
5. Rows without coordinates in either dataset are geocoded from their published address with one geocoder shared by both datasets. An omitted address is never geocoded.

## Demographics

US Census Bureau American Community Survey 5-year estimates, through the Census Data API.

1. Geography for a zip-code view is the ZIP Code Tabulation Area. Tabulation areas approximate postal zip codes but are not identical; reports label them as zip codes only with that caveat available.
2. Data profile tables: `DP05` demographics and age, `DP03` income and employment, `DP02` education, `DP04` housing.
3. Age brackets are fixed by the Census: "Under 5 years" and "5 to 9 years". A report figure such as "children 0–5" states which bracket it uses.
4. Every figure carries its table, survey vintage, and margin of error, so a report can cite it.

## Transit

MTA static GTFS feeds provide subway and bus stop locations for the "nearest bus stop" and "nearest train station" distances in reports.

## Mystery-shopping observations

Proprietary data collected by Axisence researchers by phone, website, and email. A competitor profile records:

1. Operations: weeks open per year, opening hours, inspection status, number of places.
2. Availability: current openings or waitlist, per age group.
3. Admissions: minimum booking, multi-day discounts, sibling discount, other discounts and incentives, date of the last fee rise.
4. Fees: per schedule (full time, 4 days, 3 days) and per age group, with the standard rate, the monthly rate, and any discount.
5. Other charges: registration fee, deposit, ad hoc charges.
6. Online presence: website traffic, social media profiles and activity, review ratings.
7. Positioning: unique selling points and a strengths, weaknesses, opportunities, and threats summary.

Rules for observations:

1. Every observation records the competitor, the date, the method (call, website, email), and the researcher. Prices change, so observations are never overwritten; a newer observation supersedes an older one for display while both are kept.
2. Prices keep their stated period (daily, weekly, monthly) and the provider's own age-group and schedule definitions. Age bands differ between providers, so "infant" in one center is not assumed to match "infant" in another.
3. Call notes and contact details are personal data and stay out of reports, fixtures, and logs.
4. Inspection status is an observation like any other, with its date and method. The NYC Health inspection dataset (`dsg6-ifza`) is frozen, so it is not a source of current status.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Provider | One licensed or permitted childcare program, as listed by a regulator under its own identifier. Several providers can share a location. |
| Operator | The business that runs one or more providers. |
| Target address | The location a report is about: the client's center or a site the client is considering. |
| Competitor | The providers one operator runs at one location within the report radius of the target address. |
| Observation | One recorded fact from mystery shopping, with its date, method, and researcher. |
| Licensed capacity | The maximum number of children a provider may serve, as published by its regulator. |
