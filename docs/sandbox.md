# Sandbox mode

`csiapps` ships with a **local sandbox** that emulates the CSIAPPS data
warehouse and registration API entirely on your machine — no network, no
authentication, and no risk of writing test data to production. It runs the full
**schema → validate → ingest → retrieve** warehouse workflow and the
**register orgs → register athletes → fetch profiles** registration workflow,
using the *same* function calls your production app will use.

**Sandbox mode is enabled by default.** Every request routes to a local,
in-memory warehouse instead of the REST API, and every sandboxed call prints a
`csiapps sandbox:` message so it is always visible that no real API call was
made. When your app is ready, you disable sandbox mode at deployment time — with
no changes to your app code.

## What is real and what is dummy

Sandbox mode fakes everything except the one thing it cannot fake safely — your
identity:

| Concern | In sandbox mode |
| --- | --- |
| **Login / `/me` identity** | Emulated using your real `CSIAPPS_ACCESS_TOKEN`, used *only* to load `/me` so a wrapped app's header shows your real name. |
| **Sport organizations** | Dummy. Created locally with `create_sport_org()`. |
| **Athlete profiles** | Dummy. Created locally with `create_profile()`. |
| **Warehouse schemas & records** | Dummy. Registered and ingested into an in-memory warehouse; nothing leaves your machine. |

No real client data is ever read except your own `/me` identity.

## Enabling and disabling

`is_sandbox_mode()` reports whether the sandbox is on, and supplies the default
for the `sandbox` argument of every sandbox-aware function. Resolution order:

1. The explicit override, if set, always wins (R: the `csiapps.sandbox` option;
   Python: `set_sandbox_mode(...)`).
2. Otherwise `CSIAPPS_ENV` — only the exact value `"production"` disables it.
3. Otherwise, sandbox mode is **on**.

=== "R"

    ```r
    is_sandbox_mode()   # TRUE by default

    # Turn sandbox OFF (route to the production warehouse)
    options(csiapps.sandbox = FALSE)   # ...or set CSIAPPS_ENV=production

    # Turn sandbox back ON / restore the default
    options(csiapps.sandbox = NULL)
    ```

=== "Python"

    ```python
    csiapps.is_sandbox_mode()   # True by default

    # Turn sandbox OFF (route to the production warehouse)
    csiapps.set_sandbox_mode(False)   # ...or set CSIAPPS_ENV=production

    # Turn sandbox back ON / restore the default
    csiapps.set_sandbox_mode(None)
    ```

You can also override the global setting for a **single call** with the
`sandbox` argument, which every relevant function accepts:

=== "R"

    ```r
    # One real org lookup while the sandbox is otherwise on
    orgs <- fetch_org_options(sandbox = FALSE)
    ```

=== "Python"

    ```python
    # One real org lookup while the sandbox is otherwise on
    orgs = csiapps.fetch_org_options(sandbox=False)
    ```

!!! tip "Best practice for the sandbox → production transition"
    **Do not set the sandbox toggle in your app code** — rely on the default.
    Hard-coding sandbox *on* would override the deployer's attempt to turn it
    off, silently serving sandbox data in production. Turn it *off* only at
    deployment, in a location the deployer controls (R: `options(...)` in
    `global.R` / `Rprofile.site`; either language: `CSIAPPS_ENV=production` in
    the production environment). The same code then runs unchanged in both.

## The registration workflow

In sandbox mode the registration helpers read from a **local dummy registry**.
Seed it with `create_sport_org()` and `create_profile()`; the fetch helpers then
return dummy data in the same shape the real API uses.

### 1. Register sport organizations

`create_sport_org()` registers a dummy sport org. Its `name` becomes the sport
name of every athlete created under it. Omit the id to auto-generate an unused
one in `1:999`, or pin any positive integer up to 999. Ids must be unique.

=== "R"

    ```r
    # Auto-generated id
    org <- create_sport_org("Rowing Canada")
    org$id

    # ...or pin a specific id
    create_sport_org("Athletics Canada", id = 42L)
    ```

=== "Python"

    ```python
    # Auto-generated id
    org = csiapps.create_sport_org("Rowing Canada")
    org["id"]

    # ...or pin a specific id
    csiapps.create_sport_org("Athletics Canada", id=42)
    ```

