# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
WebSocket broadcast of newly written images, for the Spectre/Guidage panels in the
web GUI. Sibling to log_broadcast.py's WebSocketLogHandler, but simpler: unlike
logging.Handler.emit() (which can be called from any thread, including one spawned by
asyncio.to_thread() for a blocking INDI call), every caller here is an async acquisition
helper already running on the event loop -- so there is no thread-safety bridge to build.

Each panel has one fixed-name preview file (config.py's GUI_PREVIEW_DIR, e.g.
"guidage.fits"), overwritten by each new capture -- so the broadcast message only ever
needs to say *which panel* changed, never a filename: the GUI already knows the fixed
URL for each panel (GET /images/{panel}) and just re-fetches it.
"""

from __future__ import annotations

from fastapi import WebSocket

IMAGE_NEW_MSG = "image.new"


class WebSocketImageBroadcaster:
    """Notifies connected GUI clients that a panel's preview image was just updated."""

    def __init__(self):
        self._clients: set[WebSocket] = set()

    def add_client(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)

    def remove_client(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, panel: str) -> None:
        if not self._clients:
            return
        message = {"msg": IMAGE_NEW_MSG, "params": {"panel": panel}}
        for websocket in list(self._clients):
            try:
                await websocket.send_json(message)
            except Exception:
                self._clients.discard(websocket)


ws_image_broadcaster = WebSocketImageBroadcaster()