# Xuntui Platform Agent Notes

This repository is an internal model training and inference platform with a Next.js frontend and a FastAPI/Python backend. Read `README.md`, `CURRENT_STATE.md`, and `ARCHITECTURE.md` when the task touches an area they describe.

Before changing any code, tests, schemas, configuration, or deployment files, read [`docs/训推平台-工程规范与变更门禁.md`](docs/训推平台-工程规范与变更门禁.md). It is the mandatory project gate for system boundaries, data transfer, validation, state transitions, cloud-resource cleanup, BUG fixes, testing, and release acceptance. Record the affected boundary, failing test, resource lifecycle, verification commands, and rollback plan before implementation.

## Agent skills

### Issue tracker

Requirements, specs, and implementation tickets use local Markdown files under `.scratch/`; see `docs/agents/issue-tracker.md`.

### Domain docs

This is a single-context repository. Domain notes live in `CONTEXT.md` when created, with architectural decisions in `docs/adr/`; see `docs/agents/domain.md`.

## Working conventions

- The canonical remote is Gitea: `origin` = `http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git` (SSH alternative: `ssh://git@git.leiziapi.cn:2222/leifengyang/cppt-platform.git`). Push branches and release tags only to Gitea. The optional `github` remote is for historical reads and has push disabled in this desktop checkout.
- Drone lives at `http://drone.leiziapi.cn:4400/leifengyang/cppt-platform`. Push/PR events run verification; tags run the production release pipeline. Drone clones directly from Gitea; no release bundle upload is needed. Do not create a production tag just to test CI.
- Keep changes scoped to the requested behavior and preserve unrelated user changes.
- Prefer the existing TypeScript, React, FastAPI, SQLite/PostgreSQL, and test patterns already in the repository.
- Run focused tests and type checks for changed areas; run the broader suite when shared contracts change.
- Do not start GPU training or a vLLM service for documentation-only checks.

### Figma workflow

Use the connected Figma MCP and the installed Figma skills for design-to-code work; see `docs/agents/figma.md` for the required workflow and configuration notes.
