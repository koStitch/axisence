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

### NYC Health dataset: Active NYC Health Code Regulated Child Care Programs

Published daily by the NYC Department of Health and Mental Hygiene on NYC Open Data, dataset identifier `gy3q-4tzp` ([dataset page](https://data.cityofnewyork.us/Health/Active-NYC-Health-Code-Regulated-Child-Care-Progra/gy3q-4tzp)). Metadata, including column names, is available from `https://data.cityofnewyork.us/api/views/gy3q-4tzp.json`.

1. Covers active Group Child Care programs (NYC Health Code Article 47) and School-Based Child Care programs (Article 43).
2. Programs with a preliminary, suspended, or closed status are excluded. A provider disappearing from the dataset means it is no longer active, so ingestion records disappearance rather than deleting the provider.
3. "Children Allowed in Care" is the maximum capacity the space allows. Actual capacity depends on staffing, so reports call it licensed capacity, never enrollment.

### Overlap between the two

The two datasets should not overlap by program type, but overlap is measured, not assumed: matching on normalized name, address, and coordinates decides whether a New York City program appears in both.

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

## Vocabulary

| Term | Meaning |
| --- | --- |
| Provider | One licensed childcare location, as listed by a regulator. |
| Operator | The business that runs one or more providers. |
| Target address | The location a report is about: the client's center or a site the client is considering. |
| Competitor | A provider within the report radius of the target address. |
| Observation | One recorded fact from mystery shopping, with its date, method, and researcher. |
| Licensed capacity | The maximum number of children a provider may serve, as published by its regulator. |
