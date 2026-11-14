# Youyun Resource Health and Training Fallback Design

## Goal

Avoid selecting Pod locations whose instance creation API is temporarily unavailable, and give visual-understanding and text-to-image LoRA training a controlled Pod-to-UHost fallback.

## Rules

- Health is keyed by instance mode, region, zone, image, GPU type, and charge type.
- `CreateCompShareInstance` transient service failures (`226645`, `8433`) open a short cooldown for that candidate; successful creation plus SSH/GPU probe clears the cooldown.
- Automatic selection excludes cooled candidates. Explicit selections fail with an actionable error rather than silently using a different location.
- `instanceMode=auto` tries Pod first, then a healthy UHost candidate in the same region, then other healthy candidates returned by the provider.
- Visual understanding and image-generation training use the same fallback policy and persist attempted modes, actual mode, and fallback reason.
- No real instances are created by tests; health state is injected through a fake provider and persisted only in the local runtime cache.

## Boundaries

`backend/providers/youyun/resource_health.py` owns the bounded cooldown cache. `backend/app.py` owns preflight candidate filtering and resource resolution. The two remote training runners own runtime fallback only when a provider create call fails before a usable instance handle exists. Frontend selectors display only candidates returned by the API.

## Failure and rollback

Provider failures retain their existing stable error codes. A fallback failure cleans any acquired handle before trying another candidate. Disable the feature with `XUNTUI_YOUYUN_RESOURCE_HEALTH_ENABLED=0`; existing explicit resource selections remain supported.
