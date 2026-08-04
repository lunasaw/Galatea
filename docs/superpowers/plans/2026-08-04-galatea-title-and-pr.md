# Galatea Title and PR Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the README around the Galatea story, commit every safe documentation change, push the current branch, and update the existing pull request targeting `master`.

**Architecture:** Keep this as a documentation-only change on `imporve-claude-md`. The README receives one exact title replacement, generated personal output is excluded at the repository boundary, and the existing PR is updated instead of opening a duplicate.

**Tech Stack:** Markdown, Git, GitHub pull requests.

## Global Constraints

- The README heading must be exactly `# Galatea：让模型从数据中苏醒`.
- The existing one-sentence positioning statement and all content below it must remain unchanged by the title edit.
- Commit all current source and documentation changes that pass the safety review.
- Do not commit `outputs/`, `.DS_Store`, generated spreadsheets, preview images, inspection artifacts, secrets, tokens, or real infrastructure identifiers.
- Preserve the existing PR direction `imporve-claude-md` → `master`.

---

### Task 1: Protect generated output and sanitize the Ray plan

**Files:**
- Modify: `.gitignore`
- Modify: `docs/superpowers/plans/2026-08-04-ray-api-document.md`

**Interfaces:**
- Consumes: the repository rule that generated output must stay out of commits.
- Produces: an ignored root `outputs/` directory and a Ray plan without a real Actor ID literal.

- [ ] **Step 1: Add the generated-output boundary**

  Append `/outputs/` to `.gitignore` so root-level generated artifacts cannot be staged accidentally.

- [ ] **Step 2: Remove the real Actor ID from the Ray plan**

  Replace the exact Actor ID check in the final validation command with the existing secret-pattern check only:

  ```bash
  ! rg -n 'Authorization: Bearer [A-Za-z0-9_-]{16,}' doc/ray-api.md
  ```

- [ ] **Step 3: Verify the safety boundary**

  Run:

  ```bash
  git check-ignore -q outputs/.DS_Store
  ! rg -n '2a3774045de0c9d53360d83801000000' docs/superpowers/plans/2026-08-04-ray-api-document.md
  git diff --check -- .gitignore docs/superpowers/plans/2026-08-04-ray-api-document.md
  ```

  Expected: every command returns 0 and no real Actor ID is printed.

### Task 2: Apply the approved Galatea title

**Files:**
- Modify: `README.md:1`

**Interfaces:**
- Consumes: the approved title from `docs/superpowers/specs/2026-08-04-galatea-readme-title-design.md`.
- Produces: the exact public-facing Galatea heading while preserving the existing platform explanation.

- [ ] **Step 1: Replace the level-one heading**

  Change only the first line from:

  ```markdown
  # AI 训练一体化平台
  ```

  to:

  ```markdown
  # Galatea：让模型从数据中苏醒
  ```

- [ ] **Step 2: Verify the title-only README diff**

  Run:

  ```bash
  test "$(sed -n '1p' README.md)" = '# Galatea：让模型从数据中苏醒'
  git diff --check -- README.md
  git diff -- README.md
  ```

  Expected: the README diff contains one removed heading and one added heading, with no other content changes.

### Task 3: Commit every safe current change

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `docs/superpowers/plans/2026-08-04-ray-api-document.md`
- Create: `docs/superpowers/plans/2026-08-04-galatea-title-and-pr.md`

**Interfaces:**
- Consumes: the verified files from Tasks 1 and 2 plus all documentation commits already ahead of the remote branch.
- Produces: one focused final documentation commit with no generated or sensitive files.

- [ ] **Step 1: Stage explicit safe paths**

  Run:

  ```bash
  git add -- .gitignore README.md \
    docs/superpowers/plans/2026-08-04-ray-api-document.md \
    docs/superpowers/plans/2026-08-04-galatea-title-and-pr.md
  ```

- [ ] **Step 2: Inspect and scan the staged change**

  Run:

  ```bash
  git diff --cached --check
  git diff --cached --name-status
  ! git diff --cached | rg -n 'ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|Authorization: Bearer [A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}'
  ```

  Expected: exactly the four documented paths are staged, and the secret scan returns 0.

- [ ] **Step 3: Commit the final documentation change**

  Run:

  ```bash
  git commit -m 'docs: finalize Galatea platform guides'
  ```

  Expected: the commit succeeds and `git status --short` shows no unignored files.

### Task 4: Push and update the master pull request

**Files:**
- No repository file changes.

**Interfaces:**
- Consumes: the complete `imporve-claude-md` branch history.
- Produces: a remote branch at the local HEAD and one open draft PR targeting `master`.

- [ ] **Step 1: Push the current branch**

  Run:

  ```bash
  git push origin imporve-claude-md
  ```

  Expected: the remote head advances to the local HEAD.

- [ ] **Step 2: Update the existing PR instead of creating a duplicate**

  Confirm PR #3 targets `master`, then update its title and body to cover the Galatea README title, illustrated platform guide, MLflow integration guide, and Ray API guide.

- [ ] **Step 3: Verify the remote and PR**

  Run:

  ```bash
  test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/imporve-claude-md | cut -f1)"
  gh pr view 3 --repo lunasaw/Galatea --json state,isDraft,baseRefName,headRefName,headRefOid,url
  ```

  Expected: local and remote SHAs match; PR #3 is open, draft, based on `master`, and headed by `imporve-claude-md` at the same SHA.
