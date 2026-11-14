# Task 1 Report: Runtime telemetry and failure classification

## Changes

- Added `ImageGenerationRuntime.update_telemetry(...)` for persisted phase, phase timestamp, heartbeat, classified failure, and suggested action fields.
- Wired telemetry into stage callbacks, provider logs/outcomes, cleanup retry failures, worker exceptions, and backend restart recovery.
- Preserved existing state/progress/cleanup fields and ensured a cleanup failure does not overwrite an existing primary failure code.
- Added runtime tests for stage timestamps, heartbeat updates, provider failure classification, cleanup failure preservation, and restart recovery classification.

## Verification

Command:

```text
python -m unittest backend.test_image_generation_runtime -v
```

Output summary: 11 tests ran and passed (`OK`).

Additional checks:

```text
python -m compileall -q backend/image_generation_runtime.py backend/test_image_generation_runtime.py
git diff --check
```

Both completed successfully.

No GPU process, Diffusers service, Youyun instance, SSH connection, or credentials were used.

## Fix Round 1

Addressed review findings:

- Provider outcomes without `failureMessage` now use a generic safe telemetry message; raw provider errors are not copied into `failureMessage` or `currentStep`.
- Cleanup retry success clears `failureCode`, `failureMessage`, and `suggestedAction` only when the code is `cleanup_failed`; primary generation failures remain intact.
- Unexpected worker/provider exceptions use the stable `generation_failed` taxonomy and safe failure text.
- Cleanup retry failures preserve an existing primary failure suggestion.

Verification:

```text
python -m unittest backend.test_image_generation_runtime -v
```

Output summary: 15 tests ran and passed (`OK`).

Additional checks:

```text
python -m compileall -q backend/image_generation_runtime.py backend/test_image_generation_runtime.py
git diff --check
```

Both completed successfully.
