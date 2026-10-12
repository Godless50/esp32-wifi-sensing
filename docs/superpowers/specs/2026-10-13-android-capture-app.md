# Нативное Android-приложение съёмки (WSN Capture) — спецификация и план

Дата: 2026-10-13. Статус: объём согласован пользователем («делай»).

## Почему
На Honor 90 (REA-NX9, Android 15, SM7450/Adreno 644, Chrome 156, ARCore 1.56) WebXR в Chrome не получает кадры:
драйвер камеры отклоняет конфигурацию, которую Chrome выбирает в ARCore (`ImageReader-1920x1080 YUV`,
`CamX configure_streams() max_buffers: 0`, `CameraCaptureSession CONFIGURE_FAILED`, `Last visual features at: 0 ns`).
Подтверждено logcat для 8 вариантов функций WebXR и для эталонных примеров immersive-web. Нативный ARCore
(приложение Google, «В вашем пространстве») на том же телефоне работает. Страница не может выбрать конфигурацию
камеры — нативное приложение может.

## Объём (что делает приложение)
- ARCore-сессия с **явно выбранной конфигурацией камеры**: задняя камера, 30 FPS, минимальное разрешение
  CPU-изображения из поддерживаемых (ожидается 640×480), глубина выключена, плоскости — горизонтальные,
  `UpdateMode.LATEST_CAMERA_IMAGE`. Выбранная конфигурация показывается в статусе.
- Камера на экране (фон через OES-текстуру), прицел в центре, hit-test по горизонтальным плоскостям в центре экрана.
- Те же кнопки, что у `/capture`: Запись / Пусто (5/10/20/30 мин) / Стоп, Я у платы, Я у роутера,
  Новая комната (имя), Угол, Закрыть комнату, Предмет (тип), позы Стою/Сижу/Лежу/Иду.
- Статус: связь, синхронизация часов, запись и длительность, CSI Гц, очередь неподтверждённых, трекинг ARCore.
- Протокол — существующий `/ws/capture` (спецификация фазы 2, Task 3), сервер не меняется:
  `hello`, `ping`/`pong`/`sync`, `poses` (30 Гц, пачки раз в 200 мс), `event` с seq; повтор неподтверждённого
  после переподключения; продолжение нумерации по `ack` сервера.
- Координаты: мировая система ARCore (y вверх, правая тройка) совпадает с WebXR `local` на Android, поэтому
  позы и точки отправляются как есть (`p=[x,y,z]`, `q=[x,y,z,w]`). Позы шлются только при `TrackingState.TRACKING`.
- Время телефона: `SystemClock.elapsedRealtimeNanos()/1e6` (мс) — и для ping, и для поз.
- Сеть: `wss://192.168.4.77:8443/ws/capture`, basic auth `wsn` + пароль (вводится один раз, хранится в
  SharedPreferences). TLS: доверие только корневому CA Caddy, вшитому в приложение (`res/raw/wsn_ca.pem`,
  `network_security_config`), системные CA — для остального.
- Установка: `adb install` по Wi-Fi (сопряжение уже есть). Подпись — debug-ключ.

**Вне объёма:** пульт платы, iOS, публикация в магазине, дообучение — позже по запросу.

## Структура
```
android/settings.gradle.kts, build.gradle.kts, gradle.properties
android/app/build.gradle.kts
android/app/src/main/AndroidManifest.xml
android/app/src/main/java/ru/wsn/capture/
  CaptureClient.kt      протокол: очередь поз/событий, seq/ack, sync — без Android API (unit-тесты на JVM)
  Transport.kt          OkHttp WebSocket + reconnect + basic auth + CA pinning
  CameraConfigPicker.kt выбор конфигурации камеры (чистая функция над списком (w,h,fps) — unit-тест)
  BackgroundRenderer.kt OES-фон камеры
  MainActivity.kt       ARCore, GLSurfaceView, кнопки, статус
android/app/src/main/res/layout/activity_main.xml, xml/network_security_config.xml, raw/wsn_ca.pem
android/app/src/test/java/ru/wsn/capture/CaptureClientTest.kt, CameraConfigPickerTest.kt
tools/android_build.sh   сборка в Docker (wsn-android-build), копирует CA из тома caddy, ставит APK по adb
```

## Зависимости
AGP 8.7.2, Kotlin 2.0.21, compileSdk/targetSdk 35, minSdk 26, `com.google.ar:core:1.45.0`,
`com.squareup.okhttp3:okhttp:4.12.0`, AndroidX AppCompat 1.7.0; тесты: JUnit 4.13.2, `org.json:json:20240303`.

## План
1. **Сборочное окружение** — `deploy/Dockerfile.android`, `tools/android_build.sh`, пустой проект собирается (`assembleDebug`).
2. **CaptureClient (TDD)** — тесты: hello→ack восстанавливает seq; позы до записи не копятся; пачка ≤ 300;
   ack удаляет подтверждённое; события досылаются после переподключения; pong→sync с c1; `not_owner`/`error` в лог.
3. **CameraConfigPicker (TDD)** — из списка выбирает заднюю, 30 FPS, минимальную CPU-площадь; без 30 FPS — любую с минимальной площадью.
4. **Transport + MainActivity + рендер** — сборка, установка на Honor, проверка: камера видна, трекинг TRACKING,
   в logcat нет `CONFIGURE_FAILED`, сервер видит `hello`/`sync`.
5. **Сквозная проверка с пользователем** — короткая сессия: старт у платы, роутер, комната из 4 углов, стоп;
   `/api/plan` содержит комнату, `skipped` пуст.
