# Backend

The backend is a FastAPI service launched inside WSL by `scripts/run_backend.py`.

Use the root documentation as the source of truth:

- [../README.md](../README.md) for startup and tests
- [../CURRENT_STATE.md](../CURRENT_STATE.md) for the audited implementation and runtime paths
- [../ARCHITECTURE.md](../ARCHITECTURE.md) for API and storage boundaries

The normal local API is `http://127.0.0.1:8010`; the frontend is normally `http://127.0.0.1:3100` on this machine.

Best-checkpoint regression coverage is split between `backend.test_store`, `backend.test_chat_api`, and the real GPU workflow in `scripts/verify_best_checkpoint_e2e.py`.
