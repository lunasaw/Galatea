# Illustrated README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the root README as an illustrated platform guide followed by an accurate operational reference.

**Architecture:** Keep `README.md` as one document with two reading depths: a first-half “图解导览” that uses four existing illustrations as cognitive anchors with explanatory copy, followed by a searchable “技术手册” that preserves deployment, validation, documentation, and security details.

**Tech Stack:** GitHub-flavored Markdown and four existing PNG assets under `images/`.

## Global Constraints

- Do not change platform architecture, commands, paths, ports, security boundaries, or training-project requirements.
- Do not add platform capability claims that are not supported by the repository.
- Keep Cats vs Dogs positioned as an example workload rather than the platform boundary.
- Use relative Markdown paths for all images and local documents.
- Do not modify or stage the unrelated `outputs/` directory.

---

### Task 1: Rewrite the illustrated guide

**Files:**
- Modify: `README.md`
- Reference: `docs/superpowers/specs/2026-08-04-illustrated-readme-design.md`
- Reference: `images/01-platform-integration.png`
- Reference: `images/02-recoverable-architecture.png`
- Reference: `images/03-auditable-training-lifecycle.png`
- Reference: `images/04-training-project-contract.png`

**Interfaces:**
- Consumes: the existing platform contract, deployment commands, links, and four committed image paths.
- Produces: one self-contained README with an illustrated guide and an operational-reference section.

- [ ] **Step 1: Capture the original technical facts**

  Review the current README and retain every command, path, service port, API boundary, lifecycle rule,
  project contract, validation command, document link, and security rule.

- [ ] **Step 2: Build the four-anchor illustrated narrative**

  Reorder and rewrite the first half using this exact sequence:

  1. Platform positioning and `01-platform-integration.png`.
  2. Component responsibilities, architecture, and `02-recoverable-architecture.png`.
  3. Governed training lifecycle and `03-auditable-training-lifecycle.png`.
  4. New-project contract and `04-training-project-contract.png`.

  Give every anchor a core judgment before the image and a short “读图” section after it. Use three to
  five bullets to connect visual elements to engineering meaning. Avoid repeating image labels verbatim.

- [ ] **Step 3: Preserve the operational reference**

  Keep the current deployment shape, quick-start commands, services and ports, MLflow optimization guide,
  example workload, repository tree, validation, documentation index, and security requirements. Improve
  headings and transitions without changing their facts.

- [ ] **Step 4: Review readability**

  Confirm that a first-time reader can answer four questions before reaching the operational reference:

  - What is this platform?
  - Which component owns each responsibility?
  - What makes a training run auditable and promotable?
  - What must a new training project provide?

### Task 2: Clean up and verify the result

**Files:**
- Delete: `doc/superpowers/specs/2026-08-04-readme-illustrated-guide-design.md`
- Verify: `README.md`
- Verify: `images/*.png`
- Keep unchanged: `outputs/`

**Interfaces:**
- Consumes: the rewritten README from Task 1.
- Produces: one canonical design specification, one implementation plan, and a verified README.

- [ ] **Step 1: Remove the duplicate design specification**

  Delete `doc/superpowers/specs/2026-08-04-readme-illustrated-guide-design.md`; keep the existing canonical
  specification at `docs/superpowers/specs/2026-08-04-illustrated-readme-design.md`.

- [ ] **Step 2: Validate local links and image order**

  Run a local-link check over every Markdown link in `README.md`. Confirm that the four image references
  exist and occur in numeric order from `01` through `04`.

- [ ] **Step 3: Validate Markdown structure and diff quality**

  Run:

  ```bash
  git diff --check
  rg -n '^#{1,6} ' README.md
  rg -n '^!\[' README.md
  ```

  Expected: no diff-check errors, consistent heading levels, and exactly four image references.

- [ ] **Step 4: Verify change scope**

  Run `git status --short` and confirm that `outputs/` remains untracked and unstaged. Review the README
  diff against the original technical facts captured in Task 1.

- [ ] **Step 5: Commit the documentation update**

  Stage only `README.md`, this plan, and the duplicate-spec deletion. Commit with:

  ```bash
  git commit -m "docs: turn README into illustrated platform guide"
  ```
