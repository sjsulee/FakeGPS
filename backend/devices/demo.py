"""In-memory fake device so the UI and routing can be developed with no hardware.

Enabled with FAKEGPS_DEMO=1. Exposes the same LocationDriver interface and just
records the last pushed coordinate.
"""
from __future__ import annotations

import logging

from . import DeviceInfo, LocationDriver

log = logging.getLogger("fakegps.demo")


class DemoLocationDriver(LocationDriver):
    def __init__(self, info: DeviceInfo):
        self.info = info
        self.last: tuple[float, float] | None = None
        self.last_fix = None

    def connect(self) -> "DemoLocationDriver":
        return self

    def set_location(self, lat: float, lon: float) -> None:
        self.last = (lat, lon)
        log.info("[demo %s] -> %.6f, %.6f", self.info.udid, lat, lon)
    
    def set_location_fix(self, fix) -> None:
        """Set location with full GPS metadata."""
        from ..routing import LocationFix
        if isinstance(fix, LocationFix):
            self.last = (fix.lat, fix.lon)
            self.last_fix = fix
            log.info(
                "[demo %s] -> %.6f, %.6f | alt=%.1fm spd=%.1fm/s brg=%.0f° acc=±%.1fm",
                self.info.udid, fix.lat, fix.lon, fix.altitude, fix.speed, 
                fix.bearing, fix.h_accuracy
            )
        else:
            self.set_location(fix.lat, fix.lon)

    def clear(self) -> None:
        self.last = None
        self.last_fix = None
        log.info("[demo %s] cleared", self.info.udid)


def list_devices() -> list[DeviceInfo]:
    return [
        DeviceInfo(udid="DEMO-IOS-0001", name="Demo iPhone", platform="ios", os_version="17.5"),
        DeviceInfo(udid="DEMO-ANDROID-1", name="Demo Pixel", platform="android", os_version="14"),
    ]


def open_driver(udid: str) -> DemoLocationDriver:
    for info in list_devices():
        if info.udid == udid:
            return DemoLocationDriver(info).connect()
    raise RuntimeError(f"demo device {udid} not found")
