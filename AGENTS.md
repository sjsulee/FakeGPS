# Repository Guidelines

## Project Structure & Module Organization
- `backend/app.py`: FastAPI REST/WebSocket endpoints and single-device session management.
- `backend/routing.py`: distance, interpolation, route playback, and GPX parsing.
- `backend/devices/`: shared synchronous `LocationDriver` interface and iOS, Android, and demo implementations.
- `frontend/`: static HTML, CSS, and vanilla JavaScript using Leaflet; no frontend build step.
- `run.py`: starts Uvicorn and the pywebview desktop window.
- `android-companion/`: Kotlin mock-location app with a Gradle wrapper.
- `docs/`: documentation assets. See `README.md` for device setup and `CLAUDE.md` for architecture pitfalls. No automated test suite is checked in.

## Build, Test, and Development Commands
Run from the repository root unless specified. Use Python 3.10+; Android builds require JDK 17+ and an Android SDK.

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
FAKEGPS_DEMO=1 uvicorn backend.app:app --port 8765 --reload
```

The demo server runs at `http://127.0.0.1:8765` without hardware. Use `FAKEGPS_DEMO=1 python3 run.py` for the desktop window; omit the environment variable for real devices. Refresh the browser after frontend changes.

```bash
python3 -m py_compile backend/*.py backend/devices/*.py run.py
node --check frontend/app.js
cd android-companion
./gradlew :app:assembleDebug
./gradlew installDebug
```

These check syntax, build the debug APK, and install it on a connected Android device.

## Coding Style & Naming Conventions
Use four-space indentation for Python/Kotlin and two spaces for JavaScript. Follow Python `snake_case`, JS/Kotlin `camelCase`, and class `PascalCase`. Preserve Python type annotations, future annotations, and `fakegps.<area>` loggers. Route frontend messages through `setStatus()`. No formatter or lint configuration is checked in; follow surrounding code.

## Testing Guidelines
No test framework or coverage threshold is configured. Smoke-test device selection, teleport, joystick, route playback, GPX import, stop, and clear in demo mode. Verify route streaming against a running Uvicorn server. For driver changes, also validate on the affected platform; rebuild and reinstall changed companion code.

## Commit & Pull Request Guidelines
History uses short imperative subjects, such as `Add MIT License`; follow that style. PRs should describe behavior changes, link relevant issues, list validation and device details, and include screenshots for UI changes.

## Architecture Constraints
Preserve idempotent device selection, persistent driver sessions, the iOS dedicated event-loop thread, and Android’s location heartbeat. Keep `pymobiledevice3>=9.17.0`.
