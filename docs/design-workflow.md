# Design Workflow

This repository uses a local-first workflow so prototypes remain editable without depending on a limited cloud file quota.

## Tools

- **Local Codex**: implementation, responsive validation, screenshots, and code review.
- **Open Design Cloud**: optional visual exploration when a generated direction is worth the model cost.
- **Penpot**: optional collaborative canvas for product review. Use it when a designer or teacher needs to move frames and annotate them directly.
- **Git**: source of truth for `DESIGN.md`, implemented UI, and accepted patterns.

## Standard Loop

1. Read `DESIGN.md`, `CURRENT_STATE.md`, and the target module.
2. Create or refine a prototype in Open Design using the same tokens and Chinese copy.
3. Bring the chosen direction into the Next.js app using existing components and `lucide-react` icons.
4. Run the app and inspect desktop, tablet, and mobile screenshots.
5. Check loading, empty, error, success, and destructive states.
6. Keep only the accepted direction in Git; archive experiments in Open Design instead of deleting the working source.

## Current Visual Exploration

The active exploration direction is **Sealos-inspired cloud-native UI**. It should borrow the clarity of a modern cloud control plane: persistent dark navigation, light canvas, indigo/blue primary actions, cyan infrastructure signals, modular panels, and approachable empty states. Do not copy Sealos branding or proprietary assets.

The first Penpot review file contains two frames named `Sealos 风格探索 / 总览` and `Sealos 风格探索 / 训练任务`.

## Open Design Project

The local project is named `Xuntui Platform`. It is intended for visual exploration and can be edited through the Open Design MCP configured in `.codex/config.toml`.

## Penpot Boundary

Penpot is not required for implementation. If the team needs shared canvas review, run Penpot separately (cloud or self-hosted) and export the accepted structure back into the repository. Do not make a cloud canvas the only copy of a design decision.

## Local Penpot MCP Bridge

The official Penpot MCP server is installed outside this repository at `C:\Users\silentfire\penpot-mcp` and currently runs without Docker:

- MCP endpoint: `http://localhost:4401/mcp` (Streamable HTTP)
- Plugin page: `http://localhost:4400/manifest.json`
- WebSocket bridge: `ws://localhost:4402`

The `/sse` endpoint remains available for legacy clients, but Codex should use
the modern `/mcp` endpoint directly. This avoids an extra `mcp-remote` proxy
process and prevents stale SSE sessions from hanging tool responses.

Use `scripts\start-penpot-mcp.ps1` and `scripts\stop-penpot-mcp.ps1` to manage the local processes. Run `scripts\check-penpot-mcp.ps1` to distinguish a stopped service from a disconnected plugin. In Penpot Cloud, open a design file, load the plugin manifest URL, keep the plugin window open, and connect it to the local MCP server. Closing the plugin window destroys its WebSocket connection; the local service cannot keep a closed Penpot plugin alive. The project `.codex\config.toml` already contains the enabled `penpot` MCP entry.
