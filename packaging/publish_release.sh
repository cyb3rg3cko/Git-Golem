#!/usr/bin/env bash
# Attaches build files to the GitHub release for the current tag (used by the workflow).
# Creates the release if it doesn't exist yet; replaces files that were already uploaded.
#   bash packaging/publish_release.sh dist/*
set -euo pipefail
TAG="${GITHUB_REF_NAME:?Run this from a tag build}"

if ! gh release view "$TAG" >/dev/null 2>&1; then
    # Both build jobs may get here at once; if the other one created it first, that's fine.
    gh release create "$TAG" --title "$TAG" --generate-notes || true
fi
gh release upload "$TAG" "$@" --clobber
