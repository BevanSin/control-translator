#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
policy_script="$script_dir/enforce-pr-policy.sh"
valid_security_body=$'- [x] Security review completed\n\nSecurity evidence or reviewer notes:\n\n- Reviewed trust boundaries and added regression coverage.\n\n## Documentation'

run_case() {
  local name="$1"
  local expected_status="$2"
  local changed_files="$3"
  local labels="$4"
  local pr_body="$5"
  local output
  local status

  set +e
  output="$(
    CHANGED_FILES="$changed_files" \
    LABELS="$labels" \
    PR_BODY="$pr_body" \
    BASE_SHA="unused" \
    HEAD_SHA="unused" \
    bash "$policy_script" 2>&1
  )"
  status=$?
  set -e

  if [[ "$status" -ne "$expected_status" ]]; then
    echo "Case '$name' returned $status; expected $expected_status."
    echo "$output"
    exit 1
  fi
}

run_diff_case() {
  local name="$1"
  local expected_status="$2"
  local repo output status base_sha head_sha

  repo="$(mktemp -d)"
  git init --quiet -b main "$repo"
  git -C "$repo" config user.email "policy-test@example.com"
  git -C "$repo" config user.name "Policy Test"

  printf 'base\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git -C "$repo" commit --quiet -m "base"

  # A pull request that only touches documentation.
  git -C "$repo" checkout --quiet -b feature
  printf 'documentation change\n' >> "$repo/README.md"
  git -C "$repo" add README.md
  git -C "$repo" commit --quiet -m "documentation only change"
  head_sha="$(git -C "$repo" rev-parse HEAD)"

  # The base branch then advances with an unrelated security-sensitive path.
  git -C "$repo" checkout --quiet main
  printf '[project]\n' > "$repo/pyproject.toml"
  git -C "$repo" add pyproject.toml
  git -C "$repo" commit --quiet -m "advance base branch with a sensitive path"
  base_sha="$(git -C "$repo" rev-parse HEAD)"

  set +e
  output="$(
    cd "$repo" && \
    LABELS="" \
    PR_BODY="" \
    BASE_SHA="$base_sha" \
    HEAD_SHA="$head_sha" \
    bash "$policy_script" 2>&1
  )"
  status=$?
  set -e
  rm -rf "$repo"

  if [[ "$status" -ne "$expected_status" ]]; then
    echo "Case '$name' returned $status; expected $expected_status."
    echo "$output"
    exit 1
  fi

  if grep -q "pyproject.toml" <<< "$output"; then
    echo "Case '$name' reported a base-branch file as changed by this pull request."
    echo "$output"
    exit 1
  fi
}

run_case "documentation only" 0 "README.md" "" ""
run_case "security documentation only" 0 "docs/security-operations.md" "" ""
run_case "sensitive API path requires checkbox" 1 "src/control_translator/api/security.py" "" ""
run_case "sensitive source filename requires checkbox" 1 "src/control_translator/authentication.py" "" ""
run_case "dependency path requires evidence" 1 "pyproject.toml" "" "- [x] Security review completed"
run_case "workflow path accepts evidence" 0 ".github/workflows/ci.yml" "" "$valid_security_body"
run_case "explicit label remains enforced" 1 "README.md" "security-sensitive" ""
run_case "major change still requires docs" 1 "src/control_translator/models/catalog.py" "major-change" ""
run_diff_case "base branch advances without affecting this pull request" 0

echo "PR policy enforcement tests passed."
