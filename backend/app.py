"""FastAPI control plane for FakeGPS Studio.

Responsibilities:
  * enumerate connected devices (iOS via pymobiledevice3, Android via adb, or
    demo devices when FAKEGPS_DEMO=1),
  * own one active LocationDriver session at a time,
  * run a background playback loop for joystick walking and route simulation,
  * serve the Leaflet frontend and expose REST + WebSocket control.

State is intentionally single-session (one device driven at a time) to match the
desktop tool's UX and keep the device connections simple.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import routing
from .devices import DeviceInfo, LocationDriver

log = logging.getLogger("fakegps.app")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DEMO = os.environ.get("FAKEGPS_DEMO") == "1"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TICK_S = 0.5  # playback update interval


# --------------------------------------------------------------------------- #
# Driver backends                                                             #
# --------------------------------------------------------------------------- #
def _backends():
    """Return the (list_devices, open_driver) pairs to use for this run."""
    if DEMO:
        from .devices import demo
        return [(demo.list_devices, demo.open_driver)]
    from .devices import android, ios
    return [(ios.list_devices, ios.open_driver), (android.list_devices, android.open_driver)]


def enumerate_devices() -> list[DeviceInfo]:
    found: list[DeviceInfo] = []
    for list_fn, _ in _backends():
        try:
            found.extend(list_fn())
        except Exception as e:  # a missing backend shouldn't break the others
            log.warning("device enumeration failed: %s", e)
    return found


def open_driver(udid: str) -> LocationDriver:
    # Route to the backend that actually owns this udid so the real connection
    # error surfaces (otherwise an iOS failure gets masked by Android's
    # "device not found" from the next backend in line).
    platform = None
    for info in enumerate_devices():
        if info.udid == udid:
            platform = info.platform
            break
    if platform is None:
        raise HTTPException(status_code=404, detail=f"device {udid} not connected")

    if DEMO:
        from .devices import demo
        open_fn = demo.open_driver
    elif platform == "ios":
        from .devices import ios
        open_fn = ios.open_driver
    else:
        from .devices import android
        open_fn = android.open_driver

    try:
        return open_fn(udid)
    except Exception as e:
        # Surface the backend's own message verbatim — it carries the actionable
        # hint (e.g. start tunneld, mount DDI, select mock-location app).
        raise HTTPException(status_code=502, detail=str(e)) from e


# --------------------------------------------------------------------------- #
# Session: the one active driver + playback task                              #
# --------------------------------------------------------------------------- #
class Session:
    def __init__(self) -> None:
        self.driver: Optional[LocationDriver] = None
        self.current: Optional[routing.Point] = None
        self._play_task: Optional[asyncio.Task] = None
        self._heading: float = 0.0
        self._lock = asyncio.Lock()
        self.subscribers: set[WebSocket] = set()

    # -- connection ------------------------------------------------------
    async def select(self, udid: str) -> DeviceInfo:
        async with self._lock:
            # Idempotent: re-selecting the already-connected device is a no-op.
            # This avoids tearing down and rebuilding the iOS DVT session when
            # the UI auto-connects and the user also clicks Connect.
            if self.driver is not None and self.driver.info.udid == udid:
                return self.driver.info
            await self.release()
            self.driver = await asyncio.to_thread(open_driver, udid)
            return self.driver.info

    async def release(self) -> None:
        await self.stop_playback()
        if self.driver is not None:
            d, self.driver = self.driver, None
            await asyncio.to_thread(d.close)
        self.current = None

    def _require(self) -> LocationDriver:
        if self.driver is None:
            raise HTTPException(status_code=409, detail="no device selected")
        return self.driver

    # -- single set ------------------------------------------------------
    async def teleport(self, lat: float, lon: float) -> None:
        await self.stop_playback()
        await self._push(lat, lon)

    async def _push(self, lat: float, lon: float) -> None:
        driver = self._require()
        await asyncio.to_thread(driver.set_location, lat, lon)
        self.current = routing.Point(lat, lon)
        await self._broadcast({"type": "position", "lat": lat, "lon": lon})

    async def clear(self) -> None:
        await self.stop_playback()
        driver = self._require()
        await asyncio.to_thread(driver.clear)
        self.current = None
        await self._broadcast({"type": "cleared"})

    # -- joystick (one nudge per call) -----------------------------------
    async def joystick(self, heading: float, speed_mps: float) -> None:
        """Move one tick's worth of distance along heading from current point."""
        await self.stop_playback()
        if self.current is None:
            raise HTTPException(status_code=409, detail="set a start location first")
        self._heading = heading
        step = max(speed_mps * TICK_S, 0.01)
        nxt = routing.destination(self.current, heading, step)
        await self._push(nxt.lat, nxt.lon)

    # -- route playback --------------------------------------------------
    async def play_route(self, points: list[routing.Point], speed_mps: float,
                         loop: bool, realistic: bool = False,
                         jitter_m: float = 0.0) -> None:
        await self.stop_playback()
        self._require()
        self._play_task = asyncio.create_task(
            self._run_route(points, speed_mps, loop, realistic, jitter_m)
        )

    def _fixes(self, points: list[routing.Point], speed_mps: float,
               realistic: bool, jitter_m: float):
        """Pick constant-speed vs lifelike playback for one pass of the route."""
        if realistic:
            cfg = routing.MovementConfig.for_speed(speed_mps, jitter_m=jitter_m)
            return routing.simulate(points, cfg, TICK_S)
        return routing.walk(points, speed_mps, TICK_S)

    async def _run_route(self, points: list[routing.Point], speed_mps: float,
                         loop: bool, realistic: bool, jitter_m: float) -> None:
        try:
            await self._broadcast({"type": "route_started", "n": len(points)})
            while True:
                for pt in self._fixes(points, speed_mps, realistic, jitter_m):
                    await self._push(pt.lat, pt.lon)
                    await asyncio.sleep(TICK_S)
                if not loop:
                    break
                points = list(reversed(points))  # ping-pong so it stays continuous
            await self._broadcast({"type": "route_finished"})
        except asyncio.CancelledError:
            await self._broadcast({"type": "route_stopped"})
            raise
        except Exception as e:
            log.exception("route playback error")
            await self._broadcast({"type": "error", "detail": str(e)})

    async def stop_playback(self) -> None:
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass
        self._play_task = None

    # -- websocket fan-out ----------------------------------------------
    async def _broadcast(self, msg: dict) -> None:
        dead = []
        for ws in self.subscribers:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.subscribers.discard(ws)


