"""Android location driver (non-rooted) via ADB + companion mock-location app.

Android's supported, root-free way to fake location is the *mock location*
developer option. Our companion app (see android-companion/) registers itself
as a test location provider. The user selects it once under
Settings > Developer options > Select mock location app. Thereafter the backend
streams coordinates to it by broadcasting an intent over ADB, which the app
turns into setTestProviderLocation() calls.

This keeps the desktop UX identical to iOS: same set_location()/clear() shape.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from . import DeviceInfo, LocationDriver

log = logging.getLogger("fakegps.android")

COMPANION_PKG = "com.fakegps.companion"
# Intent the companion app listens for (see MockLocationService.kt).
ACTION_SET = f"{COMPANION_PKG}.SET_LOCATION"
ACTION_CLEAR = f"{COMPANION_PKG}.CLEAR_LOCATION"


class AndroidDriverError(RuntimeError):
    pass


def _adb() -> str:
    path = shutil.which("adb")
    if not path:
        raise AndroidDriverError(
            "adb not found on PATH. Install Android platform-tools and retry."
        )
    return path


def _run(args: list[str], serial: str | None = None) -> str:
    cmd = [_adb()]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if proc.returncode != 0:
        raise AndroidDriverError(
            f"adb {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def list_devices() -> list[DeviceInfo]:
    out: list[DeviceInfo] = []
    try:
        raw = _run(["devices"])
    except AndroidDriverError as e:
        log.warning("%s", e)
        return out
    for line in raw.splitlines()[1:]:
        line = line.strip()
        if not line or "\tdevice" not in line:
            continue
        serial = line.split("\t", 1)[0]
        try:
            model = _run(["shell", "getprop", "ro.product.model"], serial) or "Android"
            rel = _run(["shell", "getprop", "ro.build.version.release"], serial) or "?"
        except AndroidDriverError:
            model, rel = "Android", "?"
        out.append(DeviceInfo(udid=serial, name=model, platform="android", os_version=rel))
    return out


def _companion_installed(serial: str) -> bool:
    try:
        pkgs = _run(["shell", "pm", "list", "packages", COMPANION_PKG], serial)
        return COMPANION_PKG in pkgs
    except AndroidDriverError:
        return False


class AndroidLocationDriver(LocationDriver):
    def __init__(self, info: DeviceInfo):
        self.info = info

    def connect(self) -> "AndroidLocationDriver":
        if not _companion_installed(self.info.udid):
            raise AndroidDriverError(
                "FakeGPS companion app is not installed on this device.\n"
                "Build & install it from android-companion/ (./gradlew installDebug), "
                "then set it as the mock location app under Developer options."
            )
        # Make sure the companion's foreground service is up to receive intents.
        try:
            _run(
                ["shell", "am", "start-foreground-service",
                 "-n", f"{COMPANION_PKG}/.MockLocationService"],
                self.info.udid,
            )
        except AndroidDriverError as e:
            log.warning("could not pre-start companion service: %s", e)
        return self

    def set_location(self, lat: float, lon: float) -> None:
        _run(
            [
                "shell", "am", "broadcast",
                "-a", ACTION_SET,
                "-n", f"{COMPANION_PKG}/.LocationReceiver",
                "--ef", "lat", repr(lat),
                "--ef", "lon", repr(lon),
            ],
            self.info.udid,
        )
    
    def set_location_fix(self, fix) -> None:
        """Set location with full GPS metadata."""
        from ..routing import LocationFix
        if not isinstance(fix, LocationFix):
            self.set_location(fix.lat, fix.lon)
            return
        
        # Build intent with all available fields
        cmd = [
            "shell", "am", "broadcast",
            "-a", ACTION_SET,
            "-n", f"{COMPANION_PKG}/.LocationReceiver",
            "--ef", "lat", repr(fix.lat),
            "--ef", "lon", repr(fix.lon),
            "--ef", "altitude", repr(fix.altitude),
            "--ef", "speed", repr(fix.speed),
            "--ef", "bearing", repr(fix.bearing),
            "--ef", "h_accuracy", repr(fix.h_accuracy),
            "--ef", "v_accuracy", repr(fix.v_accuracy),
            "--ef", "speed_accuracy", repr(fix.speed_accuracy),
            "--ef", "bearing_accuracy", repr(fix.bearing_accuracy),
        ]
        _run(cmd, self.info.udid)

    def clear(self) -> None:
        try:
            _run(
                ["shell", "am", "broadcast", "-a", ACTION_CLEAR,
                 "-n", f"{COMPANION_PKG}/.LocationReceiver"],
                self.info.udid,
            )
        except AndroidDriverError as e:
            log.warning("clear failed: %s", e)


def open_driver(udid: str) -> AndroidLocationDriver:
    for info in list_devices():
        if info.udid == udid:
            return AndroidLocationDriver(info).connect()
    raise AndroidDriverError(f"Android device {udid} not found")
