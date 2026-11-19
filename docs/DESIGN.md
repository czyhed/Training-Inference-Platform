# Xuntui Platform Design System

This is the source of truth for product prototypes and UI implementation. Read it before creating or revising a screen.

## Product Context

Xuntui is an internal model training and inference platform. The primary users are teachers, ML engineers, and platform operators who repeatedly scan dense operational data, configure long forms, inspect logs, and recover from failed runs.

Design for confidence and speed: make state, ownership, cost, validation, and next actions visible without decoration.

## Visual Direction: Sealos-Inspired

Use Sealos as a reference for the next UI direction, not as a pixel-for-pixel copy. The target is a cloud-native control plane that feels approachable for first-time users while staying efficient for operators.

- Tone: open, calm, product-led, Chinese-first cloud tooling.
- Density: medium. Give primary workflows more breathing room than the current dense admin layout, while keeping tables compact enough for scanning.
- Layout: light canvas, persistent dark navigation, wide content column, clear page-level toolbar, and modular work panels.
- Surface: 10-12px corners, quiet borders, restrained shadows, and indigo/blue actions with a small cyan signal for live infrastructure state.
- Motion: short, functional transitions only; use progress and status changes to communicate system activity.
- Icons: use `lucide-react` icons inside buttons. Add a tooltip/title for icon-only actions.
- Avoid: copying Sealos logos or proprietary illustrations, decorative gradients, excessive purple, giant hero sections, and placeholder copy.

## Tokens

```css
:root {
  --navy: #0f172a;
  --navy-soft: #1e293b;
  --canvas: #f7f9fc;
  --surface: #ffffff;
  --surface-soft: #f8fafc;
  --line: #e2e8f0;
  --text: #0f172a;
  --muted: #64748b;
  --subtle: #94a3b8;
  --brand: #4f46e5;
  --brand-strong: #4338ca;
  --brand-soft: #eef2ff;
  --cloud-blue: #2563eb;
  --cloud-cyan: #06b6d4;
  --success: #067647;
  --success-soft: #ecfdf3;
  --warning: #b54708;
  --warning-soft: #fffaeb;
  --danger: #d92d20;
  --danger-soft: #fef3f2;
}
```

Typography uses `Noto Sans SC`, then `Microsoft YaHei`, then Arial. Use 24px for page titles, 16px for panel headings, 13px for primary body text, 12px for table/form text, and 11px for supporting metadata.

## Component Rules

- Primary action: indigo or cloud-blue filled button, 40px high, 8px radius.
- Secondary action: white button with a neutral border.
- Destructive action: white button with a red border/text; require confirmation when data or services are removed.
- Status: colored text on a pale background with a 6px dot. Status text must explain the state, not only use color.
- Tables: sticky or visible headings, horizontal scrolling for wide datasets, row-level actions grouped at the right. Use a subtle selected-row tint instead of heavy borders.
- Forms: one clear label per field, required marks, inline validation, and a readable summary before submission.
- Loading: preserve the last successful data when background refresh fails; show a small connection/error notice.
- Empty states: explain what is missing and provide the next useful action.
- Error states: state what failed, why it matters, and how to recover.

## Prototype Checklist

Every prototype should cover:

1. The default desktop view at 1440px wide.
2. A narrow desktop/tablet view around 1024px.
3. A mobile view around 390px when the workflow is expected to be used there.
4. Loading, empty, error, success, and destructive-confirmation states.
5. Keyboard focus and readable labels for interactive controls.
6. Realistic Chinese copy and representative data; no lorem ipsum.
7. A clear path from the prototype to the existing Next.js components.

## Agent Workflow

1. Read this file and the relevant module before proposing a layout.
2. Start with a low-cost structure pass: information hierarchy, navigation, fields, and states.
3. Use Open Design for visual exploration when multiple directions are useful.
4. Use the existing Next.js app for interaction and responsive validation.
5. Compare a screenshot at desktop and mobile sizes before calling a prototype ready.
6. Record accepted patterns here when they are likely to recur.

## Current Product Patterns

- Navigation: dark left sidebar with compact icon + label items.
- Work surface: light gray canvas, white bordered panels, indigo primary actions.
- Operational pages: summary metrics, filters, wide data table, pagination, detail drawer or preview pane.
- Configuration pages: numbered sections, grouped fields, a right-side guide or summary when helpful.
- Copy: concise Simplified Chinese; use explicit labels such as `训练中`, `待发布`, `校验失败`, `已归档`.

## Sealos-Inspired Prototype Scope

The first review set should show:

1. A cloud-control-plane overview with resource health, recent activity, and one obvious primary action.
2. A training task list with status filters, resource badges, progress, and a compact action menu.
3. A training task detail/configuration view with a clear stepper and live resource summary.

Use `Sealos 风格探索` as the name for experimental frames in Penpot. Keep the accepted direction in this file after review.
