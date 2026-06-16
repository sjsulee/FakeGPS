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
    private val heartbeat = object : Runnable {
        override fun run() {
            pushCurrent()
            handler.postDelayed(this, HEARTBEAT_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        lm = getSystemService(LOCATION_SERVICE) as LocationManager
        startInForeground()
        ensureProvider()
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

    fun apply(lat: Double, lon: Double) {
        ensureProvider()
        if (!providerReady) return
        lastLat = lat
        lastLon = lon
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
            accuracy = 5f
            altitude = 0.0
            time = System.currentTimeMillis()
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                bearingAccuracyDegrees = 0.1f
                verticalAccuracyMeters = 0.1f
                speedAccuracyMetersPerSecond = 0.01f
            }
        }
        try {
            lm.setTestProviderLocation(LocationManager.GPS_PROVIDER, loc)
        } catch (e: SecurityException) {
            Log.e(TAG, "mock not enabled", e)
        }
    }

    fun release() {
        handler.removeCallbacks(heartbeat)
        lastLat = null
        lastLon = null
        try {
            lm.removeTestProvider(LocationManager.GPS_PROVIDER)
        } catch (_: Exception) {
        }
        providerReady = false
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        instance = this
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
        private const val HEARTBEAT_MS = 1000L  // re-push interval to keep mock fresh

        // Simple singleton so the broadcast receiver can reach the live service.
        @Volatile
        var instance: MockLocationService? = null
    }
}