Calling `create_sport_org()` outside sandbox mode warns and has no effect.

### 2. Register athletes under a sport org

`create_profile()` generates `n` random athlete profiles under an **existing**
sport org. Each profile is production-shaped, so downstream code sees the same
structure the real API returns. Supply explicit names (both vectors/lists, or
neither) or let them be generated.

=== "R"

    ```r
    # 5 athletes with unique random names (drawn from `babynames`)
    create_profile(5, org$id)

    # ...or supply explicit names
    create_profile(
      2, org$id,
      first_names = c("Ada", "Blair"),
      last_names  = c("Nkemelu", "Okafor")
    )
    ```

=== "Python"

    ```python
    # 5 athletes with unique random names (generated with `faker`)
    csiapps.create_profile(5, org["id"])

    # ...or supply explicit names
    csiapps.create_profile(
        2, org["id"],
        first_names=["Ada", "Blair"],
        last_names=["Nkemelu", "Okafor"],
    )
    ```

The org must already exist. Each athlete's sport id is the `sport_org_id`, which
is the field the `sport_org_id` filter matches in `fetch_profiles`.

### 3. Fetch orgs and athlete profiles

The same fetch helpers your production app uses read the registry back.
`fetch_org_options()` returns choices for a select input, and `fetch_profiles()`
returns athletes, optionally filtered by organisation (**only the
`sport_org_id` filter is honoured in the sandbox**).

=== "R"

    ```r
    fetch_org_options()   # list(list(label = "Rowing Canada", value = 123L), ...)

    # All dummy athletes
    profiles <- fetch_profiles()

    # Athletes for one organisation
    profiles <- fetch_profiles(filters = list(sport_org_id = org$id))

    # A single athlete by id (NULL if none registered)
    fetch_profile(profile_id = 1L)
    ```

=== "Python"

    ```python
    csiapps.fetch_org_options()   # {123: "Rowing Canada", ...}

    # All dummy athletes
    profiles = csiapps.fetch_profiles()

    # Athletes for one organisation
    profiles = csiapps.fetch_profiles(filters={"sport_org_id": org["id"]})

    # A single athlete by id (None if none registered)
    csiapps.fetch_profile(1)
    ```

!!! note
    Only warehouse endpoints are routed through `make_request()`. Calling
    `make_request("api/registration/...")` in the sandbox raises a 501 — use the
    `fetch_*` helpers above, which read the dummy registry instead.

## The data warehouse workflow

The sandbox emulates three warehouse endpoints through the ordinary
`make_request()` interface, validating records against a registered JSON schema
and storing accepted payloads locally.

### 1. Register a data source (schema)

!!! note "You never handle the real data source uuid"
    In production a CSIAPPS insider provisions the warehouse and supplies the
    source uuid through a `SOURCE_UUID` environment variable. Reference the
    source **only** from the environment — never a hard-coded uuid — so the same
    code runs in development and production. In development set `SOURCE_UUID`
    yourself to any throwaway placeholder; the sandbox accepts any identifying
    string.

The schema may be an object, a JSON string, or a path to a JSON file.

=== "R"

    ```r
    Sys.setenv(SOURCE_UUID = "dev-placeholder")

    schema <- '{
      "type": "object",
      "required": ["id", "firstName", "lastName"],
      "properties": {
        "id":        {"type": "string"},
        "firstName": {"type": "string"},
        "lastName":  {"type": "string"},
        "age":       {"type": "integer"},
        "telephone": {"type": "string", "minLength": 10}
      }
    }'

    register_sandbox_schema(Sys.getenv("SOURCE_UUID"), schema)
    ```

=== "Python"

    ```python
    import os
    os.environ["SOURCE_UUID"] = "dev-placeholder"

    schema = {
        "type": "object",
        "required": ["id", "firstName", "lastName"],
        "properties": {
            "id":        {"type": "string"},
            "firstName": {"type": "string"},
            "lastName":  {"type": "string"},
            "age":       {"type": "integer"},
            "telephone": {"type": "string", "minLength": 10},
        },
    }

    csiapps.register_sandbox_schema(os.environ["SOURCE_UUID"], schema)
    ```

