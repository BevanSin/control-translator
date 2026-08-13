## Summary

<!-- What changed and why? -->

Closes #

## Acceptance evidence

<!-- Map evidence to the linked issue's acceptance criteria. -->

- 

## Validation

- [ ] Relevant targeted tests pass
- [ ] `python -m ruff check src tests` passes
- [ ] `python -m pytest` passes
- [ ] `python -m build` passes

## Risk and data safety

- [ ] No secrets, credentials, tenant data, uploaded standards, or generated user data are included
- [ ] Existing mapping decisions and user-managed `data/` content are preserved
- [ ] New failure modes are surfaced rather than silently ignored
- [ ] Rollback or compatibility impact is described below

Risk notes:

## Security review

The PR policy automatically requires this review for known sensitive paths.
Apply `security-sensitive` when changes to authentication, secrets, uploads, URL
fetching, network access, subprocesses, parsers, dependencies, or generated
deployment artifacts fall outside those paths.

- [ ] Not security-sensitive
- [ ] Security review completed

Security evidence or reviewer notes:

## Documentation

- [ ] Documentation updated
- [ ] No documentation update is needed; reason provided below

Documentation notes:
