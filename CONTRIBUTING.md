# Contributing

Development is managed through GitHub Issues and pull requests. Direct changes to
`main` are not part of the normal workflow.

## Workflow

1. Create or select an issue with clear acceptance criteria.
2. Apply `agent-ready` only when the issue has enough detail to implement.
3. Work on a branch associated with the issue.
4. Open a pull request using the repository template.
5. Run the smallest relevant tests while developing, then all required checks.
6. Update documentation when behavior, configuration, architecture, or user workflows change.
7. Obtain repository-owner approval before merging.

Copilot coding agent may implement issues, update the backlog, and prepare pull
requests. It must not merge its own work.

## Local validation

```powershell
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest
python -m build
```

Tests must remain runnable without cloud credentials. Network-backed behavior
should be exercised through fakes or offline fixtures in continuous integration.

## Data and secrets

- Never commit `.env`, credentials, tokens, tenant data, uploaded standards, or live endpoints.
- Treat `data/` as user-owned working state.
- Never overwrite curated mapping decisions during code synchronization or tests.
- Use temporary directories for tests that create mappings or generated bundles.
- Keep examples synthetic and remove identifying information from diagnostics.

## Pull request risk

Apply `major-change` when a change materially affects architecture, user behavior,
configuration, generated artifacts, or deployment. These changes require a
corresponding update to `README.md` or `docs/`.

Apply `security-sensitive` when a change affects authentication, authorization,
secret handling, file uploads, URL fetching, subprocesses, network access,
deserialization, generated deployment artifacts, or dependency trust. Complete
the security review checklist before requesting approval.
