# FakeGPS Companion (Android)

Mock-location provider app. Lets the desktop backend set the device's GPS
without root, using Android's built-in *mock location* developer option.

## Build & install

```bash
cd android-companion
./gradlew installDebug      # requires Android SDK + a connected device
```
(If you don't have a Gradle wrapper yet, run `gradle wrapper` once with a local
Gradle, or open the folder in Android Studio and Run.)

## One-time on-device setup

1. Enable **Developer options** (tap Build number 7×).
2. **Developer options → Select mock location app → FakeGPS Companion**.
3. Launch the app once so the foreground service starts; grant location
   permission if prompted.

After that, the desktop app discovers the device over ADB and streams
coordinates to it. Nothing else to do on the phone.

## How control works

The desktop backend (`backend/devices/android.py`) sends broadcasts via
`adb shell am broadcast`:

- `com.fakegps.companion.SET_LOCATION` with float extras `lat`, `lon`
- `com.fakegps.companion.CLEAR_LOCATION`

`LocationReceiver` forwards these to `MockLocationService`, which calls
`setTestProviderLocation()`.
