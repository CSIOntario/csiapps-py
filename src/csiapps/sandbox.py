"""Local, in-memory emulation of the CSIAPPS data warehouse and registration API.

Port of ``R/sandbox.R``. Lets developers run the full
schema -> validate -> ingest -> retrieve workflow with no network and no auth,
plus a dummy sport-org/athlete registry for the ``fetch_*`` helpers.

Two dependency swaps from the R package:

* schema validation uses ``jsonschema`` (Draft 7) where R used Ajv via
  ``jsonvalidate``. Validator wording differs on edge cases (see PORTING_PLAN).
* random athlete names use ``faker`` where R used the ``babynames`` dataset.

State lives for the process; :func:`clear_sandbox` resets it (test teardown).
"""

import json
import os
import random
import re
import secrets
import shutil
import tempfile
import warnings
import webbrowser
from datetime import date, datetime, timedelta, timezone

from jsonschema import Draft7Validator

from . import config
from .config import _message

# ---- session state (mirror of R's .sandbox_env) ----
_state = {
    "schemas": {},   # source_uuid -> schema dict
    "records": {},   # source_uuid -> list of wrapped record dicts
    "orgs": {},      # str(id) -> {id, name, annual_cycle_start}
    "profiles": [],  # list of prod-shaped athlete dicts, insertion order
    "dir": None,     # on-disk payload dir (lazy, per session)
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sandbox_dir() -> str:
    """Lazily create and return the per-session on-disk payload directory."""
    d = _state["dir"]
    if d is None or not os.path.isdir(d):
        d = tempfile.mkdtemp(prefix="csiapps_sandbox_")
        _state["dir"] = d
    return d


def normalize_endpoint(endpoint: str) -> str:
    """Strip leading/trailing slashes so routing tolerates both forms."""
    return endpoint.strip("/")


def sandbox_error(status: int, msg: str):
    # Mirror the real HTTP path's error format so tryCatch-style handling works
    # identically in both modes.
    raise RuntimeError(f"API request failed ({status}): {msg} [csiapps sandbox]")


# ---- schema registration / clearing / browsing -------------------------


def register_sandbox_schema(source_uuid: str, schema: dict | str) -> dict:
    """Register a JSON schema for a data source in the sandbox.

    The schema is what the sandbox validates ingested records against, so
    register one before calling ingestion for that source. Registering again for
    the same ``source_uuid`` replaces the previous schema.

    Args:
        source_uuid: The data-source identifier to register the schema under.
            Must be a non-empty string.
        schema: The JSON Schema (Draft 7) to validate records against. May be a
            dict, a JSON string, or a path to a ``.json`` file.

    Returns:
        dict: The parsed schema that was stored (useful when a path or JSON
        string was passed in).

    Raises:
        ValueError: If ``source_uuid`` is empty, or ``schema`` is not a dict,
            JSON string, or readable JSON file.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.register_sandbox_schema(
            "hr-source",
            {"type": "object", "required": ["athlete_id", "hr"]},
        )
        ```

    Note:
        Validation uses ``jsonschema`` (Draft 7) where the R package used Ajv via
        ``jsonvalidate``; validator wording can differ on edge cases. Registered
        schemas live for the process — see
        [`clear_sandbox`][csiapps.sandbox.clear_sandbox] to reset.
    """
    if not (isinstance(source_uuid, str) and source_uuid):
        raise ValueError("register_sandbox_schema: `source_uuid` must be a non-empty string.")

    if isinstance(schema, str):
        if os.path.isfile(schema):
            with open(schema) as f:
                schema = json.load(f)
        else:
            schema = json.loads(schema)
    if not isinstance(schema, dict):
        raise ValueError(
            "register_sandbox_schema: `schema` must be a list, a JSON string, "
            "or a path to a JSON file."
        )

    _state["schemas"][source_uuid] = schema
    _message(f"csiapps sandbox: schema registered for source '{source_uuid}'")
    return schema


def clear_sandbox(source_uuid: str | None = None) -> None:
    """Reset sandbox state, entirely or for a single data source.

    Useful in test teardown so registered schemas, ingested records, and
    on-disk payloads do not leak between runs.

    Args:
        source_uuid: If given, clear only that source's schema, records, and
            payload directory. If ``None`` (the default), clear everything:
            all schemas, records, dummy orgs, profiles, and payload
            directories.

    Returns:
        None

    Example:
        ```python
        import csiapps

        csiapps.clear_sandbox("hr-source")   # one source
        csiapps.clear_sandbox()              # everything
        ```
    """
    if source_uuid is None:
        _state["schemas"] = {}
        _state["records"] = {}
        _state["orgs"] = {}
        _state["profiles"] = []
        d = _state["dir"]
        if d and os.path.isdir(d):
            for entry in os.listdir(d):
                path = os.path.join(d, entry)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
        _message("csiapps sandbox: entire sandbox cleared")
    else:
        _state["schemas"].pop(source_uuid, None)
        _state["records"].pop(source_uuid, None)
        d = _state["dir"]
        if d:
            target = os.path.join(d, source_uuid)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
        _message(f"csiapps sandbox: cleared source '{source_uuid}'")
    return None


def browse_sandbox(source_uuid: str | None = None) -> str:
    """Open the sandbox payload directory in the system file explorer.

    Each successful ingestion writes the submitted records to disk for
    inspection; this opens that directory so you can see the raw payloads.

    Args:
        source_uuid: If given, open that source's subdirectory. If ``None`` (the
            default), open the top-level sandbox payload directory.

    Returns:
        str: The filesystem path that was opened.

    Raises:
        RuntimeError: If the target directory does not exist yet — typically
            because nothing has been ingested for that source.

    Example:
        ```python
        import csiapps

        csiapps.browse_sandbox("hr-source")
        # -> '/tmp/csiapps_sandbox_ab12cd/hr-source'
        ```
    """
    target = sandbox_dir()
    if source_uuid is not None:
        target = os.path.join(target, source_uuid)
    if not os.path.isdir(target):
        raise RuntimeError(
            f"csiapps sandbox: directory '{target}' does not exist. "
            "Have you ingested any data yet?"
        )
    webbrowser.open(target)
    return target


# ---- dummy registration registry (sport orgs + athletes) ---------------


def _org_ids():
    return [int(o["id"]) for o in _state["orgs"].values()]


def _resolve_subject(record, subject_field):
    # Resolve an ingested record's subject against the athlete registry at read
    # time, mirroring production: subject_field names the record field whose
    # value must match a registered athlete's id. None when unresolved.
    if subject_field is None:
        return None
    key = record.get(subject_field)
    if key is None:
        return None
    for p in _state["profiles"]:
        if str(p["id"]) == str(key):
            return {
                "id": p["id"],
                "first_name": p["person"]["first_name"],
                "last_name": p["person"]["last_name"],
                "sport": p["sport"],
            }
    return None


def _random_names(n):
    # n distinct first/last pairs. Faker replaces R's babynames draw; we loop on
    # a seen-set so every full name is distinct (the parity the tests assert).
    from faker import Faker

    fake = Faker()
    firsts, lasts, seen = [], [], set()
    while len(firsts) < n:
        first, last = fake.first_name(), fake.last_name()
        if (first, last) in seen:
            continue
        seen.add((first, last))
        firsts.append(first)
        lasts.append(last)
    return firsts, lasts


def _make_profile(id, sport_org_id, first, last):
    # One prod-shaped athlete profile; placeholder fields match the
    # /api/registration/profile/ payload shape.
    org_name = _state["orgs"][str(sport_org_id)]["name"]
    return {
        "role_slug": "athlete",
        "id": id,
        "person": {
            "id": id,
            "first_name": first,
            "last_name": last,
            "email": f"{first.lower()}.{last.lower()}@example.com",
            "dob": (date.today() - timedelta(days=random.randint(6570, 12775))).isoformat(),
            "majority_age": "",
            "guardian": None,
            "emergency_contact": None,
            "competent_minor": True,
            "social_media_accounts": [],
        },
        "sport": {"id": int(sport_org_id), "name": org_name},
        "current_enrollment": "",
        "current_nomination": "",
        "residence_city": None,
        "birth_city": None,
        "status": "ACTIVE",
        "confirmed_date": _utcnow(),
        "discipline": "",
        "para_role": "ATHLETE",
        "sex_of_competition": random.choice(["M", "F"]),
        "gender": random.choice(["M", "F"]),
        "ethnicity": "",
        "ethnicity_other": "",
        "pronouns": random.choice(["HE", "SHE", "THEY"]),
        "pronouns_other": "",
        "disability": "NO",
        "birth_country": "CAN",
        "residence_country": "CAN",
        "education_attending": True,
        "education_level": "ATTENDING_SECONDARY",
        "education_institution": "",
        "education_css": "NO",
        "created_by": 0,
        "updated_by": 0,
        "updated_by_profile": 0,
        "role": 0,
        "carding_level": 0,
    }


def create_sport_org(name: str, id: int | None = None) -> dict:
    """Create a dummy sport organisation in the sandbox registry.

    Populates the local registry that the sandbox branches of
    [`fetch_org_options`][csiapps.client.fetch_org_options] and
    [`fetch_profiles`][csiapps.client.fetch_profiles] read from, so those
    helpers behave like production without a network call. Create an org before
    adding athletes to it with
    [`create_profile`][csiapps.sandbox.create_profile].

    Args:
        name: Display name for the organisation. Must be a non-empty string.
        id: Organisation id, a positive integer in ``1..999``. If ``None`` (the
            default), an unused id in that range is chosen at random.

    Returns:
        dict: The created org with keys ``id``, ``name``, and
        ``annual_cycle_start`` (today's date).

    Raises:
        ValueError: If ``name`` is empty, ``id`` is not a positive integer in
            ``1..999``, or an org with that ``id`` already exists.
        RuntimeError: If all 999 ids are already in use.

    Warns:
        UserWarning: If called while not in sandbox mode — dummy orgs are only
            read by sandbox helpers and have no effect in production.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.create_sport_org("Rowing", id=7)
        # -> {'id': 7, 'name': 'Rowing', 'annual_cycle_start': '2026-07-16'}
        ```
    """
    if not config.is_sandbox_mode():
        warnings.warn(
            "create_sport_org: not in sandbox mode; dummy orgs are only read by "
            "sandbox helpers and have no effect in production.",
            stacklevel=2,
        )
    if not (isinstance(name, str) and name):
        raise ValueError("create_sport_org: `name` must be a non-empty string.")

    existing = _org_ids()
    if id is None:
        pool = [i for i in range(1, 1000) if i not in existing]
        if not pool:
            raise RuntimeError(
                "create_sport_org: too many sport orgs in the sandbox. Limit is 999."
            )
        id = random.choice(pool)
    else:
        ok = (
            isinstance(id, (int, float))
            and not isinstance(id, bool)
            and float(id).is_integer()
            and 0 < id <= 999
        )
        if not ok:
            raise ValueError("create_sport_org: `id` must be a positive integer in 1:999.")
        id = int(id)
        if id in existing:
            raise ValueError(f"create_sport_org: sport org id {id} already exists in the sandbox.")

    org = {"id": id, "name": name, "annual_cycle_start": date.today().isoformat()}
    _state["orgs"][str(id)] = org
    _message(f"csiapps sandbox: created sport org {id} ('{name}')")
    return org


def create_profile(
    n: int,
    sport_org_id: int,
    first_names: list[str] | None = None,
    last_names: list[str] | None = None,
) -> list:
    """Create ``n`` dummy athlete profiles under an existing sandbox sport org.

    The generated profiles are production-shaped and become readable through the
    sandbox branches of [`fetch_profiles`][csiapps.client.fetch_profiles] and
    [`fetch_profile`][csiapps.client.fetch_profile]. Their ids also let ingested
    records resolve a ``subject`` when read back.

    Args:
        n: Number of profiles to create. Must be a non-negative integer.
        sport_org_id: Id of an existing sandbox sport org (create one first with
            [`create_sport_org`][csiapps.sandbox.create_sport_org]).
        first_names: Optional explicit first names, length ``n``. If omitted,
            random distinct names are generated. Must be given together with
            ``last_names`` or not at all.
        last_names: Optional explicit last names, length ``n``. Same rules as
            ``first_names``.

    Returns:
        list: The newly created profile dicts (production-shaped).

    Raises:
        ValueError: If ``n`` is not a non-negative integer, the sport org does
            not exist, only one of ``first_names``/``last_names`` is given, or
            the provided name lists are not each of length ``n``.

    Warns:
        UserWarning: If called while not in sandbox mode — dummy profiles are
            only read by sandbox helpers and have no effect in production.

    Example:
        ```python
        import csiapps

        csiapps.set_sandbox_mode(True)
        csiapps.create_sport_org("Rowing", id=7)
        athletes = csiapps.create_profile(3, sport_org_id=7)
        len(athletes)   # -> 3
        ```

    Note:
        Random names use ``faker`` where the R package used the ``babynames``
        dataset; generated full names are guaranteed distinct within a call.
    """
    if not config.is_sandbox_mode():
        warnings.warn(
            "create_profile: not in sandbox mode; dummy profiles are only read by "
            "sandbox helpers and have no effect in production.",
            stacklevel=2,
        )
    if not (isinstance(n, int) and not isinstance(n, bool) and n >= 0):
        raise ValueError("create_profile: `n` must be a non-negative integer.")
    sport_org_id = int(sport_org_id)
    if str(sport_org_id) not in _state["orgs"]:
        raise ValueError(
            f"create_profile: sport org '{sport_org_id}' does not exist; "
            f"create it first with create_sport_org({sport_org_id})."
        )

    if first_names is None and last_names is None:
        first_names, last_names = _random_names(n)
    elif first_names is None or last_names is None:
        raise ValueError("create_profile: provide both `first_names` and `last_names`, or neither.")
    else:
        if not (len(first_names) == n and len(last_names) == n):
            raise ValueError(
                "create_profile: `first_names` and `last_names` must each have length n."
            )

    start = len(_state["profiles"])
    new = [
        _make_profile(start + i + 1, sport_org_id, first_names[i], last_names[i])
        for i in range(n)
    ]
    _state["profiles"].extend(new)
    _message(
        f"csiapps sandbox: created {n} athlete(s) under sport org {sport_org_id} "
        f"({len(_state['profiles'])} total)"
    )
    return new


# ---- readers used by client.py sandbox branches ------------------------


def _sandbox_org_options():
    _message("csiapps sandbox: fetch_org_options() reading local registry (see create_sport_org())")
    return {o["id"]: o["name"] for o in _state["orgs"].values()}


def _sandbox_profiles(sport_org_id=None):
    _message("csiapps sandbox: fetch_profiles() reading local registry (see create_profile())")
    profs = list(_state["profiles"])
    if sport_org_id is not None:
        sid = int(sport_org_id)
        profs = [p for p in profs if int(p["sport"]["id"]) == sid]
    return profs


def _sandbox_profile(profile_id):
    _message("csiapps sandbox: fetch_profile() reading local registry (see create_profile())")
    for p in _state["profiles"]:
        if int(p["id"]) == int(profile_id):
            return p
    return None


# ---- request router ----------------------------------------------------


def _make_sandbox_request(
    endpoint, method="GET", body=None, query=None, verbose=False, paginate=False
):
    if query is None:
        query = {}
    ep = normalize_endpoint(endpoint)
    method = method.upper()
    _message(f"csiapps sandbox: emulating {method} {ep} (no real API call made)")

    # ROUTE 1: schema retrieval -- GET api/warehouse/data-sources/{uuid}
    if re.match(r"^api/warehouse/data-sources/.+$", ep) and method == "GET":
        source_uuid = re.sub(r"^api/warehouse/data-sources/", "", ep)
        schema = _state["schemas"].get(source_uuid)
        if schema is None:
            sandbox_error(
                404,
                f"no schema registered for source '{source_uuid}'. "
                "Register one with register_sandbox_schema().",
            )
        return {"uuid": source_uuid, "head_primary_definition": {"schema": schema}}

    # ROUTE 2: ingestion -- POST api/warehouse/ingestion/primary/
    if ep == "api/warehouse/ingestion/primary" and method == "POST":
        return sandbox_ingest(body, verbose=verbose)

    # ROUTE 3: record retrieval -- GET api/warehouse/data-records
    if ep == "api/warehouse/data-records" and method == "GET":
        source_uuid = query.get("source_uuid")
        if source_uuid is None:
            sandbox_error(400, "'source_uuid' query parameter is required.")

        records = _state["records"].get(source_uuid, [])
        results = [
            {
                "id": r["id"],
                "dataset_uuid": r["dataset_uuid"],
                "data": r["data"],
                "subject": _resolve_subject(r["data"], r["subject_field"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in records
        ]
        page = {"count": len(results), "next": None, "previous": None, "results": results}
        if paginate:
            return [page]
        return page

    sandbox_error(
        501,
        f"endpoint '{ep}' ({method}) is not emulated by the sandbox. Supported: "
        "GET api/warehouse/data-sources/{uuid}, "
        "POST api/warehouse/ingestion/primary/, "
        "GET api/warehouse/data-records.",
    )


def sandbox_ingest(body, verbose=False):
    source_uuid = body.get("source") if body else None
    records = body.get("records") if body else None
    subject_field = body.get("subject_field") if body else None

    if source_uuid is None or records is None:
        sandbox_error(400, "'source' and 'records' must be provided in the body.")
    if len(records) == 0:
        sandbox_error(400, "no records provided.")

    schema = _state["schemas"].get(source_uuid)
    if schema is None:
        sandbox_error(
            404,
            f"no schema registered for source '{source_uuid}'. "
            "Register one with register_sandbox_schema().",
        )

    validator = Draft7Validator(schema)
    failed, details = [], []
    for i, record in enumerate(records, start=1):
        errs = sorted(validator.iter_errors(record), key=lambda e: e.json_path)
        if errs:
            failed.append(i)
            msg = "; ".join(f"{e.json_path} {e.message}" for e in errs)
            details.append(f"record {i}: {msg}")

    if failed:
        sandbox_error(
            400,
            "validation failed for record(s) "
            + ", ".join(str(i) for i in failed)
            + ".\n"
            + "\n".join(details),
        )

    now = _utcnow()
    dataset_uuid = secrets.token_hex(16)
    n_existing = len(_state["records"].get(source_uuid, []))

    # Store subject_field (not a resolved subject) so the athlete link resolves
    # at read time -- lets late-registered athletes backfill.
    wrapped = [
        {
            "id": n_existing + i,
            "dataset_uuid": dataset_uuid,
            "data": records[i - 1],
            "subject_field": subject_field,
            "created_at": now,
            "updated_at": now,
        }
        for i in range(1, len(records) + 1)
    ]
    _state["records"].setdefault(source_uuid, [])
    _state["records"][source_uuid] = _state["records"][source_uuid] + wrapped

    # Write the payload to disk for developer inspection.
    target_dir = os.path.join(sandbox_dir(), source_uuid)
    os.makedirs(target_dir, exist_ok=True)
    seq = len(os.listdir(target_dir)) + 1
    fname = f"payload_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{seq:03d}.json"
    with open(os.path.join(target_dir, fname), "w") as f:
        json.dump(records, f, indent=2)

    if verbose:
        _message(
            f"csiapps sandbox: {len(records)} record(s) validated and stored "
            f"for source '{source_uuid}'"
        )
        _message(f"  payload written to: {os.path.join(target_dir, fname)}")

    return {
        "dataset": {"uuid": dataset_uuid, "source": source_uuid},
        "created_records": len(records),
    }
