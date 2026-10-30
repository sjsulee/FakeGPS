"""Geo math: great-circle interpolation, speed-based playback, GPX parsing.

Pure functions + a small generator so the device drivers can stay dumb: they
just receive (lat, lon) points to push. All angles in degrees, distances in
metres, speed in metres/second.
"""
from __future__ import annotations

import math
import random
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.lat, self.lon)


@dataclass(frozen=True)
class LocationFix:
    """A complete GPS fix with all metadata for realistic simulation."""
    lat: float
    lon: float
    altitude: float = 0.0
    speed: float = 0.0  # m/s
    bearing: float = 0.0  # degrees clockwise from north
    h_accuracy: float = 5.0  # horizontal accuracy, metres
    v_accuracy: float = 3.0  # vertical accuracy, metres
    speed_accuracy: float = 0.5  # m/s
    bearing_accuracy: float = 10.0  # degrees
    timestamp: Optional[float] = None  # Unix timestamp; generated if None

    def as_point(self) -> Point:
        return Point(self.lat, self.lon)


def _rad(d: float) -> float:
    return d * math.pi / 180.0


def _deg(r: float) -> float:
    return r * 180.0 / math.pi


def haversine_m(a: Point, b: Point) -> float:
    """Great-circle distance between two points, in metres."""
    p1, p2 = _rad(a.lat), _rad(b.lat)
    dp = _rad(b.lat - a.lat)
    dl = _rad(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: Point, b: Point) -> float:
    """Initial bearing from a to b, degrees clockwise from north (0..360)."""
    p1, p2 = _rad(a.lat), _rad(b.lat)
    dl = _rad(b.lon - a.lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (_deg(math.atan2(y, x)) + 360.0) % 360.0


def destination(origin: Point, bearing: float, distance_m: float) -> Point:
    """Point reached by travelling distance_m from origin along bearing."""
    d = distance_m / EARTH_RADIUS_M
    br = _rad(bearing)
    p1 = _rad(origin.lat)
    l1 = _rad(origin.lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return Point(_deg(p2), (_deg(l2) + 540.0) % 360.0 - 180.0)


def densify(path: list[Point], step_m: float) -> list[Point]:
    """Insert intermediate points so consecutive points are <= step_m apart.

    This is what turns a clicked two-spot or multi-spot route into a smooth
    stream of GPS fixes. step_m is derived from speed * tick interval.
    """
    if step_m <= 0:
        raise ValueError("step_m must be positive")
    if len(path) < 2:
        return list(path)
    out: list[Point] = [path[0]]
    for a, b in zip(path, path[1:]):
        seg = haversine_m(a, b)
        if seg <= step_m:
            out.append(b)
            continue
        brg = bearing_deg(a, b)
        n = int(seg // step_m)
        for i in range(1, n + 1):
            out.append(destination(a, brg, step_m * i))
        if out[-1].as_tuple() != b.as_tuple():
            out.append(b)
    return out


def walk(path: list[Point], speed_mps: float, tick_s: float) -> Iterator[Point]:
    """Yield one point per tick so playback advances at ~speed_mps.

    Used by the server's route/joystick playback loop; the caller sleeps tick_s
    between yields and pushes each point to the device.
    """
    if speed_mps <= 0:
        raise ValueError("speed must be positive")
    step = max(speed_mps * tick_s, 0.01)
    for pt in densify(path, step):
        yield pt


# Common speed presets (m/s)
SPEED_PRESETS = {
    "walk": 1.4,      # ~5 km/h
    "run": 3.3,       # ~12 km/h
    "cycle": 6.0,     # ~21 km/h
    "drive": 13.9,    # ~50 km/h
}


# --------------------------------------------------------------------------- #
# Realistic GPS noise and accuracy                                            #
# --------------------------------------------------------------------------- #
class GaussMarkovNoise:
    """Correlated GPS drift using Gauss-Markov process.
    
    This produces realistic GPS wander: position drifts gradually rather than
    jumping randomly each tick. Models common GPS multipath/atmosphere effects.
    """
    def __init__(self, sigma: float = 5.0, beta: float = 0.1, seed: Optional[int] = None):
        """
        Args:
            sigma: steady-state std deviation in metres (typical 3-8m for consumer GPS)
            beta: correlation time constant; smaller = slower drift (0.05-0.2 typical)
            seed: RNG seed for reproducibility
        """
        self.sigma = sigma
        self.beta = beta
        self.rng = random.Random(seed)
        # Gauss-Markov state: north and east offset in metres
        self.offset_n = 0.0
        self.offset_e = 0.0
        
    def step(self, dt: float = 1.0) -> tuple[float, float]:
        """Advance one time step and return (north_offset_m, east_offset_m)."""
        # Gauss-Markov: x(t+dt) = x(t)*exp(-beta*dt) + w
        decay = math.exp(-self.beta * dt)
        noise_std = self.sigma * math.sqrt(1 - decay * decay)
        self.offset_n = self.offset_n * decay + self.rng.gauss(0, noise_std)
        self.offset_e = self.offset_e * decay + self.rng.gauss(0, noise_std)
        return (self.offset_n, self.offset_e)
    
    def apply_to_point(self, p: Point, dt: float = 1.0) -> Point:
        """Apply drift to a point and return the noisy position."""
        n, e = self.step(dt)
        # Convert north/east offset to lat/lon
        # Rough approximation: 1 degree lat ≈ 111 km, lon varies by latitude
        dlat = n / 111_000.0
        dlon = e / (111_000.0 * math.cos(_rad(p.lat)))
        return Point(p.lat + dlat, p.lon + dlon)


class AccuracyModel:
    """Realistic horizontal/vertical accuracy that varies over time."""
    def __init__(self, 
                 h_base: float = 5.0, 
                 h_variation: float = 0.4,
                 v_base: float = 8.0,
                 v_variation: float = 0.5,
                 seed: Optional[int] = None):
        """
        Args:
            h_base: baseline horizontal accuracy in metres (3-10m typical)
            h_variation: fractional random variation (0.3-0.5 typical)
            v_base: baseline vertical accuracy (typically worse than horizontal)
            v_variation: fractional variation for vertical
            seed: RNG seed
        """
        self.h_base = h_base
        self.h_variation = h_variation
        self.v_base = v_base
        self.v_variation = v_variation
        self.rng = random.Random(seed)
        
    def sample(self) -> tuple[float, float]:
        """Return (h_accuracy, v_accuracy) in metres."""
        h = max(1.0, self.h_base * (1 + self.rng.uniform(-self.h_variation, self.h_variation)))
        v = max(1.0, self.v_base * (1 + self.rng.uniform(-self.v_variation, self.v_variation)))
        return (h, v)


# --------------------------------------------------------------------------- #
# Realistic movement model                                                    #
# --------------------------------------------------------------------------- #
# Constant-speed playback (walk()) produces unnaturally rigid traces: instant
# start/stop, full speed through hairpin turns, zero GPS noise. For exercising
# your own location-aware app under lifelike conditions, simulate() adds a
# trapezoidal speed profile (accelerate from rest, brake before turns and the
# finish), gentle per-tick speed variation, and optional GPS jitter.

@dataclass
class MovementConfig:
    max_speed: float = 1.4          # cruising speed (m/s)
    accel: float = 0.8              # acceleration (m/s^2); ~walking
    decel: float = 1.2              # braking (m/s^2)
    turn_speed: float = 0.6         # speed cap through a sharp turn (m/s)
    turn_threshold_deg: float = 40.0  # bearing change counted as "sharp"
    jitter_m: float = 0.0           # GPS noise std-dev in metres (0 = off; for legacy simple jitter)
    speed_variation: float = 0.06   # fractional random speed wobble (+/-)
    seed: Optional[int] = None      # set for reproducible traces
    # New realistic simulation parameters
    use_gm_noise: bool = True       # Use Gauss-Markov correlated noise instead of simple jitter
    gm_sigma: float = 5.0           # Gauss-Markov noise level (m)
    gm_beta: float = 0.1            # Gauss-Markov correlation (smaller = slower drift)
    base_altitude: float = 50.0     # Starting altitude in metres
    altitude_variation: float = 5.0 # Random altitude variation (m)
    h_accuracy_base: float = 5.0    # Baseline horizontal accuracy (m)
    h_accuracy_var: float = 0.4     # Horizontal accuracy variation fraction
    v_accuracy_base: float = 8.0    # Baseline vertical accuracy (m)
    v_accuracy_var: float = 0.5     # Vertical accuracy variation fraction
    speed_accuracy: float = 0.5     # Speed measurement accuracy (m/s)
    bearing_accuracy: float = 10.0  # Bearing accuracy (degrees)

    @classmethod
    def for_speed(cls, max_speed: float, **kw) -> "MovementConfig":
        """Sensible accel/turn defaults scaled to the cruising speed."""
        return cls(
            max_speed=max_speed,
            accel=max(0.6, max_speed * 0.4),
            decel=max(0.9, max_speed * 0.6),
            turn_speed=min(max_speed, max(0.6, max_speed * 0.35)),
            **kw,
        )


def _cumulative(path: list[Point]) -> list[float]:
    """Arc length from the start to each vertex (metres)."""
    out = [0.0]
    for a, b in zip(path, path[1:]):
        out.append(out[-1] + haversine_m(a, b))
    return out


def point_at_distance(path: list[Point], cum: list[float], d: float) -> Point:
    """Interpolate the point that lies d metres along the polyline."""
    if d <= 0:
        return path[0]
    if d >= cum[-1]:
        return path[-1]
    # find the segment containing d
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] <= d:
            lo = mid + 1
        else:
            hi = mid
    i = lo - 1  # segment path[i] -> path[i+1]
    seg_len = cum[i + 1] - cum[i]
    if seg_len <= 0:
        return path[i + 1]
    brg = bearing_deg(path[i], path[i + 1])
    return destination(path[i], brg, d - cum[i])


def _turn_distances(path: list[Point], cum: list[float], threshold: float) -> list[float]:
    """Arc-length positions of vertices where the route turns sharply."""
    turns: list[float] = []
    for i in range(1, len(path) - 1):
        b_in = bearing_deg(path[i - 1], path[i])
        b_out = bearing_deg(path[i], path[i + 1])
        delta = abs((b_out - b_in + 180.0) % 360.0 - 180.0)
        if delta >= threshold:
            turns.append(cum[i])
    return turns


def _jitter(p: Point, sigma_m: float, rng: random.Random) -> Point:
    """Offset a point by Gaussian GPS-style noise of std-dev sigma_m metres."""
    if sigma_m <= 0:
        return p
    dist = abs(rng.gauss(0.0, sigma_m))
    brg = rng.uniform(0.0, 360.0)
    return destination(p, brg, dist)


def simulate(path: list[Point], cfg: MovementConfig, tick_s: float) -> Iterator[LocationFix]:
    """Yield one fix per tick following a lifelike speed profile with full GPS metadata.

    The caller sleeps tick_s between yields and pushes each fix to the device.
    Speed eases up from rest, brakes for sharp turns and the finish line,
    wobbles slightly, and carries realistic GPS noise and accuracy variation.
    """
    if len(path) < 2:
        # Single point: return stationary fix
        if path:
            noise_gen = GaussMarkovNoise(cfg.gm_sigma, cfg.gm_beta, cfg.seed) if cfg.use_gm_noise else None
            acc_model = AccuracyModel(cfg.h_accuracy_base, cfg.h_accuracy_var, 
                                     cfg.v_accuracy_base, cfg.v_accuracy_var, cfg.seed)
            rng = random.Random(cfg.seed)
            p = path[0]
            if noise_gen:
                p = noise_gen.apply_to_point(p, tick_s)
            h_acc, v_acc = acc_model.sample()
            alt = cfg.base_altitude + rng.uniform(-cfg.altitude_variation, cfg.altitude_variation)
            yield LocationFix(
                lat=p.lat, lon=p.lon, altitude=alt,
                speed=0.0, bearing=0.0,
                h_accuracy=h_acc, v_accuracy=v_acc,
                speed_accuracy=cfg.speed_accuracy,
                bearing_accuracy=cfg.bearing_accuracy,
                timestamp=time.time()
            )
        return
    if tick_s <= 0:
        raise ValueError("tick_s must be positive")

    cum = _cumulative(path)
    total = cum[-1]
    if total <= 0:
        # Degenerate path
        noise_gen = GaussMarkovNoise(cfg.gm_sigma, cfg.gm_beta, cfg.seed) if cfg.use_gm_noise else None
        acc_model = AccuracyModel(cfg.h_accuracy_base, cfg.h_accuracy_var, 
                                 cfg.v_accuracy_base, cfg.v_accuracy_var, cfg.seed)
        rng = random.Random(cfg.seed)
        p = path[0]
        if noise_gen:
            p = noise_gen.apply_to_point(p, tick_s)
        h_acc, v_acc = acc_model.sample()
        alt = cfg.base_altitude + rng.uniform(-cfg.altitude_variation, cfg.altitude_variation)
        yield LocationFix(
            lat=p.lat, lon=p.lon, altitude=alt,
            speed=0.0, bearing=0.0,
            h_accuracy=h_acc, v_accuracy=v_acc,
            speed_accuracy=cfg.speed_accuracy,
            bearing_accuracy=cfg.bearing_accuracy,
            timestamp=time.time()
        )
        return
    turns = _turn_distances(path, cum, cfg.turn_threshold_deg)
    rng = random.Random(cfg.seed)
    
    # Initialize realistic GPS models
    noise_gen = GaussMarkovNoise(cfg.gm_sigma, cfg.gm_beta, cfg.seed) if cfg.use_gm_noise else None
    acc_model = AccuracyModel(cfg.h_accuracy_base, cfg.h_accuracy_var, 
                             cfg.v_accuracy_base, cfg.v_accuracy_var, cfg.seed)

    d = 0.0
    v = 0.0
    prev_point = path[0]
    
    # First fix
    p = path[0]
    if cfg.use_gm_noise and noise_gen:
        p = noise_gen.apply_to_point(p, tick_s)
    elif cfg.jitter_m > 0:
        p = _jitter(p, cfg.jitter_m, rng)
    h_acc, v_acc = acc_model.sample()
    alt = cfg.base_altitude + rng.uniform(-cfg.altitude_variation, cfg.altitude_variation)
    yield LocationFix(
        lat=p.lat, lon=p.lon, altitude=alt,
        speed=0.0, bearing=0.0,
        h_accuracy=h_acc, v_accuracy=v_acc,
        speed_accuracy=cfg.speed_accuracy,
        bearing_accuracy=cfg.bearing_accuracy,
        timestamp=time.time()
    )
    
    # guard against pathological configs producing an unbounded loop
    max_ticks = int(total / max(0.01, cfg.max_speed) / tick_s) + 10_000
    for _ in range(max_ticks):
        # distance still needed to brake to a stop / to turn speed
        brake_to_stop = v * v / (2 * cfg.decel)
        target = cfg.max_speed

        # slow for the finish line
        if (total - d) <= brake_to_stop + v * tick_s:
            target = 0.0
        # slow for the next sharp turn ahead
        for td in turns:
            ahead = td - d
            if ahead < 0:
                continue
            brake_to_turn = max(0.0, (v * v - cfg.turn_speed * cfg.turn_speed) / (2 * cfg.decel))
            if ahead <= brake_to_turn + v * tick_s:
                target = min(target, cfg.turn_speed)
            break  # only the nearest upcoming turn matters

        # ease current speed toward target
        if v < target:
            v = min(target, v + cfg.accel * tick_s)
        elif v > target:
            v = max(target, v - cfg.decel * tick_s)

        # natural wobble (never negative)
        eff = v * (1.0 + rng.uniform(-cfg.speed_variation, cfg.speed_variation))
        eff = max(0.0, eff)

        d += eff * tick_s
        if d >= total:
            # Final fix
            p = path[-1]
            if cfg.use_gm_noise and noise_gen:
                p = noise_gen.apply_to_point(p, tick_s)
            elif cfg.jitter_m > 0:
                p = _jitter(p, cfg.jitter_m, rng)
            h_acc, v_acc = acc_model.sample()
            alt += rng.uniform(-cfg.altitude_variation * 0.5, cfg.altitude_variation * 0.5)
            yield LocationFix(
                lat=p.lat, lon=p.lon, altitude=alt,
                speed=0.0, bearing=bearing_deg(prev_point, path[-1]),
                h_accuracy=h_acc, v_accuracy=v_acc,
                speed_accuracy=cfg.speed_accuracy,
                bearing_accuracy=cfg.bearing_accuracy,
                timestamp=time.time()
            )
            return
        
        curr_point = point_at_distance(path, cum, d)
        bearing = bearing_deg(prev_point, curr_point) if haversine_m(prev_point, curr_point) > 0.1 else 0.0
        
        # Apply noise
        p = curr_point
        if cfg.use_gm_noise and noise_gen:
            p = noise_gen.apply_to_point(p, tick_s)
        elif cfg.jitter_m > 0:
            p = _jitter(p, cfg.jitter_m, rng)
        
        h_acc, v_acc = acc_model.sample()
        alt += rng.uniform(-cfg.altitude_variation * 0.5, cfg.altitude_variation * 0.5)
        
        yield LocationFix(
            lat=p.lat, lon=p.lon, altitude=alt,
            speed=eff, bearing=bearing,
            h_accuracy=h_acc, v_accuracy=v_acc,
            speed_accuracy=cfg.speed_accuracy,
            bearing_accuracy=cfg.bearing_accuracy,
            timestamp=time.time()
        )
        prev_point = curr_point

        # came to rest short of the end (shouldn't normally happen) -> nudge on
        if v <= 0.0 and target <= 0.0 and (total - d) > 1.0:
            v = cfg.accel * tick_s


def simulate_stationary(point: Point, cfg: MovementConfig, tick_s: float, 
                       duration_s: Optional[float] = None) -> Iterator[LocationFix]:
    """Yield GPS fixes for a stationary position with realistic drift.
    
    Simulates a phone sitting still: position drifts gradually (Gauss-Markov),
    accuracy varies, altitude wobbles slightly. Useful for testing stationary
    location features.
    
    Args:
        point: The nominal stationary position
        cfg: Movement configuration (uses noise/accuracy params)
        tick_s: Update interval in seconds
        duration_s: Total duration; None means infinite
    """
    if tick_s <= 0:
        raise ValueError("tick_s must be positive")
    
    noise_gen = GaussMarkovNoise(cfg.gm_sigma * 0.6, cfg.gm_beta * 0.5, cfg.seed) if cfg.use_gm_noise else None
    acc_model = AccuracyModel(cfg.h_accuracy_base, cfg.h_accuracy_var, 
                             cfg.v_accuracy_base, cfg.v_accuracy_var, cfg.seed)
    rng = random.Random(cfg.seed)
    alt = cfg.base_altitude + rng.uniform(-cfg.altitude_variation, cfg.altitude_variation)
    
    ticks = int(duration_s / tick_s) if duration_s else 999999999
    for _ in range(ticks):
        p = point
        if noise_gen:
            p = noise_gen.apply_to_point(p, tick_s)
        elif cfg.jitter_m > 0:
            p = _jitter(p, cfg.jitter_m * 0.5, rng)  # Less jitter when stationary
        
        h_acc, v_acc = acc_model.sample()
        alt += rng.uniform(-cfg.altitude_variation * 0.3, cfg.altitude_variation * 0.3)
        
        yield LocationFix(
            lat=p.lat, lon=p.lon, altitude=alt,
            speed=0.0, bearing=0.0,
            h_accuracy=h_acc, v_accuracy=v_acc,
            speed_accuracy=cfg.speed_accuracy,
            bearing_accuracy=cfg.bearing_accuracy,
            timestamp=time.time()
        )


def parse_gpx(data: str) -> list[Point]:
    """Extract an ordered point list from GPX track/route/waypoint data."""
    root = ET.fromstring(data)
    # GPX namespaces vary; match by local tag name to stay version-agnostic.
    pts: list[Point] = []

    def localname(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    # Prefer track points, then route points, then bare waypoints.
    for wanted in ("trkpt", "rtept", "wpt"):
        for el in root.iter():
            if localname(el.tag) != wanted:
                continue
            try:
                pts.append(Point(float(el.attrib["lat"]), float(el.attrib["lon"])))
            except (KeyError, ValueError):
                continue
        if pts:
            break
    if not pts:
        raise ValueError("no track/route/waypoint coordinates found in GPX")
    return pts
