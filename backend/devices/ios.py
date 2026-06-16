"""iOS location driver (non-jailbroken, iOS 17+ focus).

Uses pymobiledevice3 (9.x) to drive Apple's developer "simulate location"
feature — the same mechanism Xcode's *Simulate Location* uses. Nothing on the
device is modified or jailbroken.

iOS 17+ moved developer services behind a RemoteXPC tunnel exposed via
RemoteServiceDiscovery (RSD). The tunnel creates a virtual network interface on
the host and therefore needs admin rights. The supported way to provide it is
the long-running tunneld daemon:

    sudo python3 -m pymobiledevice3 remote tunneld

pymobiledevice3 9.x exposes this entire flow as **async** coroutines bound to an
event loop. Our public LocationDriver interface is synchronous (the FastAPI app
calls it via asyncio.to_thread), so this driver owns a dedicated asyncio loop on
a background thread and marshals each operation onto it with
run_coroutine_threadsafe. That keeps one consistent loop for all pymobiledevice3
I/O while presenting plain set_location()/clear() to the rest of the app.

For iOS <= 16 the legacy DtSimulateLocation service is used instead (also async
in 9.x, driven through the same loop).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from . import DeviceInfo, LocationDriver

log = logging.getLogger("fakegps.ios")


class IOSDriverError(RuntimeError):
    pass


def _major(version: str) -> int:
    try:
        return int(version.split(".", 1)[0])
    except (ValueError, AttributeError):
        return 0


async def _alist_devices() -> list[DeviceInfo]:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.usbmux import list_devices as usbmux_list

    out: list[DeviceInfo] = []
    seen: set[str] = set()
    for dev in await usbmux_list():
        # usbmux reports a device once per transport (USB + Network); dedup.
        if dev.serial in seen:
            continue
        seen.add(dev.serial)
        try:
            ld = await create_using_usbmux(serial=dev.serial)
            name = ld.get_value(key="DeviceName")
            if asyncio.iscoroutine(name):  # 9.x: get_value is async
                name = await name
            out.append(
                DeviceInfo(
                    udid=dev.serial,
                    name=name or "iPhone",
                    platform="ios",
                    os_version=ld.product_version,
                )
            )
        except Exception as e:  # one bad device shouldn't hide the rest
            log.warning("could not read %s: %s", dev.serial, e)
    return out


def list_devices() -> list[DeviceInfo]:
    """Enumerate connected iOS devices via usbmux (deduped across transports).

    pymobiledevice3 9.x's usbmux API is async; run it in a throwaway loop so this
    stays a plain sync call for the rest of the app.
    """
    try:
        import pymobiledevice3.usbmux  # noqa: F401 - probe availability
    except ImportError as e:  # pragma: no cover - dep missing
        raise IOSDriverError(f"pymobiledevice3 not available: {e}") from e
    return asyncio.run(_alist_devices())


class IOSLocationDriver(LocationDriver):
    """Persistent location-simulation session over a dedicated asyncio loop."""

    CALL_TIMEOUT = 180.0  # connect/mount can be slow on first run

    def __init__(self, info: DeviceInfo):
        self.info = info
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        # modern (iOS 17+)
        self._rsd = None
        self._dvt_cm = None
        self._sim_cm = None
        self._sim = None
        # legacy (iOS <= 16)
        self._legacy = None

    # -- dedicated event loop -------------------------------------------
    def _start_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name=f"ios-{self.info.udid[:8]}", daemon=True
        )
        self._thread.start()

    def _call(self, coro, timeout: Optional[float] = None):
        if self._loop is None:
            raise IOSDriverError("driver loop not running")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout or self.CALL_TIMEOUT)

    # -- lifecycle -------------------------------------------------------
    def connect(self) -> "IOSLocationDriver":
        self._start_loop()
        try:
            if _major(self.info.os_version) >= 17:
                self._call(self._aconnect_modern())
            else:
                self._call(self._aconnect_legacy())
        except Exception:
            self.close()
            raise
        return self

    async def _aconnect_modern(self) -> None:
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import (
            LocationSimulation,
        )
        from pymobiledevice3.services.mobile_image_mounter import auto_mount
        from pymobiledevice3.tunneld.api import get_tunneld_device_by_udid

        rsd = await get_tunneld_device_by_udid(self.info.udid)
        if rsd is None:
            raise IOSDriverError(
                "iOS 17+ requires the tunnel daemon. In a separate terminal run:\n"
                "    sudo python3 -m pymobiledevice3 remote tunneld\n"
                "then make sure the device is connected and trusted."
            )
        self._rsd = rsd

        try:
            await auto_mount(rsd)
        except AlreadyMountedError:
            pass  # DDI already present — fine
        except Exception as e:
            raise IOSDriverError(
                f"failed to mount Developer Disk Image: {e}\n"
                "Try: python3 -m pymobiledevice3 mounter auto-mount"
            ) from e

        self._dvt_cm = DvtProvider(rsd)
        dvt = await self._dvt_cm.__aenter__()
        self._sim_cm = LocationSimulation(dvt)
        self._sim = await self._sim_cm.__aenter__()
        log.info("iOS17+ location session ready for %s", self.info.udid)

    async def _aconnect_legacy(self) -> None:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.mobile_image_mounter import auto_mount
        from pymobiledevice3.services.simulate_location import DtSimulateLocation

        ld = await create_using_usbmux(serial=self.info.udid)
        try:
            await auto_mount(ld)
        except Exception as e:
            log.warning("legacy auto_mount: %s", e)
        self._legacy = DtSimulateLocation(ld)
        log.info("iOS<=16 location session ready for %s", self.info.udid)

    # -- LocationDriver --------------------------------------------------
    def set_location(self, lat: float, lon: float) -> None:
        if self._sim is not None:
            self._call(self._sim.set(lat, lon), timeout=30)
        elif self._legacy is not None:
            self._call(self._legacy.set(lat, lon), timeout=30)
        else:
            raise IOSDriverError("driver not connected; call connect() first")

    def clear(self) -> None:
        try:
            if self._sim is not None:
                self._call(self._sim.clear(), timeout=30)
            elif self._legacy is not None:
                self._call(self._legacy.clear(), timeout=30)
        except Exception as e:
            log.warning("clear failed: %s", e)

    def close(self) -> None:
        # best-effort clear + teardown of async resources on the loop
        try:
            self.clear()
        except Exception:
            pass

        async def _teardown() -> None:
            if self._sim_cm is not None:
                try:
                    await self._sim_cm.__aexit__(None, None, None)
                except Exception:
                    pass
            if self._dvt_cm is not None:
                try:
                    await self._dvt_cm.__aexit__(None, None, None)
                except Exception:
                    pass

        if self._loop is not None and self._loop.is_running():
            try:
                self._call(_teardown(), timeout=15)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._sim = self._sim_cm = self._dvt_cm = self._rsd = self._legacy = None
        self._loop = self._thread = None


def open_driver(udid: str) -> IOSLocationDriver:
    """Find the device by udid and return a connected driver."""
    for info in list_devices():
        if info.udid == udid:
            return IOSLocationDriver(info).connect()
    raise IOSDriverError(f"iOS device {udid} not found")
