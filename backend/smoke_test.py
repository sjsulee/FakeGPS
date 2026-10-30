#!/usr/bin/env python3
"""Quick smoke test for new realistic GPS simulation features."""
import sys
sys.path.insert(0, '.')

from routing import (
    Point, LocationFix, GaussMarkovNoise, AccuracyModel,
    MovementConfig, simulate, simulate_stationary, haversine_m
)

print("Testing LocationFix...")
fix = LocationFix(lat=37.0, lon=-122.0, altitude=100, speed=5.0, bearing=45.0)
assert fix.lat == 37.0
assert fix.as_point().lat == 37.0
print("✓ LocationFix works")

print("\nTesting GaussMarkovNoise...")
noise = GaussMarkovNoise(sigma=5.0, beta=0.1, seed=42)
n, e = noise.step(1.0)
assert abs(n) < 50 and abs(e) < 50, "Noise should be reasonable"
p = Point(37.0, -122.0)
noisy = noise.apply_to_point(p, 1.0)
dist = haversine_m(p, noisy)
assert 0 < dist < 100, f"Noisy point distance: {dist}m"
print(f"✓ Gauss-Markov noise: drift {dist:.1f}m")

print("\nTesting AccuracyModel...")
acc = AccuracyModel(seed=42)
h, v = acc.sample()
assert 1 < h < 50 and 1 < v < 50
print(f"✓ Accuracy: h={h:.1f}m, v={v:.1f}m")

print("\nTesting simulate() with realistic movement...")
path = [Point(37.0, -122.0), Point(37.005, -122.0)]
cfg = MovementConfig.for_speed(2.0, use_gm_noise=True, seed=42)
fixes = []
for i, fix in enumerate(simulate(path, cfg, tick_s=0.5)):
    fixes.append(fix)
    if i >= 5:
        break
assert len(fixes) > 2
assert all(isinstance(f, LocationFix) for f in fixes)
assert fixes[0].speed == 0.0, "Should start stopped"
print(f"✓ simulate() produced {len(fixes)} fixes")
print(f"  First: lat={fixes[0].lat:.6f}, spd={fixes[0].speed:.1f}m/s, acc=±{fixes[0].h_accuracy:.1f}m")
print(f"  Last:  lat={fixes[-1].lat:.6f}, spd={fixes[-1].speed:.1f}m/s, acc=±{fixes[-1].h_accuracy:.1f}m")

print("\nTesting simulate_stationary()...")
point = Point(37.0, -122.0)
cfg = MovementConfig(use_gm_noise=True, gm_sigma=5.0, seed=42)
stat_fixes = []
for i, fix in enumerate(simulate_stationary(point, cfg, tick_s=1.0, duration_s=5)):
    stat_fixes.append(fix)
    if i >= 4:
        break
assert len(stat_fixes) == 5
assert all(f.speed == 0.0 for f in stat_fixes), "Stationary should have zero speed"
distances = [haversine_m(point, Point(f.lat, f.lon)) for f in stat_fixes]
assert all(d < 50 for d in distances), "Should stay within 50m"
print(f"✓ simulate_stationary() produced {len(stat_fixes)} fixes")
print(f"  Max drift: {max(distances):.1f}m, avg: {sum(distances)/len(distances):.1f}m")

print("\n✅ All smoke tests passed!")
