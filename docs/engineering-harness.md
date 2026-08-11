# Engineering Harness

GitHub is the control plane for development. GitHub Issues hold the backlog,
Copilot coding agent implements issues on isolated branches, automated checks
produce acceptance evidence, and the repository owner approves every merge.

## Delivery flow

1. Capture work through an issue template.
2. Add `agent-ready` only when scope, acceptance criteria, validation, security,
   and documentation requirements are clear.
3. Assign the issue to Copilot coding agent.
4. Copilot prepares a branch and pull request.
5. Required CI, package, CodeQL, dependency, policy, and security checks run.
6. Copilot repairs failures and updates the pull request evidence.
7. The repository owner reviews and merges the pull request.
8. Close the issue when its acceptance criteria are verified.

Copilot must not merge its own pull requests.

## Labels

The label bootstrap workflow creates the standard labels after it is merged to
the default branch. Labels describe type and required handling:

- `agent-ready`: sufficiently specified for autonomous implementation.
- `major-change`: material product or architecture change; documentation required.
- `security-sensitive`: a checked security-review checkbox and substantive
  evidence or reviewer notes in the pull request are required.
- `type: feature`, `type: bug`, `type: documentation`, `type: dependencies`,
  `type: maintenance`, `type: security`: backlog categories.
- `status: blocked`: needs a decision or external dependency.

## Required checks

- **CI / test**: Ruff and the offline pytest suite on supported Python versions.
- **CI / package**: builds both source and wheel distributions and verifies installation.
- **CodeQL**: scans Python changes and the default branch.
- **Dependency Review**: blocks vulnerable dependency additions in pull requests.
- **PR Policy**: enforces documentation for `major-change`. A
  `security-sensitive` pull request must include the completed review checkbox
  and non-placeholder content below `Security evidence or reviewer notes:`
  before the next level-two heading.

## Repository settings

After these files reach `main`, configure a branch ruleset for `main`:

1. Require pull requests before merging.
2. Require one approval and dismiss stale approvals.
3. Require review from Code Owners.
4. Require the CI, CodeQL, Dependency Review, and PR Policy checks.
5. Require branches to be up to date before merging.
6. Block force pushes and branch deletion.
7. Enable the dependency graph, secret scanning, push protection, Dependabot
   alerts, and private vulnerability reporting.
8. Permit Copilot coding agent in the repository, but do not grant merge bypass.

GitHub check names are visible after the workflows have run once. Add required
checks to the ruleset after that first run.

## Cloud agent environment

`.github/workflows/copilot-setup-steps.yml` creates the deterministic agent
environment. It installs the package with development dependencies and verifies
that the package imports. Cloud-backed tests remain excluded from setup and CI;
tests must use offline fixtures or fakes.
