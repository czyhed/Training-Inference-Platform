from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch
from starlette.websockets import WebSocketDisconnect
from backend import test_app_cli as bootstrap

api = bootstrap.app_module


class TrainingWebSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_event_is_followed_by_normal_close(self):
        store = Mock()
        store.get.return_value = {"id": "ft-stream", "state": "completed", "progress": 100}
        store.log_items.return_value = {"items": [{"id": 1, "message": "training finished"}], "hasMore": False}
        store.metrics.return_value = [{"step": 1, "trainLoss": 0.5}]
        socket = Mock(query_params={})
        events = []
        socket.accept = AsyncMock()
        socket.send_json = AsyncMock(side_effect=lambda message: events.append(message["type"]))
        socket.close = AsyncMock(side_effect=lambda code: events.append(code))
        with patch.object(api, "store", store), patch.object(api, "websocket_authenticated", return_value=True):
            await api.task_stream(socket, "ft-stream")
        self.assertEqual(events, ["log", "state", "metrics", "terminal", 1000])

    async def test_idle_running_stream_sends_heartbeat(self):
        store = Mock()
        store.get.return_value = {"id": "ft-stream", "state": "running", "progress": 1}
        store.log_items.return_value = {"items": [], "hasMore": False}
        store.metrics.return_value = []
        socket = Mock(query_params={})
        socket.accept = AsyncMock()
        socket.send_json = AsyncMock()
        socket.close = AsyncMock()
        with patch.object(api, "store", store), patch.object(api, "websocket_authenticated", return_value=True), patch.object(api, "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", 0), patch("backend.app.asyncio.sleep", side_effect=WebSocketDisconnect):
            await api.task_stream(socket, "ft-stream")
        types = [call.args[0]["type"] for call in socket.send_json.await_args_list]
        self.assertIn("heartbeat", types)
        socket.close.assert_not_awaited()
