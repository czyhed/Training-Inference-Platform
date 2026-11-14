# Issue Tracker: Local Markdown

This repository uses local Markdown files for requirements, specs, and implementation tickets. No Gitee Issue integration is configured by default.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The feature spec is `.scratch/<feature-slug>/spec.md`
- Implementation tickets live at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Conversation notes append under a `## Comments` heading

When a skill says to publish to the issue tracker, create or update the corresponding files under `.scratch/`.
