# Service-Scoped Inference And API Keys

## Goal

Bind visual understanding image-to-text and validation evaluation workflows to a concrete inference service detail page, keep image-generation generation/evaluation service-scoped, and expose authenticated external API endpoints with rotatable service keys.

## Design

- Persist only a SHA-256 hash of each service API key in the inference service SQLite store.
- Return plaintext keys only from create/issue/rotate operations; normal service reads expose metadata, never the secret.
- Use `Authorization: Bearer <key>` for external requests. Keys are scoped to one service and workload type.
- Add service-scoped external routes for visual chat and image generation. Existing internal routes remain unchanged.
- Add service detail controls for API key issue, copy, and rotation. Visual service detail uses the current service as the implicit target for image-to-text and evaluation.

## Security

- Constant-time hash comparison.
- Reject missing, malformed, revoked, archived, or wrong-workload service keys.
- Do not include filesystem paths, remote handles, or provider credentials in external errors.

## Acceptance

- Key create, authenticate, rotate, and revoke behavior is covered by backend tests.
- External requests without/with invalid keys return 401; valid keys reach the existing runtime only when the service is running.
- Service detail UI no longer asks users to choose another service for visual inference/evaluation.
- TypeScript and focused backend tests pass.
