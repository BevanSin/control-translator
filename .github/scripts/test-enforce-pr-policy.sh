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

run_case "documentation only" 0 "README.md" "" ""
run_case "security documentation only" 0 "docs/security-operations.md" "" ""
run_case "sensitive API path requires checkbox" 1 "src/control_translator/api/security.py" "" ""
run_case "sensitive source filename requires checkbox" 1 "src/control_translator/authentication.py" "" ""
run_case "dependency path requires evidence" 1 "pyproject.toml" "" "- [x] Security review completed"
run_case "workflow path accepts evidence" 0 ".github/workflows/ci.yml" "" "$valid_security_body"
run_case "explicit label remains enforced" 1 "README.md" "security-sensitive" ""
run_case "major change still requires docs" 1 "src/control_translator/models/catalog.py" "major-change" ""

echo "PR policy enforcement tests passed."
