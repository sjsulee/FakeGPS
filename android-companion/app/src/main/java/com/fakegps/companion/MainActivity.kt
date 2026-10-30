package com.fakegps.companion

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient

/** Standalone UI: a map on the phone itself to teleport / restore location. */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private val main = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startForegroundService(Intent(this, MockLocationService::class.java))
        requestPermissions(arrayOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            "android.permission.POST_NOTIFICATIONS"), 1)
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = WebViewClient()
        web.addJavascriptInterface(Bridge(), "Native")
        web.loadUrl("file:///android_asset/map.html")
        setContentView(web)
    }

    inner class Bridge {
        @JavascriptInterface
        fun teleport(lat: Double, lon: Double): String {
            val svc = MockLocationService.instance
            if (svc == null) {
                startForegroundService(Intent(this@MainActivity, MockLocationService::class.java)
                    .putExtra("lat", lat).putExtra("lon", lon))
                return "starting"
            }
            main.post { svc.apply(lat, lon) }
            return if (svc.isMockAllowed()) "ok" else "nomock"
        }

        @JavascriptInterface
        fun restore(): String {
            main.post { MockLocationService.instance?.release() }
            return "ok"
        }

        @JavascriptInterface
        fun current(): String {
            val s = MockLocationService.instance ?: return ""
            val lat = s.currentLat() ?: return ""
            val lon = s.currentLon() ?: return ""
            return "$lat,$lon"
        }
    }
}