You can retrieve the registered schema back through the same call production
uses — nested under `head_primary_definition.schema`, exactly like the real API
(see the [REST API](rest-api.md) article).

### 2. Prepare and ingest records

Records must comply with the schema; the sandbox validates every record before
accepting it, stores accepted payloads in memory, and writes a JSON copy to disk
for inspection.

=== "R"

    ```r
    records <- list(
      list(id = "xxxx", firstName = "John", lastName = "Doe",   age = 30, telephone = "1234567890"),
      list(id = "yyyy", firstName = "Jane", lastName = "Smith", age = 25, telephone = "0987654321")
    )

    result <- make_request(
      endpoint = "api/warehouse/ingestion/primary/",
      method   = "POST",
      body = list(
        source        = Sys.getenv("SOURCE_UUID"),
        records       = records,
        subject_field = "id"
      )
    )
    ```

=== "Python"

    ```python
    records = [
        {"id": "xxxx", "firstName": "John", "lastName": "Doe",   "age": 30, "telephone": "1234567890"},
        {"id": "yyyy", "firstName": "Jane", "lastName": "Smith", "age": 25, "telephone": "0987654321"},
    ]

    result = csiapps.make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={
            "source": os.environ["SOURCE_UUID"],
            "records": records,
            "subject_field": "id",
        },
    )
    ```

Records that fail schema validation are **rejected** with an error naming which
records failed and why — so you exercise the same failure handling as
production:

=== "R"

    ```r
    # Missing the required `id` field -> rejected before anything is stored
    bad <- list(list(firstName = "No", lastName = "Id"))
    make_request(
      endpoint = "api/warehouse/ingestion/primary/",
      method   = "POST",
      body     = list(source = Sys.getenv("SOURCE_UUID"), records = bad, subject_field = "id")
    )
    #> Error: ... validation failed for record(s) 1 ...
    ```

=== "Python"

    ```python
    # Missing the required "id" field -> rejected before anything is stored
    bad = [{"firstName": "No", "lastName": "Id"}]
    csiapps.make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={"source": os.environ["SOURCE_UUID"], "records": bad, "subject_field": "id"},
    )
    # RuntimeError: ... validation failed for record(s) 1 ...
    ```

### 3. Pull records back

Retrieval is a `GET` on the data-records endpoint; the only required query
parameter is `source_uuid`. Set pagination on to return all matching records
(the sandbox returns a single page, mirroring the paginated shape).

=== "R"

    ```r
    records <- make_request(
      endpoint = "api/warehouse/data-records",
      query    = list(source_uuid = Sys.getenv("SOURCE_UUID")),
      paginate = TRUE
    )
    ```

=== "Python"

    ```python
    records = csiapps.make_request(
        "api/warehouse/data-records",
        query={"source_uuid": os.environ["SOURCE_UUID"]},
        paginate=True,
    )
    ```

Each record is wrapped in the same envelope the real API uses (`id`,
`dataset_uuid`, `data`, `subject`, `created_at`, `updated_at`). If a record's
`subject_field` value matches an athlete registered with `create_profile()`, the
sandbox resolves `subject` to that athlete; otherwise it is null. Resolution
happens at **read time**, so athletes registered *after* ingestion backfill on
the next read.

## Inspecting and resetting

Accepted payloads are written as pretty-printed JSON, one folder per data
source, so you can inspect the exact JSON that would have been sent to the API.
Clear a single source or the whole sandbox to reset state between tests.

=== "R"

    ```r
    browse_sandbox(Sys.getenv("SOURCE_UUID"))  # open the payload folder
    browse_sandbox()                           # open the sandbox root

    clear_sandbox(Sys.getenv("SOURCE_UUID"))   # clear one source
    clear_sandbox()                            # clear everything
    ```

=== "Python"

    ```python
    csiapps.browse_sandbox(os.environ["SOURCE_UUID"])  # open the payload folder
    csiapps.browse_sandbox()                           # open the sandbox root

    csiapps.clear_sandbox(os.environ["SOURCE_UUID"])   # clear one source
    csiapps.clear_sandbox()                            # clear everything
    ```

Sandbox state lasts only for the session.

