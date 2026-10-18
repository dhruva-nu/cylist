plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    // `in.cylist` cannot be a Kotlin or Java package, because `in` is a keyword
    // in both. The application id is just a string, so it can carry the domain;
    // the code lives under `cylist.app`.
    namespace = "cylist.app"

    // Live Updates (Notification.ProgressStyle and promoted ongoing
    // notifications) arrived in Android 16, API 36, so targeting 36 is what
    // makes them reachable; minSdk stays lower and the app checks for 36 at
    // runtime before posting one. compileSdk is a step ahead only because
    // current AndroidX will not build against less than 37. It changes what the
    // compiler can see, not how the app behaves; targetSdk does that, and
    // moving it to 37 is a decision of its own.
    compileSdk = 37

    defaultConfig {
        applicationId = "in.cylist.app"
        // 26 for variable fonts: Figtree is shipped as a single variable font.
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        debug {
            // So a debug build and a future release build can be installed side by side.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }

    lint {
        warningsAsErrors = true
        // A newer library, plugin or SDK being out is not a defect in this
        // change, and making it one would fail CI on a day nobody touched
        // android/. Bumps happen in gradle/libs.versions.toml, on purpose.
        disable += setOf("GradleDependency", "NewerVersionAvailable", "AndroidGradlePluginVersion", "OldTargetApi")
    }

    testOptions {
        unitTests.all {
            // ThemeTokensTest checks the Compose theme against the web app's
            // tokens.css, so the two cannot drift apart.
            it.systemProperty(
                "cylist.webTokens",
                rootProject.file("../frontend/src/theme/tokens.css").absolutePath,
            )
        }
    }
}

dependencies {
    implementation(project(":api"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    debugImplementation(libs.compose.ui.tooling)

    testImplementation(libs.junit)
}
