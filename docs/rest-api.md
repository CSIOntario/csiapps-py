# CSIAPPS REST API

`csiapps` includes a generic `make_request()` function for the CSIAPPS REST API,
plus registration helpers (`fetch_org_options()`, `fetch_profiles()`,
`fetch_profile()`). See the
[CSIAPPS Swagger docs](https://apps.csiontario.ca/api/swagger/) for every
endpoint, parameter, and response shape.

To reach the **real** API you need an access token in `CSIAPPS_ACCESS_TOKEN`
(outside a Shiny app) or the per-session token stored by `server_wrapper()`
(inside one).

!!! note "Sandbox mode is the default"
    The **warehouse** `make_request()` calls below are routed to a local,
    in-memory warehouse rather than the production API, so you can develop
    safely. Not every endpoint is emulated: the registration and auth endpoints
    are **not**, and require `sandbox=False` (R: `sandbox = FALSE`). See
    [Which calls work in sandbox](#which-calls-work-in-sandbox) at the end, and
    the dedicated [Sandbox mode](sandbox.md) article for the full local
    workflow.

## Authorization status

The simplest endpoint is `/api/csiauth/me/`, which reports the authenticated
user. A successful response means your token is valid. Set your institute first.

=== "R"

    ```r
    set_institute("csiontario")   # or "csipacific" (the default)

    result <- make_request(
      endpoint = "api/csiauth/me/",
      sandbox  = FALSE            # /me is not emulated; it needs the real API
    )
    ```

=== "Python"

    ```python
    csiapps.set_institute("csiontario")   # or "csipacific" (the default)

    result = csiapps.make_request(
        "api/csiauth/me/",
        sandbox=False,            # /me is not emulated; it needs the real API
    )
    ```

!!! warning "Sandbox note"
    `/api/csiauth/me/` is **not** emulated by the sandbox, so calling it in the
    default sandbox mode raises a 501. Pass `sandbox=False` to reach the real
    endpoint. Inside a wrapped Shiny app, `/me` is loaded for you by the login
    simulation — you don't call it directly.

## Registration API

The registration API exposes organisations and profiles. The examples below
pass `sandbox=False` to contact the **real** registration API; in sandbox mode
(the default) these same helpers read a local dummy registry instead — see the
[Sandbox mode](sandbox.md) article.

### Organisations

`fetch_org_options()` returns all organisations your token can see, in a shape
ready for a select input.

=== "R"

    ```r
    set_institute("csiontario")

    orgs <- fetch_org_options(sandbox = FALSE)
    # list(list(label = "CSI Ontario", value = 1L), ...)  -> selectInput() choices
    ```

=== "Python"

    ```python
    csiapps.set_institute("csiontario")

    orgs = csiapps.fetch_org_options(sandbox=False)
    # {1: "CSI Ontario", ...}  -> ui.input_select(choices=...) directly
    ```

!!! info "Deliberate divergence"
    The return **shape** differs by design. R returns `label`/`value` pairs for
    R's `selectInput()`; Python returns a `{value: label}` dict, which is what
    Shiny for Python's `ui.input_select(choices=...)` expects (the R shape would
    raise `TypeError: unhashable type: 'dict'`). Same data, framework-native
    shape.

Both are thin wrappers over a `GET` to `api/registration/organization/`; you can
make the same call directly with `make_request()`:

=== "R"

    ```r
    resp <- make_request(
      endpoint = "api/registration/organization/",
      query    = list(limit = 1000),
      sandbox  = FALSE            # registration is NOT emulated in sandbox
    )
    orgs <- resp$results          # each element has $id and $name
    ```

=== "Python"

    ```python
    resp = csiapps.make_request(
        "api/registration/organization/",
        query={"limit": 1000},
        sandbox=False,            # registration is NOT emulated in sandbox
    )
    orgs = resp["results"]        # each element has "id" and "name"
    ```

### Profiles

`fetch_profiles()` returns all profiles your token can see, auto-paginating.
Filter by organisation with `sport_org_id`.

=== "R"

    ```r
    profiles <- fetch_profiles(sandbox = FALSE)

    # Filter to a specific organisation
    profiles <- fetch_profiles(
      filters = list(sport_org_id = 42L),
      sandbox = FALSE
    )
    ```

=== "Python"

    ```python
    profiles = csiapps.fetch_profiles(sandbox=False)

    # Filter to a specific organisation
    profiles = csiapps.fetch_profiles(
        filters={"sport_org_id": 42},
        sandbox=False,
    )
    ```

Profiles come back as deeply nested records. Flatten them into a table for
display or matching:

=== "R"

    ```r
    profile_df <- do.call(rbind, lapply(profiles, function(p) {
      data.frame(
        id         = p$id,
        first_name = p$person$first_name %||% NA_character_,
        last_name  = p$person$last_name  %||% NA_character_,
        email      = p$person$email      %||% NA_character_
      )
    }))
    ```

=== "Python"

    ```python
    import pandas as pd

    # flatten_profile() turns one nested profile into scalar fields
    profile_df = pd.DataFrame(csiapps.flatten_profile(p) for p in profiles)
    ```

    `flatten_profile()` is a Python-only convenience — Shiny for Python's
    `render.data_frame` rejects nested dicts. See the
    [parity checklist](parity.md).

`fetch_profile()` retrieves a single profile by id:

=== "R"

    ```r
    profile <- fetch_profile(profile_id = 123L, sandbox = FALSE)
    profile$person$first_name
    ```

=== "Python"

    ```python
    profile = csiapps.fetch_profile(123, sandbox=False)
    profile["person"]["first_name"]
    ```

## Data warehouse ingestion

Ingesting records is a `POST` to `api/warehouse/ingestion/primary/`. You supply
the `source` (uuid) of the target data source, a list of `records`, and
optionally the `subject_field` that uniquely assigns each record to a user. The
records must comply with the data source's JSON schema.

!!! note "You don't choose the source uuid"
    The `source` uuid is provisioned by the CSIAPPS team when they set up the
    remote warehouse for your app, and supplied through a `SOURCE_UUID`
    environment variable — the app developer never hard-codes it. Read it from
    the environment so the same code runs in development and production. See
    [Sandbox mode](sandbox.md) for how this works locally.

### JSON schema

Consider this [JSON Schema](https://json-schema.org/learn):

```json
{
  "title": "A registration form",
  "type": "object",
  "required": ["id", "firstName", "lastName"],
  "properties": {
    "id":        {"type": "string",  "title": "ID"},
    "firstName": {"type": "string",  "title": "First name"},
    "lastName":  {"type": "string",  "title": "Last name"},
    "age":       {"type": "integer", "title": "Age"},
    "telephone": {"type": "string",  "title": "Telephone", "minLength": 10}
  }
}
```

Here `id` is the field that uniquely assigns each record to a subject. Retrieve
the registered schema through the same call production uses — it is nested under
`head_primary_definition.schema`, exactly like the real API:

=== "R"

    ```r
    data_source <- make_request(
      endpoint = paste0("api/warehouse/data-sources/", Sys.getenv("SOURCE_UUID"))
    )
    schema <- data_source$head_primary_definition$schema
    ```

=== "Python"

    ```python
    import os

    data_source = csiapps.make_request(
        "api/warehouse/data-sources/" + os.environ["SOURCE_UUID"]
    )
    schema = data_source["head_primary_definition"]["schema"]
    ```

### Prepare and validate records

Build records that comply with the schema. The warehouse validates them at
ingestion, but you can validate up front with the same validator each language
uses:

=== "R"

    ```r
    records <- list(
      list(id = "xxxx", firstName = "John", lastName = "Doe",   age = 30, telephone = "1234567890"),
      list(id = "yyyy", firstName = "Jane", lastName = "Smith", age = 25, telephone = "0987654321")
    )

    # jsonvalidate (Ajv). Set auto_unbox = TRUE when serializing.
    json_schema <- jsonvalidate::json_schema$new(jsonlite::toJSON(schema, auto_unbox = TRUE))
    stopifnot(all(sapply(records, function(r)
      json_schema$validate(jsonlite::toJSON(r, auto_unbox = TRUE)))))
    ```

=== "Python"

    ```python
    from jsonschema import Draft7Validator

    records = [
        {"id": "xxxx", "firstName": "John", "lastName": "Doe",   "age": 30, "telephone": "1234567890"},
        {"id": "yyyy", "firstName": "Jane", "lastName": "Smith", "age": 25, "telephone": "0987654321"},
    ]

    validator = Draft7Validator(schema)
    for r in records:
        assert not list(validator.iter_errors(r))
    ```

    Python validates with `jsonschema` (Draft 7) where R uses Ajv via
    `jsonvalidate`; wording differs on edge cases (see the
    [parity checklist](parity.md)).

### Ingest

=== "R"

    ```r
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

## Data record retrieval

Retrieval is a `GET` on `api/warehouse/data-records`. The only required query
parameter is `source_uuid`; set `paginate=True` to fetch all matching records.

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

## Which calls work in sandbox

`make_request()` runs against the **local sandbox** by default. The sandbox
emulates the **warehouse** endpoints but **not** the registration or auth
endpoints — calls it does not emulate raise a **501** unless you pass
`sandbox=False`:

| Endpoint (`make_request`)            | Default sandbox | `sandbox=False` | Sandbox alternative                    |
|--------------------------------------|-----------------|-----------------|----------------------------------------|
| `api/csiauth/me/`                    | ✗ 501           | ✓               | login simulation (Shiny)               |
| `api/registration/organization/`     | ✗ 501           | ✓               | `fetch_org_options()`                  |
| `api/registration/profile/`          | ✗ 501           | ✓               | `fetch_profiles()` / `fetch_profile()` |
| `api/warehouse/data-sources/{uuid}`  | ✓ emulated      | ✓               | —                                      |
| `api/warehouse/ingestion/primary/`   | ✓ emulated      | ✓               | —                                      |
| `api/warehouse/data-records`         | ✓ emulated      | ✓               | —                                      |

In short: the **warehouse** examples work in both modes, while the
**registration/auth** examples work only against the real API (`sandbox=False`).
For registration data in sandbox, use the `fetch_*` helpers, which read the
local dummy registry directly. The full sandbox workflow is documented in
**[Sandbox mode](sandbox.md)**.