## A complete end-to-end example

Registration and warehouse workflows in one script, all local, no network:

=== "R"

    ```r
    library(csiapps)

    # 1. (Optional) simulate login for a wrapped app's /me header
    Sys.setenv(CSIAPPS_ACCESS_TOKEN = "your-dev-access-token")
    set_institute("csiontario")
    check_secrets()

    # 2. Point the app at a placeholder source
    Sys.setenv(SOURCE_UUID = "dev-placeholder")

    # 3. Seed the registration registry
    org <- create_sport_org("Rowing Canada")
    create_profile(3, org$id, first_names = c("Ada", "Blair", "Cai"),
                              last_names  = c("Nkemelu", "Okafor", "Zhang"))

    # 4. Read it back like production
    athletes <- fetch_profiles(filters = list(sport_org_id = org$id))

    # 5. Register a schema and ingest records keyed to those athletes
    register_sandbox_schema(Sys.getenv("SOURCE_UUID"), '{
      "type": "object",
      "required": ["athlete_id", "weight"],
      "properties": {"athlete_id": {"type": "integer"}, "weight": {"type": "number"}}
    }')

    make_request(
      endpoint = "api/warehouse/ingestion/primary/",
      method   = "POST",
      body = list(
        source        = Sys.getenv("SOURCE_UUID"),
        records       = list(
          list(athlete_id = athletes[[1]]$id, weight = 72.5),
          list(athlete_id = athletes[[2]]$id, weight = 68.1)
        ),
        subject_field = "athlete_id"
      )
    )

    # 6. Pull the records back; subjects resolve to the registered athletes
    make_request(
      endpoint = "api/warehouse/data-records",
      query    = list(source_uuid = Sys.getenv("SOURCE_UUID")),
      paginate = TRUE
    )

    # 7. Reset when done
    clear_sandbox()
    ```

=== "Python"

    ```python
    import os
    import csiapps

    # 1. (Optional) simulate login for a wrapped app's /me header
    os.environ["CSIAPPS_ACCESS_TOKEN"] = "your-dev-access-token"
    csiapps.set_institute("csiontario")
    csiapps.check_secrets()

    # 2. Point the app at a placeholder source
    os.environ["SOURCE_UUID"] = "dev-placeholder"

    # 3. Seed the registration registry
    org = csiapps.create_sport_org("Rowing Canada")
    csiapps.create_profile(3, org["id"], first_names=["Ada", "Blair", "Cai"],
                           last_names=["Nkemelu", "Okafor", "Zhang"])

    # 4. Read it back like production
    athletes = csiapps.fetch_profiles(filters={"sport_org_id": org["id"]})

    # 5. Register a schema and ingest records keyed to those athletes
    csiapps.register_sandbox_schema(os.environ["SOURCE_UUID"], {
        "type": "object",
        "required": ["athlete_id", "weight"],
        "properties": {"athlete_id": {"type": "integer"}, "weight": {"type": "number"}},
    })

    csiapps.make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={
            "source": os.environ["SOURCE_UUID"],
            "records": [
                {"athlete_id": athletes[0]["id"], "weight": 72.5},
                {"athlete_id": athletes[1]["id"], "weight": 68.1},
            ],
            "subject_field": "athlete_id",
        },
    )

    # 6. Pull the records back; subjects resolve to the registered athletes
    csiapps.make_request(
        "api/warehouse/data-records",
        query={"source_uuid": os.environ["SOURCE_UUID"]},
        paginate=True,
    )

    # 7. Reset when done
    csiapps.clear_sandbox()
    ```

When deployed, disable sandbox mode and let CSIAPPS set `SOURCE_UUID` to the real
warehouse uuid — the app code above is **unchanged**. The schema registration in
step 5 is development-only scaffolding; in production the schema already lives in
the remote warehouse.

## Worked example: integrating third-party vendor data

A common real task is joining a **third-party vendor's** data — a testing
platform, a wearable, a lab system — onto CSIAPPS athletes. CSI Ontario
maintains an **AMS_mapping** data source whose records link each CSIAPPS athlete
(`id`) to that athlete's profile in a vendor system (`vendor_profile_id`,
`vendor_profile_name`). A consumer app reads AMS_mapping to translate the
vendor's own ids into canonical CSIAPPS athlete ids.

