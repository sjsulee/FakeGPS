# CLAUDE.md

給在此專案工作的 Claude / 開發者的工作指引。詳細使用說明見 `README.md`，
本檔聚焦**架構、關鍵陷阱、慣例與常用指令**。

## 專案概觀

FakeGPS Studio：在**未越獄 iOS（17+，含 iOS 26）**與**未 root Android** 上
模擬 GPS 定位的桌面工具，地圖 UI 操作。功能對標 iAnyGo（teleport、joystick、
兩點／多點 route、GPX、可調速度）。**不修改手機系統**，只驅動各平台官方的
開發者定位機制。

合法用途定位：等同 Xcode *Simulate Location* 與 Android *mock location*，
供 App 測試／除錯使用。

## 架構

```
frontend/  Leaflet 地圖 UI（靜態檔，免建置）── HTTP + WebSocket ──┐
                                                                  ▼
backend/app.py   FastAPI 控制層：單一 Session（同時只驅動一台裝置）
   ├─ devices/ios.py      pymobiledevice3 驅動（iOS 17+ 為主）
   ├─ devices/android.py  adb + companion App 驅動
   ├─ devices/demo.py     FAKEGPS_DEMO=1 用的假裝置
   └─ routing.py          大圓內插、速度播放、GPX（純函式，已測）
run.py                    起 uvicorn + pywebview 桌面視窗
android-companion/        Kotlin 模擬定位 App（Gradle，已可建置）
```

- `LocationDriver`（`backend/devices/__init__.py`）是共同介面：`set_location()`
  / `clear()` / `close()`，**同步**。各平台實作隱藏自己的連線細節。
- `app.py` 的 `Session` 持有唯一 active driver，並跑背景 asyncio task 做
  route／joystick 播放；對 driver 的呼叫用 `asyncio.to_thread` 包起來。

## 關鍵陷阱（踩過的雷，務必記住）

1. **pymobiledevice3 必須 ≥ 9.x。**
   - iOS 26 / iOS 17+ 只有 9.x 支援。4.x 會 `ModuleNotFoundError:
     ipsw_parser.img4` 且不支援新機。`requirements.txt` 已釘 `>=9.17.0`，
     **不要降版**。
2. **9.x 的相關 API 幾乎全是 `async`。**
   - `usbmux.list_devices`、`lockdown.create_using_usbmux`、`get_value`、
     `mobile_image_mounter.auto_mount`、`tunneld.api.get_tunneld_device_by_udid`、
     `DvtProvider`、`LocationSimulation.set/clear` 全是協程。
   - 因此 `IOSLocationDriver` **內部跑一條專屬事件迴圈執行緒**，用
     `run_coroutine_threadsafe` 把協程丟上去，對外維持同步介面。新增 iOS
     功能時沿用 `self._call(coro)`，不要直接在主執行緒 await。
   - 模組級 `ios.list_devices()` 用 `asyncio.run(_alist_devices())` 包裝。
3. **iOS 17+ 需要 tunneld，且需 sudo。**
   - 使用者要另開終端機跑 `sudo python3 -m pymobiledevice3 remote tunneld`
     並保持執行。工具／自動化**無法代跑 sudo**（會被權限系統擋）。
   - driver 會從 `get_tunneld_device_by_udid` 取得已連線的 RSD；若 tunneld
     沒跑會回傳 None，要給可操作的錯誤訊息。
4. **同一時間只能有一個定位模擬 session。**
   - 反覆 `select` 拆／建 iOS DVT session 會卡住。`Session.select` 已做
     **冪等**（同 udid 為 no-op），不要破壞這個性質。
5. **`open_driver` 依平台路由。**
   - 一定要先用 `enumerate_devices()` 查出該 udid 的平台，再用對應後端。
     早期「都試一遍」會把 iOS 真實錯誤蓋成 Android「not found」。
6. **usbmux 會把同一台手機依 USB／Network 各報一次** → `ios.list_devices`
   以 udid 去重。
7. **前端 `api()` 的 method**：無 body 預設 GET。POST 但無參數的端點
   （`/api/stop`、`/api/clear`）必須明確傳 `"POST"`，否則 405。
8. **前端是靜態檔**：改 `frontend/` 重整瀏覽器即可；改 `backend/` 要重啟
   伺服器（或 `--reload`）。
9. **TestClient 限制**：背景 asyncio task（route 播放）在 `TestClient` 請求
   之間不會推進；要驗證 route 串流請用真正的 `uvicorn`。
10. **Android mock 會「過期」→ 飄回原點。** `setTestProviderLocation` 單筆 fix
    過幾秒就被 fused provider 丟掉，退回真實 GPS。`MockLocationService` 內建
    **心跳**（`HEARTBEAT_MS=1000`）：每秒用最新時間戳重送 `lastLat/lastLon`，
    在手機端自行維持，桌面端 idle（teleport 後不再送）也不會飄。改 driver 時
    別把這個拿掉。驗證：`dumpsys location | grep fused` 看 `et=` 是否持續遞增
    而座標不變。
11. **改 companion 要 rebuild+reinstall。** Kotlin 改完 `./gradlew :app:assembleDebug`
    再 `adb install -r -g ...`，並 `am force-stop` + 重開 App 讓新 service 生效。

## 常用指令

```bash
# 啟動（真實裝置）— 需先在另一終端機跑 tunneld（見上）
cd "iOS Fake GPS" && source backend/.venv/bin/activate
uvicorn backend.app:app --port 8765           # 瀏覽器: http://127.0.0.1:8765
python3 run.py                                 # 原生桌面視窗

# Demo 模式（免硬體）
FAKEGPS_DEMO=1 uvicorn backend.app:app --port 8765

# 停止 / log
pkill -f "uvicorn backend.app:app"
tail -f /tmp/fakegps.log

# 純函式 / 語法檢查
python3 -m py_compile backend/*.py backend/devices/*.py run.py
node --check frontend/app.js

# 列 iOS 裝置（確認 pymobiledevice3 連得到）
python3 -m pymobiledevice3 usbmux list

# Android companion
cd android-companion
JAVA_HOME=$(/usr/libexec/java_home) ./gradlew installDebug   # 裝到手機
JAVA_HOME=$(/usr/libexec/java_home) ./gradlew :app:assembleDebug
```

## 慣例

- Python：型別註記、`from __future__ import annotations`、模組級 logger
  `logging.getLogger("fakegps.<area>")`。錯誤訊息要**可操作**（告訴使用者下一步）。
- 前端：原生 JS（無框架）、Leaflet。狀態訊息走 `setStatus()`；錯誤紅字。
- 回覆使用者：**繁體中文**（使用者偏好）。
- 不要把 `set_location` 寫成每次 spawn 新程序／新連線；要用持續 session 串流，
  joystick／route 才會順。

## 已驗證狀態

- iOS 實機（iPhone 17,1 / iOS 26.5）：teleport、joystick、route 串流、
  完整 API 流程實機通過。
- Android 實機（SM-S9010 / Android 16）：teleport、route 串流、clear 還原
  實機通過。mock 同時傳播到 `gps` 與 `fused` provider（fused 是多數 App 讀的）。
  授權捷徑：`adb shell appops set com.fakegps.companion android:mock_location allow`
  即可免手動點開發者選項。驗證落地用 `dumpsys location | grep "fused"`（帶 `mock` 標記）。
- `routing.py`：距離／方位／內插／GPX 已測。

## 待辦 / 後續

- route 播放在真實 uvicorn 下的長路線壓測。
- 可考慮：GPX 匯出、書籤位置、多裝置切換。
