"""Device drivers: a uniform interface over iOS and Android location backends."""
from __future__ import annotations

import abc
from dataclasses import dataclass


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
        """Move the device's reported location to (lat, lon)."""

    @abc.abstractmethod
    def clear(self) -> None:
        """Stop simulating; return device to its real GPS."""

    def close(self) -> None:
        """Release the underlying connection."""
        try:
            self.clear()
        except Exception:
            pass
