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

An optional `source` identifies which sub-plugin produced this particular capture (e.g.
"focus") -- omitted from the message entirely when not given, so an existing caller
that never passes it keeps sending exactly the same message as before. Used today so
the GUI can tell a focus.py guide image apart from every other Guidage-panel capture
(slew_72ed, pointing_72ed, stop_phd2_guiding, home_sync_am3, none of which pass it) and only then
draw focus's own reference search-window rectangle -- see fits_viewer.ts.

An optional `overlay` (a small JSON-serialisable dict) carries per-image geometry the GUI
draws on top of the image -- used today by slit_xy.py (search zone, measured slit line,
retained slit point, in Rosace's pixel convention). Omitted from the message when not
given, same as `source`.
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

    async def broadcast(self, panel: str, source: str | None = None, overlay: dict | None = None) -> None:
        if not self._clients:
            return
        params: dict = {"panel": panel}
        if source is not None:
            params["source"] = source
        if overlay is not None:
            params["overlay"] = overlay
        message = {"msg": IMAGE_NEW_MSG, "params": params}
        for websocket in list(self._clients):
            try:
                await websocket.send_json(message)
            except Exception:
                self._clients.discard(websocket)


ws_image_broadcaster = WebSocketImageBroadcaster()