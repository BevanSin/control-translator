# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
private security advisory reporting:

https://github.com/BevanSin/control-translator/security/advisories/new

Include the affected version, reproduction steps, potential impact, and any
suggested remediation. Do not include real credentials, tenant data, or private
standards in the report.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest release and the
default branch.

## Security review scope

Changes involving secrets, authentication, uploads, URL ingestion, network
access, subprocess execution, parsers, dependencies, or generated Azure
deployment artifacts must use the `security-sensitive` label and complete the
security section of the pull request template.
