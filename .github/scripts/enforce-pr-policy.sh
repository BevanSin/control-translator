#!/usr/bin/env bash
set -euo pipefail

if [[ -v CHANGED_FILES ]]; then
  changed_files="$CHANGED_FILES"
else
  # Compare against the merge base rather than the base branch tip. The base tip
  # advances whenever another pull request merges, and a plain two-dot diff would
  # then report those unrelated files as changed here, demanding security review
  # for paths this pull request never touched.
  merge_base="$(git merge-base "$BASE_SHA" "$HEAD_SHA" 2>/dev/null || echo "$BASE_SHA")"
  changed_files="$(git diff --name-only "$merge_base" "$HEAD_SHA")"
fi

security_sensitive=false
security_sensitive_paths=""
while IFS= read -r changed_file; do
  case "$changed_file" in
    .github/workflows/*|.github/scripts/*|\
    pyproject.toml|setup.py|build_support.py|MANIFEST.in|\
    frontend/package.json|frontend/package-lock.json|\
    src/control_translator/api/*|src/control_translator/build/*|\
    src/control_translator/distribute/*|src/control_translator/ingest/*|\
    src/control_translator/validate/*|src/control_translator/config.py|\
    src/control_translator/web.py|src/control_translator/*security*|\
    src/control_translator/*auth*)
      security_sensitive=true
      security_sensitive_paths+="${security_sensitive_paths:+, }${changed_file}"
      ;;
  esac
done <<< "$changed_files"

if [[ ",$LABELS," == *",security-sensitive,"* ]]; then
  security_sensitive=true
fi

if [[ ",$LABELS," == *",major-change,"* ]]; then
  if ! grep -Eq '^(README\.md|CONTRIBUTING\.md|SECURITY\.md|docs/)' <<< "$changed_files"; then
    echo "::error::Pull requests labeled major-change must update README.md, CONTRIBUTING.md, SECURITY.md, or docs/."
    exit 1
  fi
fi

if [[ "$security_sensitive" == true ]]; then
  if [[ -n "$security_sensitive_paths" ]]; then
    echo "::notice::Security review required by changed paths: $security_sensitive_paths"
  fi
  if ! grep -Fqi -- "- [x] Security review completed" <<< "$PR_BODY"; then
    echo "::error::Security-sensitive pull requests must complete the security review checkbox."
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
    evidence_line="$(sed -E 's/^[[:space:]]*([-*][[:space:]]*)?//; s/[[:space:].!]+$//' <<< "$evidence_line")"
    evidence_line="$(tr '[:upper:]' '[:lower:]' <<< "$evidence_line")"
    case "$evidence_line" in
      ""|"n/a"|"none"|"tbd"|"todo"|"not applicable")
        ;;
      *)
        has_security_evidence=true
        break
        ;;
    esac
  done <<< "$security_evidence"

  if [[ "$has_security_evidence" != true ]]; then
    echo "::error::Security-sensitive pull requests must include substantive content under 'Security evidence or reviewer notes:' before the next level-two heading."
    exit 1
  fi
fi
