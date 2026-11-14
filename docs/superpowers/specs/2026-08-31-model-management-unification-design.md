# Vision and Image Model Management Unification

## Goal

Make visual-understanding and text-to-image model management follow the main NLP model-management interaction model while preserving each domain's existing validation, catalog, and deployment semantics.

## Design

Both domains use the same information architecture: page header, action buttons, asset summary, lineage note, model-type tabs, search/filter toolbar, base-model table, trained-Adapter table, and a detail drawer. A shared presentational shell owns layout, tab state, search, empty/loading states, and table semantics. Domain adapters normalize API records into base and trained asset rows and provide domain-specific detail, catalog, probe, register, refresh, and deployment callbacks.

The shared shell does not change backend contracts. Visual records retain probe capabilities and local/Youyun readiness; image-generation records retain LoRA/QLoRA compatibility and image-generation readiness. Registration and catalog dialogs remain domain-owned because their validation fields differ.

## Interaction Rules

- `全部`, `基模`, `训练模型`, and `回收站` are the primary asset filters.
- Search covers display name, repository ID, local path, task ID, and domain-specific metadata.
- Clicking a model name or `详情` opens the detail drawer.
- Base-model rows expose source, path/repository, local or remote readiness, and the domain's primary action.
- Trained rows expose base-model lineage, training method, Adapter variant, and deployment readiness.
- Empty states always provide either `清除筛选`, `浏览基模目录`, or `登记基模`.

## Verification

Add source-level regression tests for the shared shell contract and both domain adapters. Run focused frontend tests, `npx tsc --noEmit`, and `npm run build`. No GPU process or remote instance is started.
