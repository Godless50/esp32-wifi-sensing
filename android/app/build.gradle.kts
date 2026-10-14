plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Подпись релиза: ключ хранится вне репозитория (tools/android_build.sh release создаёт его один раз в
// ~/.config/wsn/release.jks), путь и пароль — через переменные окружения. Без них — отладочный ключ.
val releaseKeystore: String? = System.getenv("WSN_KEYSTORE")

android {
    namespace = "ru.wsn.capture"
    compileSdk = 35
    buildToolsVersion = "35.0.0"  // установлен в образе wsn-android-build; каталог SDK только для чтения
    defaultConfig {
        applicationId = "ru.wsn.capture"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "1.0"
    }
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = System.getenv("WSN_KEYSTORE_PASS")
                keyAlias = "wsn"
                keyPassword = System.getenv("WSN_KEYSTORE_PASS")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (releaseKeystore != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("com.google.ar:core:1.45.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")  // сканер QR-кода сопряжения
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("com.fazecast:jSerialComm:2.11.0")  // проверка прошивальщика на настоящей плате (WSN_HW_PORT)
}
