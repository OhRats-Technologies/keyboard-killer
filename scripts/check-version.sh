#!/bin/sh
# Validate a release version against pyproject.toml and, when running from a tag,
# the tag name. Prints the version on success.
set -eu
cd "$(dirname "$0")/.."
VERSION="${1:-$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1)}"
[ -n "$VERSION" ] || { echo "no version in pyproject.toml" >&2; exit 1; }
grep -q "^version = \"$VERSION\"$" pyproject.toml ||
  { echo "pyproject.toml does not declare version $VERSION" >&2; exit 1; }
grep -q "^## \[$VERSION\]" CHANGELOG.md ||
  { echo "CHANGELOG.md has no entry for $VERSION" >&2; exit 1; }
case "${GITHUB_REF:-}" in
  refs/tags/*)
    [ "$GITHUB_REF_NAME" = "v$VERSION" ] ||
      { echo "tag $GITHUB_REF_NAME does not match version $VERSION" >&2; exit 1; }
    ;;
esac
printf '%s\n' "$VERSION"
