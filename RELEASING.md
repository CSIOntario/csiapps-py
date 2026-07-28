# Releasing `csiapps` (maintainer playbook)

How to ship a change to the `csiapps` **Python** package safely. This is
maintainer documentation — if you are *using* `csiapps` to build an app, see the
[user docs](https://csiontario.github.io/csiapps/) instead.

> The R package (`csiapps-r`) follows the same shape against its own dummy app
> and CRAN/pkgdown; this file covers the Python side.

## The canary principle

Several production apps depend on `csiapps` (`beach-wellness`, `fso-wellness`,
`xcso_PSO_testing-dashboard`, `CKO_Progression-1`, …), so an unforeseen change
can silently break them. We don't rely on catching that by eye — we use the
**dummy apps** as regression harnesses:

- [`dummy-python-shiny`](https://github.com/CSIOntario/dummy-python-shiny) — the
  Shiny canary.
- [`dummy-python-dash`](https://github.com/CSIOntario/dummy-python-dash) — the
  Dash canary.

Each exercises the whole `csiapps` public surface. A **clean render plus two
green self-test boards** (sandbox + live) is our evidence that a change is safe.
Every release runs the dummy apps twice: once **locally** against the working
copy, and once again on the **deployed** apps after the change is pushed.

## The loop

### 1. Make the change locally

Edit `csiapps-py` and run its own test suite:

```bash
cd csiapps-py
uv run pytest            # or: .venv/bin/python -m pytest
```

Both framework test groups skip themselves when their extra is absent; run the
full matrix if you touched shared code.

### 2. Validate against the dummy apps locally

Install the **working copy** into each dummy app (the editable install overrides
the pinned wheel) and run its gates. Do this for **both** frameworks — a change
to shared core code must be green in each:

```bash
# in each dummy app dir, with its own .venv active
pip install -e '../csiapps-py[shiny]'    # dummy-python-shiny
pip install -e '../csiapps-py[dash]'     # dummy-python-dash

# a) sandbox gate — fast, no credentials, must all PASS and exit 0
.venv/bin/python selftest.py

# b) live gate — real token, read-only + one ingest round-trip
#    (needs CSIAPPS_ENV=production, CSIAPPS_ACCESS_TOKEN, CSIAPPS_TEST_SOURCE_UUID)
.venv/bin/python selftest.py --live

# c) run the app and eyeball it: chrome renders, both boards green,
#    Registry + Warehouse tabs work
.venv/bin/shiny run --reload app.py       # dummy-python-shiny  (:8000)
.venv/bin/python app.py                    # dummy-python-dash   (:8050 or the
                                           # port in CSIAPPS_REDIRECT_URI)
```

Run each app in **sandbox** mode (the default) at minimum; run it in
**production** mode too if the change touches auth, the client, or chrome.

**Green everywhere ⇒ proceed. Any red ⇒ fix `csiapps` and repeat.**

### 3. Push `csiapps` to `main`

Once local is green, push the branch / merge to `main`. Nothing is published to
PyPI yet — this only makes the change available from source.

### 4. Re-validate on the *deployed* dummy apps

The point of this step is to catch what a local editable install can't: real
OAuth, real data, the deployment environment, multi-request behaviour. Point the
deployed dummy apps at the unreleased code and redeploy them:

- Temporarily install `csiapps` **from `main`** rather than the pinned release —
  in the dummy app's `requirements.txt`:

  ```text
  # pre-release validation only; revert to the pinned version before step 5
  csiapps[dash] @ git+https://github.com/CSIOntario/csiapps-py.git@main
  ```

- Redeploy the dummy app (Posit Connect) and open it. Confirm: login returns
  "Signed in as …", both self-test boards are green in production, Registry shows
  real orgs/athletes, and the Warehouse round-trip works.

**Green on the deployed dummy apps ⇒ the change is safe to make official.**

### 5. Make it official

1. **Bump the version** in `pyproject.toml` (and `__version__`), following
   semver — a breaking public-surface change is a major/minor bump, not a patch.
2. **Publish to PyPI** (tag + build + upload, per the repo's release automation).
3. **Update the docs** — [`csiapps-docs`](https://github.com/CSIOntario/csiapps-docs):
   the API reference autodocs from `main`, but tutorials, the parity checklist,
   and any changed behaviour are hand-written and must be edited. Pushing to
   `main` there redeploys the site.
4. **Bump the pin and redeploy consumers.** Change `csiapps[...]==<new version>`
   in the dummy apps (revert the step-4 git line) **and** in the real production
   apps, then redeploy. The dummy apps should be green one more time on the
   pinned release.

## Checklist

- [ ] `pytest` green in `csiapps-py`.
- [ ] `selftest.py` (sandbox) green in **both** dummy apps against the working copy.
- [ ] `selftest.py --live` green where credentials are available.
- [ ] Each dummy app renders and both boards are green locally.
- [ ] Change pushed to `csiapps-py@main`.
- [ ] Deployed dummy apps green in production (installed from `main`).
- [ ] Version bumped; published to PyPI.
- [ ] `csiapps-docs` updated (tutorials / parity / behaviour notes).
- [ ] `csiapps==<version>` pin bumped and redeployed in the dummy apps and every
      dependent production app.

## Notes

- **Breaking changes** to the app-wrapper contract (route paths, required env
  vars, import locations) break already-deployed apps the moment they reinstall.
  Call them out in the release notes and in the consumer-app bump so nobody is
  surprised at redeploy time.
- **Keep the dummy apps in lockstep.** If a change adds public surface, extend
  `selftest.py` in the dummy apps to cover it — the canary is only as good as
  what it exercises.