In production the mapping already lives in the warehouse. To build and test the
consumer **locally**, you reproduce a miniature AMS_mapping source in the
sandbox, then read it back and join — exactly as production will.

!!! note
    Steps 1–3 below (dummy athletes, schema, ingestion) are **development-only
    scaffolding**. In production the AMS_mapping source is already populated
    upstream; only the read-and-join in step 4 is real app code, and it ships
    **unchanged**. Give each source its own descriptively-named environment
    variable (here `AMS_MAPPING_UUID`) rather than a generic `SOURCE_UUID`.

### 1. Create dummy athletes and note their ids

The `id` column of your mapping must match real athlete ids. In the sandbox
those come from `create_profile()`, which assigns ids in insertion order.

=== "R"

    ```r
    Sys.setenv(AMS_MAPPING_UUID = "dev-ams-mapping")

    org <- create_sport_org("Rowing Canada")
    create_profile(3, org$id,
                   first_names = c("Ada", "Blair", "Cai"),
                   last_names  = c("Nkemelu", "Okafor", "Zhang"))

    athletes <- fetch_profiles(filters = list(sport_org_id = org$id))
    vapply(athletes, function(p) p$id, integer(1))   # e.g. 1 2 3
    ```

=== "Python"

    ```python
    os.environ["AMS_MAPPING_UUID"] = "dev-ams-mapping"

    org = csiapps.create_sport_org("Rowing Canada")
    csiapps.create_profile(3, org["id"],
                           first_names=["Ada", "Blair", "Cai"],
                           last_names=["Nkemelu", "Okafor", "Zhang"])

    athletes = csiapps.fetch_profiles(filters={"sport_org_id": org["id"]})
    [p["id"] for p in athletes]   # e.g. [1, 2, 3]
    ```

### 2. Register the AMS_mapping schema and ingest the mapping

The mapping bridges two id systems: `id` is the CSIAPPS athlete id (matching the
dummy profiles above), and `vendor_profile_id` is the athlete's id in **your**
vendor system — a real value from your data. Keep every base mapping
`active = true`, and ingest with `subject_field = "id"` so each mapping links to
its CSIAPPS athlete. `register_sandbox_schema()` also accepts a path to a
`.schema.json` file.

=== "R"

    ```r
    register_sandbox_schema(Sys.getenv("AMS_MAPPING_UUID"), "mapping.schema.json")

    mapping <- list(
      list(id = 1, vendor = "VendorX", vendor_profile_id = "VX-8841",
           vendor_profile_name = "Ada N.",   active = TRUE),
      list(id = 2, vendor = "VendorX", vendor_profile_id = "VX-8842",
           vendor_profile_name = "Blair O.", active = TRUE),
      list(id = 3, vendor = "VendorX", vendor_profile_id = "VX-9001",
           vendor_profile_name = "Cai Z.",   active = TRUE)
    )

    make_request(
      endpoint = "api/warehouse/ingestion/primary/",
      method   = "POST",
      body = list(source = Sys.getenv("AMS_MAPPING_UUID"),
                  records = mapping, subject_field = "id")
    )
    ```

=== "Python"

    ```python
    csiapps.register_sandbox_schema(os.environ["AMS_MAPPING_UUID"], "mapping.schema.json")

    mapping = [
        {"id": 1, "vendor": "VendorX", "vendor_profile_id": "VX-8841",
         "vendor_profile_name": "Ada N.",   "active": True},
        {"id": 2, "vendor": "VendorX", "vendor_profile_id": "VX-8842",
         "vendor_profile_name": "Blair O.", "active": True},
        {"id": 3, "vendor": "VendorX", "vendor_profile_id": "VX-9001",
         "vendor_profile_name": "Cai Z.",   "active": True},
    ]

    csiapps.make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={"source": os.environ["AMS_MAPPING_UUID"],
              "records": mapping, "subject_field": "id"},
    )
    ```

Keep `vendor_profile_id` a **string** (vendor id formats vary). If you are
loading the mapping from a CSV, read that column as text before building the
records.

### 3. Read the mapping back and join your vendor data

