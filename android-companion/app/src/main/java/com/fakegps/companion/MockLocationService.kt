package com.fakegps.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log

/**
 * Foreground service that owns a single test location provider and applies fake
 * fixes pushed in by [LocationReceiver].
 *
 * Requires the user to have selected this app under
 * Settings > Developer options > Select mock location app. No root needed.
 */
class MockLocationService : Service() {

    private lateinit var lm: LocationManager
    private var providerReady = false

    // A single setTestProviderLocation() fix "ages out": after a second or two
    // the fused provider stops trusting it and falls back to the real GPS, so
    // the location appears to drift back to the device's true position. We keep
    // the mock alive by re-pushing the latest fix with a fresh timestamp on a
    // heartbeat. This runs on the device itself, so the location holds even when
    // the desktop isn't actively streaming (teleport / idle joystick).
    private val handler = Handler(Looper.getMainLooper())
    @Volatile private var lastLat: Double? = null
    @Volatile private var lastLon: Double? = null
    @Volatile private var lastAltitude: Double = 0.0
    @Volatile private var lastSpeed: Float = 0f
    @Volatile private var lastBearing: Float = 0f
    @Volatile private var lastHAccuracy: Float = 5f
    @Volatile private var lastVAccuracy: Float = 3f
    @Volatile private var lastSpeedAccuracy: Float = 0.5f
    @Volatile private var lastBearingAccuracy: Float = 10f
    private val heartbeat = object : Runnable {
        override fun run() {
            pushCurrent()
            handler.postDelayed(this, HEARTBEAT_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        lm = getSystemService(LOCATION_SERVICE) as LocationManager
        try {
            startInForeground()
        } catch (e: Exception) {
            // Android 14+/15 refuses a location FGS started from background; keep running anyway.
            Log.w(TAG, "startForeground refused, continuing in background", e)
        }
        ensureProvider()
        // Restore the last coordinate so a restart doesn't drop back to real GPS.
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        if (prefs.contains("lat") && prefs.contains("lon")) {
            lastLat = java.lang.Double.longBitsToDouble(prefs.getLong("lat", 0))
            lastLon = java.lang.Double.longBitsToDouble(prefs.getLong("lon", 0))
            lastAltitude = java.lang.Double.longBitsToDouble(prefs.getLong("altitude", 0))
            lastSpeed = prefs.getFloat("speed", 0f)
            lastBearing = prefs.getFloat("bearing", 0f)
            lastHAccuracy = prefs.getFloat("h_accuracy", 5f)
            lastVAccuracy = prefs.getFloat("v_accuracy", 3f)
            lastSpeedAccuracy = prefs.getFloat("speed_accuracy", 0.5f)
            lastBearingAccuracy = prefs.getFloat("bearing_accuracy", 10f)
            pushCurrent()
            handler.postDelayed(heartbeat, HEARTBEAT_MS)
            Log.i(TAG, "restored last location")
        }
    }

    private fun ensureProvider() {
        if (providerReady) return
        try {
            // Replace the real GPS provider with a controllable test provider.
            lm.addTestProvider(
                LocationManager.GPS_PROVIDER,
                false, false, false, false,
                true, true, true,
                android.location.Criteria.POWER_LOW,
                android.location.Criteria.ACCURACY_FINE
            )
            lm.setTestProviderEnabled(LocationManager.GPS_PROVIDER, true)
            providerReady = true
            Log.i(TAG, "test provider ready")
        } catch (e: SecurityException) {
            Log.e(TAG, "Not allowed to mock. Select this app as the mock location app.", e)
        } catch (e: IllegalArgumentException) {
            // Provider may already exist; treat as ready.
            providerReady = true
        }
    }

    fun apply(lat: Double, lon: Double, altitude: Double = 0.0, 
              speed: Float = 0f, bearing: Float = 0f,
              hAccuracy: Float = 5f, vAccuracy: Float = 3f,
              speedAccuracy: Float = 0.5f, bearingAccuracy: Float = 10f) {
        ensureProvider()
        if (!providerReady) return
        lastLat = lat
        lastLon = lon
        lastAltitude = altitude
        lastSpeed = speed
        lastBearing = bearing
        lastHAccuracy = hAccuracy
        lastVAccuracy = vAccuracy
        lastSpeedAccuracy = speedAccuracy
        lastBearingAccuracy = bearingAccuracy
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .putLong("lat", java.lang.Double.doubleToRawLongBits(lat))
            .putLong("lon", java.lang.Double.doubleToRawLongBits(lon))
            .putLong("altitude", java.lang.Double.doubleToRawLongBits(altitude))
            .putFloat("speed", speed)
            .putFloat("bearing", bearing)
            .putFloat("h_accuracy", hAccuracy)
            .putFloat("v_accuracy", vAccuracy)
            .putFloat("speed_accuracy", speedAccuracy)
            .putFloat("bearing_accuracy", bearingAccuracy)
            .apply()
        pushCurrent()
        // (re)arm the heartbeat so this fix is kept fresh until the next update.
        handler.removeCallbacks(heartbeat)
        handler.postDelayed(heartbeat, HEARTBEAT_MS)
    }

    /** Push the last requested coordinate with an up-to-the-moment timestamp. */
    private fun pushCurrent() {
        val lat = lastLat ?: return
        val lon = lastLon ?: return
        if (!providerReady) return
        val loc = Location(LocationManager.GPS_PROVIDER).apply {
            latitude = lat
            longitude = lon
            accuracy = lastHAccuracy
            altitude = lastAltitude
            speed = lastSpeed
            bearing = lastBearing
            time = System.currentTimeMillis()
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                bearingAccuracyDegrees = lastBearingAccuracy
                verticalAccuracyMeters = lastVAccuracy
                speedAccuracyMetersPerSecond = lastSpeedAccuracy
            }
        }
        try {
            lm.setTestProviderLocation(LocationManager.GPS_PROVIDER, loc)
        } catch (e: SecurityException) {
            Log.e(TAG, "mock not enabled", e)
        }
    }

    fun currentLat(): Double? = lastLat
    fun currentLon(): Double? = lastLon
    fun isMockAllowed(): Boolean {
        ensureProvider()
        return providerReady
    }

    fun release() {
        handler.removeCallbacks(heartbeat)
        lastLat = null
        lastLon = null
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().apply()
        try {
            lm.removeTestProvider(LocationManager.GPS_PROVIDER)
        } catch (_: Exception) {
        }
        providerReady = false
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        instance = this
        val lat = intent?.getDoubleExtra("lat", Double.NaN) ?: Double.NaN
        val lon = intent?.getDoubleExtra("lon", Double.NaN) ?: Double.NaN
        if (!lat.isNaN() && !lon.isNaN()) {
            val altitude = intent?.getDoubleExtra("altitude", 0.0) ?: 0.0
            val speed = intent?.getFloatExtra("speed", 0f) ?: 0f
            val bearing = intent?.getFloatExtra("bearing", 0f) ?: 0f
            val hAccuracy = intent?.getFloatExtra("h_accuracy", 5f) ?: 5f
            val vAccuracy = intent?.getFloatExtra("v_accuracy", 3f) ?: 3f
            val speedAccuracy = intent?.getFloatExtra("speed_accuracy", 0.5f) ?: 0.5f
            val bearingAccuracy = intent?.getFloatExtra("bearing_accuracy", 10f) ?: 10f
            apply(lat, lon, altitude, speed, bearing, hAccuracy, vAccuracy, speedAccuracy, bearingAccuracy)
        }
        return START_STICKY
    }

    override fun onDestroy() {
        release()
        instance = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startInForeground() {
        val mgr = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL, "FakeGPS", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val n: Notification = Notification.Builder(this, CHANNEL)
            .setContentTitle("FakeGPS Companion")
            .setContentText("Simulating location")
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .build()
        startForeground(1, n)
    }

    companion object {
        private const val TAG = "FakeGPS"
        private const val CHANNEL = "fakegps"
        private const val PREFS = "fakegps_state"
        private const val HEARTBEAT_MS = 1000L  // re-push interval to keep mock fresh

        // Simple singleton so the broadcast receiver can reach the live service.
        @Volatile
        var instance: MockLocationService? = null
    }
}
