# Ignore Root Outputs Directory

## Goal

Keep generated files under the repository-root `outputs/` directory out of Git without changing the
handling of similarly named directories inside individual projects.

## Design

Append `/outputs/` to the root `.gitignore`. The leading slash anchors the rule to the repository root,
and the trailing slash limits the match to a directory. Existing ignore rules remain unchanged.

## Scope

- Ignore the untracked repository-root `outputs/` directory and everything beneath it.
- Do not ignore `outputs/` directories nested elsewhere in the repository.
- Do not remove or modify files already tracked by Git.

## Verification

- Use `git check-ignore -v outputs/` to confirm the rule and its source line.
- Use `git status --short` to confirm `outputs/` no longer appears while `.gitignore` remains the only
  intended implementation change.
