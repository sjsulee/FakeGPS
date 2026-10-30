package com.fakegps.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Bridges desktop backend commands into the running [MockLocationService].
 *
 * Triggered from the host with:
 *   adb shell am broadcast -a com.fakegps.companion.SET_LOCATION \
 *       -n com.fakegps.companion/.LocationReceiver --ef lat 37.33 --ef lon -122.0
 *   adb shell am broadcast -a com.fakegps.companion.CLEAR_LOCATION \
 *       -n com.fakegps.companion/.LocationReceiver
 */
class LocationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION_SET -> {
                val lat = intent.getFloatExtra("lat", Float.NaN).toDouble()
                val lon = intent.getFloatExtra("lon", Float.NaN).toDouble()
                if (lat.isNaN() || lon.isNaN()) {
                    Log.w(TAG, "SET_LOCATION missing lat/lon")
                    return
                }
                
                // Extract optional realistic GPS fields
                val altitude = intent.getFloatExtra("altitude", 0f).toDouble()
                val speed = intent.getFloatExtra("speed", 0f)
                val bearing = intent.getFloatExtra("bearing", 0f)
                val hAccuracy = intent.getFloatExtra("h_accuracy", 5f)
                val vAccuracy = intent.getFloatExtra("v_accuracy", 3f)
                val speedAccuracy = intent.getFloatExtra("speed_accuracy", 0.5f)
                val bearingAccuracy = intent.getFloatExtra("bearing_accuracy", 10f)
                
                val live = MockLocationService.instance
                if (live != null) { 
                    live.apply(lat, lon, altitude, speed, bearing, 
                              hAccuracy, vAccuracy, speedAccuracy, bearingAccuracy)
                    return 
                }
                // Service not running: start it and hand over this fix so it isn't dropped.
                val i = Intent(context, MockLocationService::class.java)
                    .putExtra("lat", lat)
                    .putExtra("lon", lon)
                    .putExtra("altitude", altitude)
                    .putExtra("speed", speed)
                    .putExtra("bearing", bearing)
                    .putExtra("h_accuracy", hAccuracy)
                    .putExtra("v_accuracy", vAccuracy)
                    .putExtra("speed_accuracy", speedAccuracy)
                    .putExtra("bearing_accuracy", bearingAccuracy)
                try { context.startService(i) } catch (e: Exception) {
                    try { context.startForegroundService(i) } catch (e2: Exception) { Log.e(TAG, "cannot start service", e2) }
                }
            }
            ACTION_CLEAR -> MockLocationService.instance?.release()
            else -> Log.w(TAG, "unknown action ${intent.action}")
        }
    }

    private fun ensureService(context: Context): MockLocationService? {
        val live = MockLocationService.instance
        if (live != null) return live
        // Service not up yet: start it; this fix will be dropped, next one lands.
        context.startForegroundService(Intent(context, MockLocationService::class.java))
        return null
    }

    companion object {
        private const val TAG = "FakeGPS"
        const val ACTION_SET = "com.fakegps.companion.SET_LOCATION"
        const val ACTION_CLEAR = "com.fakegps.companion.CLEAR_LOCATION"
    }
}
