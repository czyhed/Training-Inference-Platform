# Backend

FastAPI service for the training and inference platform.

- Project overview and setup: [../README.md](../README.md)
- Architecture and API boundaries: [../ARCHITECTURE.md](../ARCHITECTURE.md)

The backend listens on `http://127.0.0.1:8010` by default; the frontend dev server uses `http://127.0.0.1:3100`.

Best-checkpoint regression coverage is split between `backend.test_store`, `backend.test_chat_api`, and the real GPU workflow in `scripts/verify_best_checkpoint_e2e.py`.
