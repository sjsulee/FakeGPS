package com.fakegps.companion

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Minimal UI: explains setup and starts the foreground service. The actual
 * control happens from the desktop app over ADB.
 */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startForegroundService(Intent(this, MockLocationService::class.java))

        val tv = TextView(this).apply {
            setPadding(40, 80, 40, 40)
            textSize = 16f
            text = buildString {
                appendLine("FakeGPS Companion is running.")
                appendLine()
                appendLine("One-time setup:")
                appendLine("1. Enable Developer options.")
                appendLine("2. Developer options > Select mock location app")
                appendLine("   → choose \"FakeGPS Companion\".")
                appendLine("3. Grant location permission if asked.")
                appendLine()
                appendLine("Then control location from the desktop app.")
            }
        }
        setContentView(LinearLayout(this).apply { addView(tv) })
    }
}