session = Session()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await session.release()


app = FastAPI(title="FakeGPS Studio", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Request models                                                              #
# --------------------------------------------------------------------------- #
class SelectReq(BaseModel):
    udid: str


class PointReq(BaseModel):
    lat: float
    lon: float


class JoystickReq(BaseModel):
    heading: float
    speed: float = routing.SPEED_PRESETS["walk"]


class RouteReq(BaseModel):
    points: list[PointReq]
    speed: float = routing.SPEED_PRESETS["walk"]
    loop: bool = False
    realistic: bool = False     # accelerate/brake + turn slowdown vs constant speed
    jitter_m: float = 0.0       # GPS noise std-dev in metres (realistic mode only)


# --------------------------------------------------------------------------- #
# REST API                                                                    #
# --------------------------------------------------------------------------- #
@app.get("/api/devices")
async def api_devices():
    devices = await asyncio.to_thread(enumerate_devices)
    return {"demo": DEMO, "devices": [asdict(d) for d in devices]}


@app.post("/api/select")
async def api_select(req: SelectReq):
    info = await session.select(req.udid)
    return {"selected": asdict(info)}


@app.get("/api/status")
async def api_status():
    return {
        "selected": asdict(session.driver.info) if session.driver else None,
        "current": session.current.as_tuple() if session.current else None,
        "playing": bool(session._play_task and not session._play_task.done()),
    }


@app.post("/api/teleport")
async def api_teleport(req: PointReq):
    await session.teleport(req.lat, req.lon)
    return {"ok": True}


@app.post("/api/joystick")
async def api_joystick(req: JoystickReq):
    await session.joystick(req.heading, req.speed)
    return {"ok": True, "current": session.current.as_tuple() if session.current else None}


@app.post("/api/route")
async def api_route(req: RouteReq):
    pts = [routing.Point(p.lat, p.lon) for p in req.points]
    if len(pts) < 2:
        raise HTTPException(status_code=400, detail="route needs >= 2 points")
    await session.play_route(pts, req.speed, req.loop, req.realistic, req.jitter_m)
    return {"ok": True}


@app.post("/api/stop")
async def api_stop():
    await session.stop_playback()
    return {"ok": True}


@app.post("/api/clear")
async def api_clear():
    await session.clear()
    return {"ok": True}


@app.post("/api/gpx")
async def api_gpx(payload: dict):
    try:
        pts = routing.parse_gpx(payload["gpx"])
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"points": [p.as_tuple() for p in pts]}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    session.subscribers.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive; control is via REST
    except WebSocketDisconnect:
        pass
    finally:
        session.subscribers.discard(websocket)


# --------------------------------------------------------------------------- #
# Static frontend                                                             #
# --------------------------------------------------------------------------- #
@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
