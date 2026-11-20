#!/bin/sh
# Validate a release version against pyproject.toml and, when running from a tag,
# the tag name. Prints the version on success.
set -eu
cd "$(dirname "$0")/.."
VERSION="${1:-$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1)}"
[ -n "$VERSION" ] || { echo "no version in pyproject.toml" >&2; exit 1; }
grep -q "^version = \"$VERSION\"$" pyproject.toml ||
  { echo "pyproject.toml does not declare version $VERSION" >&2; exit 1; }
if [ -n "${GITHUB_REF_NAME:-}" ]; then
  [ "$GITHUB_REF_NAME" = "v$VERSION" ] ||
    { echo "tag $GITHUB_REF_NAME does not match version $VERSION" >&2; exit 1; }
fi
printf '%s\n' "$VERSION"
