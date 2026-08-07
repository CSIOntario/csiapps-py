# Contributing to `csiapps` (Python)

Thanks for improving `csiapps`. A few pointers:

- **Using the package to build an app?** You're in the wrong place — see the
  [user documentation](https://csiontario.github.io/csiapps/) (tutorials for
  [Shiny](https://csiontario.github.io/csiapps/shiny-apps/) and
  [Dash](https://csiontario.github.io/csiapps/dash-apps/) apps, the sandbox, and
  the REST API).

- **Shipping a change to the package?** Follow
  **[Releasing csiapps](https://csiontario.github.io/csiapps/releasing/)** — it is
  the staging→canary→publish playbook: validate locally, merge to `staging`,
  re-validate with the deployed dummy apps, then fast-forward `main`, publish to
  PyPI, and update the docs.

## Quick start

```bash
git clone https://github.com/CSIOntario/csiapps-py
cd csiapps-py
uv sync                 # or: python -m venv .venv && pip install -e '.[shiny,dash]'
uv run pytest
```

The core (ingestion, auth, client, sandbox) depends on no web framework; the
Shiny and Dash wrappers live behind the mutually exclusive `[shiny]` / `[dash]`
extras. Keep that boundary — core code must never import a web framework.
