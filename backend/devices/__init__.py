"""Device drivers: a uniform interface over iOS and Android location backends."""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..routing import LocationFix


@dataclass
class DeviceInfo:
    udid: str
    name: str
    platform: str          # "ios" | "android"
    os_version: str
    connected: bool = True


class LocationDriver(abc.ABC):
    """A connected device we can push simulated coordinates to.

    Implementations keep whatever session/connection they need open between
    set_location() calls so route/joystick playback can stream fixes cheaply.
    """

    info: DeviceInfo

    @abc.abstractmethod
    def set_location(self, lat: float, lon: float) -> None:
        """Move the device's reported location to (lat, lon).
        
        Legacy method for simple teleport/joystick. Use set_location_fix for
        full GPS simulation with altitude, speed, bearing, and accuracies.
        """

    def set_location_fix(self, fix: "LocationFix") -> None:
        """Set location with complete GPS metadata.
        
        Default implementation calls set_location with lat/lon only.
        Platform drivers should override to pass full metadata when supported.
        """
        self.set_location(fix.lat, fix.lon)

    @abc.abstractmethod
    def clear(self) -> None:
        """Stop simulating; return device to its real GPS."""

    def close(self) -> None:
        """Release the underlying connection."""
        try:
            self.clear()
        except Exception:
            pass