This is the only part that ships to production. Pull the AMS_mapping records,
keep the active ones, and join your real vendor data onto them by
`vendor_profile_id` — every vendor measurement now carries a canonical CSIAPPS
`id`, which you can enrich with `fetch_profile()`.

=== "R"

    ```r
    resp <- make_request(
      endpoint = "api/warehouse/data-records",
      query    = list(source_uuid = Sys.getenv("AMS_MAPPING_UUID")),
      paginate = TRUE
    )

    mapping <- do.call(rbind, lapply(resp[[1]]$results, function(r) data.frame(
      id                = r$data$id,
      vendor_profile_id = r$data$vendor_profile_id,
      active            = r$data$active,
      stringsAsFactors  = FALSE
    )))
    mapping <- mapping[mapping$active, ]

    # your REAL third-party data, keyed by the vendor's own id
    vendor_data <- data.frame(
      vendor_profile_id = c("VX-8841", "VX-8842", "VX-9001"),
      resting_hr        = c(52, 58, 49),
      stringsAsFactors  = FALSE
    )

    joined <- merge(vendor_data, mapping, by = "vendor_profile_id")
    joined$athlete <- vapply(joined$id, function(i) {
      p <- fetch_profile(profile_id = i)
      paste(p$person$first_name, p$person$last_name)
    }, character(1))
    joined
    ```

=== "Python"

    ```python
    import pandas as pd

    resp = csiapps.make_request(
        "api/warehouse/data-records",
        query={"source_uuid": os.environ["AMS_MAPPING_UUID"]},
        paginate=True,
    )

    mapping = pd.DataFrame(
        {"id": r["data"]["id"],
         "vendor_profile_id": r["data"]["vendor_profile_id"],
         "active": r["data"]["active"]}
        for r in resp[0]["results"]
    )
    mapping = mapping[mapping["active"]]

    # your REAL third-party data, keyed by the vendor's own id
    vendor_data = pd.DataFrame({
        "vendor_profile_id": ["VX-8841", "VX-8842", "VX-9001"],
        "resting_hr": [52, 58, 49],
    })

    joined = vendor_data.merge(mapping, on="vendor_profile_id")
    joined["athlete"] = [
        " ".join([(p := csiapps.fetch_profile(i))["person"]["first_name"],
                  p["person"]["last_name"]])
        for i in joined["id"]
    ]
    joined
    ```

!!! note "The `active` flag and append-only history"
    AMS_mapping is append-only: a mapping is corrected or removed by appending a
    new record (a tombstone with `active = false`, plus any corrected record),
    and the most recent record per identity wins. In **production** the warehouse
    returns only currently-active mappings (filtered server-side). The
    **sandbox returns every record you ingest**, unfiltered — so if you ingest
    corrections while testing, reduce to the latest record per identity and keep
    the active ones yourself to mirror what production returns.

### Going to production

Disable sandbox mode and let CSIAPPS set `AMS_MAPPING_UUID` to the real source
uuid. Steps 1–2 fall away entirely — the real AMS_mapping source is already
populated upstream — and the step 3 read-and-join runs **unchanged**, now
returning server-filtered active mappings whose subjects resolve against real
registrations.

## Limitations

The sandbox faithfully simulates the *schema contract*, not the warehouse.
Anything that depends on server-side state will differ from production:

- **Validation parity is approximate.** The sandbox validates records against
  the JSON Schema only (R: Ajv via `jsonvalidate`; Python: `jsonschema`,
  Draft 7), catching the most common failures. The real server additionally
  enforces things the schema cannot express — `subject_field` resolution against
  registered profiles, duplicate/dataset handling, and token permissions — and
  validators differ on edge cases (e.g. `format` enforcement). Passing sandbox
  validation is *necessary but not sufficient* for production acceptance.
- **`subject` linkage is emulated only for registered athletes.** On retrieval
  the sandbox resolves each record's `subject_field` value against athletes
  created with `create_profile()`, returning the matched athlete or null.
  Production would reject an unresolved subject; the sandbox accepts it silently.
- **Only warehouse endpoints go through `make_request()`.** Registration
  endpoints raise a 501; use the `fetch_*` helpers. Any other endpoint is
  unsupported.
