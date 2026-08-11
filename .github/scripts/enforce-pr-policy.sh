#!/usr/bin/env bash
set -euo pipefail

changed_files="$(git diff --name-only "$BASE_SHA" "$HEAD_SHA")"

if [[ ",$LABELS," == *",major-change,"* ]]; then
  if ! grep -Eq '^(README\.md|CONTRIBUTING\.md|SECURITY\.md|docs/)' <<< "$changed_files"; then
    echo "::error::Pull requests labeled major-change must update README.md, CONTRIBUTING.md, SECURITY.md, or docs/."
    exit 1
  fi
fi

if [[ ",$LABELS," == *",security-sensitive,"* ]]; then
  if ! grep -Fqi -- "- [x] Security review completed" <<< "$PR_BODY"; then
    echo "::error::Pull requests labeled security-sensitive must complete the security review checkbox."
    exit 1
  fi

  security_evidence="$(
    awk '
      /^Security evidence or reviewer notes:[[:space:]]*$/ { capture = 1; next }
      capture && /^##[[:space:]]/ { exit }
      capture { print }
    ' <<< "$PR_BODY"
  )"
  has_security_evidence=false
  while IFS= read -r evidence_line; do
    evidence_line="$(sed -E 's/^[[:space:]]*([-*][[:space:]]*)?//; s/[[:space:]]+$//' <<< "$evidence_line")"
    evidence_line="$(tr '[:upper:]' '[:lower:]' <<< "$evidence_line")"
    case "$evidence_line" in
      ""|"n/a"|"na"|"none"|"tbd"|"todo"|"not applicable")
        ;;
      *)
        has_security_evidence=true
        break
        ;;
    esac
  done <<< "$security_evidence"

  if [[ "$has_security_evidence" != true ]]; then
    echo "::error::Pull requests labeled security-sensitive must include substantive content under 'Security evidence or reviewer notes:' before the next level-two heading."
    exit 1
  fi
fi
