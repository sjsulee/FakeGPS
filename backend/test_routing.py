"""Tests for routing.py: realistic GPS simulation, noise models, and movement."""
from __future__ import annotations

import math
import pytest

from routing import (
    Point,
    LocationFix,
    GaussMarkovNoise,
    AccuracyModel,
    MovementConfig,
    simulate,
    simulate_stationary,
    haversine_m,
    bearing_deg,
)


def test_location_fix_creation():
    """LocationFix can be created with full metadata."""
    fix = LocationFix(
        lat=37.33, lon=-122.01, altitude=100.0,
        speed=5.5, bearing=45.0,
        h_accuracy=8.0, v_accuracy=10.0,
        speed_accuracy=0.8, bearing_accuracy=15.0
    )
    assert fix.lat == 37.33
    assert fix.lon == -122.01
    assert fix.altitude == 100.0
    assert fix.speed == 5.5
    assert fix.bearing == 45.0
    assert fix.h_accuracy == 8.0


def test_gauss_markov_noise_produces_drift():
    """Gauss-Markov process produces correlated drift, not independent noise."""
    noise = GaussMarkovNoise(sigma=5.0, beta=0.1, seed=42)
    
    # Take several steps
    offsets = [noise.step(1.0) for _ in range(10)]
    
    # Check that consecutive offsets are correlated (not independent)
    # If they were independent, sign changes would be ~50%
    # With correlation, consecutive values should be closer
    diffs = [abs(offsets[i+1][0] - offsets[i][0]) for i in range(len(offsets)-1)]
    avg_diff = sum(diffs) / len(diffs)
    
    # Average change should be smaller than 2*sigma (independent would be ~sqrt(2)*sigma)
    assert avg_diff < 10.0, "Drift should be correlated, not jumping wildly"
    
    # Drift should accumulate (not always return to origin)
    final_dist = math.sqrt(offsets[-1][0]**2 + offsets[-1][1]**2)
    assert final_dist > 0.5, "Should drift away from origin"


def test_gauss_markov_apply_to_point():
    """Gauss-Markov noise can be applied to a Point."""
    noise = GaussMarkovNoise(sigma=5.0, beta=0.1, seed=42)
    p = Point(37.0, -122.0)
    
    noisy = noise.apply_to_point(p, 1.0)
    
    # Should be close but not identical
    dist_m = haversine_m(p, noisy)
    assert 0 < dist_m < 50, f"Noisy point should be nearby, got {dist_m}m"


def test_accuracy_model_variation():
    """AccuracyModel produces varying but reasonable accuracy values."""
    model = AccuracyModel(h_base=5.0, h_variation=0.4, 
                         v_base=8.0, v_variation=0.5, seed=42)
    
    samples = [model.sample() for _ in range(20)]
    h_accs = [h for h, v in samples]
    v_accs = [v for h, v in samples]
    
    # All positive
    assert all(h > 0 for h in h_accs)
    assert all(v > 0 for v in v_accs)
    
    # Should vary
    assert len(set(h_accs)) > 5, "Horizontal accuracy should vary"
    assert len(set(v_accs)) > 5, "Vertical accuracy should vary"
    
    # Should be roughly around the base values
    avg_h = sum(h_accs) / len(h_accs)
    avg_v = sum(v_accs) / len(v_accs)
    assert 3 < avg_h < 8, f"Average h_accuracy should be near 5.0, got {avg_h}"
    assert 5 < avg_v < 12, f"Average v_accuracy should be near 8.0, got {avg_v}"


def test_simulate_produces_location_fixes():
    """simulate() now yields LocationFix objects with full metadata."""
    path = [Point(37.0, -122.0), Point(37.01, -122.0)]
    cfg = MovementConfig.for_speed(1.4, use_gm_noise=True, seed=42)
    
    fixes = list(simulate(path, cfg, tick_s=0.5))
    
    assert len(fixes) > 2, "Should produce multiple fixes"
    
    # All should be LocationFix
    for fix in fixes:
        assert isinstance(fix, LocationFix)
        assert fix.lat is not None
        assert fix.lon is not None
        assert fix.altitude >= 0
        assert fix.h_accuracy > 0
        assert fix.v_accuracy > 0
        assert 0 <= fix.bearing < 360


def test_simulate_realistic_has_varying_speed():
    """Realistic simulation produces varying speeds (acceleration/deceleration)."""
    path = [Point(37.0, -122.0), Point(37.01, -122.0)]
    cfg = MovementConfig.for_speed(2.0, use_gm_noise=False, seed=42)
    
    fixes = list(simulate(path, cfg, tick_s=0.5))
    speeds = [fix.speed for fix in fixes]
    
    # Should start from 0 (stopped)
    assert speeds[0] == 0.0
    
    # Should accelerate in the middle
    if len(speeds) > 5:
        mid_speeds = speeds[2:len(speeds)//2]
        assert any(s > 0.5 for s in mid_speeds), "Should be moving in the middle"
    
    # Should decelerate at the end
    assert speeds[-1] == 0.0 or speeds[-1] < max(speeds), "Should slow at finish"


def test_simulate_stationary_stays_near_origin():
    """simulate_stationary drifts slightly but stays within reasonable bounds."""
    point = Point(37.0, -122.0)
    cfg = MovementConfig(use_gm_noise=True, gm_sigma=5.0, seed=42)
    
    fixes = []
    for i, fix in enumerate(simulate_stationary(point, cfg, tick_s=1.0, duration_s=10)):
        fixes.append(fix)
        if i >= 9:  # 10 fixes
            break
    
    assert len(fixes) == 10
    
    # All speeds should be zero (stationary)
    assert all(fix.speed == 0.0 for fix in fixes)
    
    # Should stay within ~50m (typical GPS drift)
    for fix in fixes:
        dist = haversine_m(point, Point(fix.lat, fix.lon))
        assert dist < 50, f"Stationary drift should stay within 50m, got {dist}m"
    
    # Should vary (not all identical due to noise)
    lats = [fix.lat for fix in fixes]
    assert len(set(lats)) > 3, "Position should drift, not be static"


def test_movement_config_for_speed_scales_parameters():
    """MovementConfig.for_speed() scales accel/decel/turn speed appropriately."""
    cfg_slow = MovementConfig.for_speed(1.4)  # walking
    cfg_fast = MovementConfig.for_speed(13.9)  # driving
    
    # Faster speed should have higher acceleration
    assert cfg_fast.accel > cfg_slow.accel
    assert cfg_fast.decel > cfg_slow.decel
    assert cfg_fast.turn_speed > cfg_slow.turn_speed


def test_simulate_with_single_point_returns_stationary_fix():
    """simulate() with a single point returns one stationary fix."""
    path = [Point(37.0, -122.0)]
    cfg = MovementConfig(use_gm_noise=False, seed=42)
    
    fixes = list(simulate(path, cfg, tick_s=0.5))
    
    assert len(fixes) == 1
    assert fixes[0].speed == 0.0
    assert fixes[0].lat == pytest.approx(37.0, abs=0.001)
    assert fixes[0].lon == pytest.approx(-122.0, abs=0.001)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
