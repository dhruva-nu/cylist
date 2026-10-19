import com.android.build.api.variant.BuildConfigField
import groovy.json.JsonSlurper

/*
 * The version comes from the release tag (CYLIST-108): the release workflow
 * passes the tag as the Gradle property `cylist.version`, or the environment
 * variable CYLIST_VERSION, and ReleaseVersion (buildSrc) turns `v0.3.2` into
 * versionName 0.3.2 and versionCode 3002. Any other build is 0.0.0, code 1.
 */
val releaseVersion: ReleaseVersion =
    (providers.gradleProperty("cylist.version").orNull ?: providers.environmentVariable("CYLIST_VERSION").orNull)
        ?.let(ReleaseVersion::parse)
        ?: ReleaseVersion.UNRELEASED

/*
 * The release signing key, from the environment only, so its passwords never
 * appear on a command line. All four are set, or none: with none, the release
 * APK comes out unsigned (app-release-unsigned.apk), which is what CI and a
 * plain checkout build. The keystore lives in the repository's Actions secrets
 * and one offline backup, nowhere else; DEVELOPMENT.md says which secrets.
 */
val releaseSigningEnv = listOf(
    "CYLIST_RELEASE_KEYSTORE",
    "CYLIST_RELEASE_KEYSTORE_PASSWORD",
    "CYLIST_RELEASE_KEY_ALIAS",
    "CYLIST_RELEASE_KEY_PASSWORD",
).associateWith { providers.environmentVariable(it).orNull?.takeIf(String::isNotEmpty) }
val releaseSigning: Map<String, String>? = when {
    releaseSigningEnv.values.all { it == null } -> null
    releaseSigningEnv.values.any { it == null } -> throw GradleException(
        "Release signing needs all of ${releaseSigningEnv.keys.joinToString()}; " +
            "missing ${releaseSigningEnv.filterValues { it == null }.keys.joinToString()}",
    )
    else -> releaseSigningEnv.mapValues { it.value!! }
}

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    // The stored session is written as JSON before it is sealed (session/SessionStore.kt).
    alias(libs.plugins.kotlin.serialization)
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
        versionCode = releaseVersion.code
        versionName = releaseVersion.name
    }

    signingConfigs {
        if (releaseSigning != null) {
            create("release") {
                storeFile = file(releaseSigning.getValue("CYLIST_RELEASE_KEYSTORE"))
                storePassword = releaseSigning.getValue("CYLIST_RELEASE_KEYSTORE_PASSWORD")
                keyAlias = releaseSigning.getValue("CYLIST_RELEASE_KEY_ALIAS")
                keyPassword = releaseSigning.getValue("CYLIST_RELEASE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // So a debug build and a future release build can be installed side by side.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            // The hosts the server picker lets a debug build reach over plain
            // HTTP. Keep it equal to src/debug/res/xml/network_security_config.xml,
            // which is what Android itself enforces.
            buildConfigField("String", "CLEARTEXT_HOSTS", "\"10.0.2.2,localhost\"")
        }
        release {
            buildConfigField("String", "CLEARTEXT_HOSTS", "\"\"")
            if (releaseSigning != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
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

/*
 * Firebase, for agent pushes (CYLIST-107), configured without the
 * google-services plugin. That plugin fails the build when the config file is
 * missing, and again when it has no client for a variant's application id,
 * and both are normal here: CI and most checkouts have no file at all, and a
 * project's file may register `in.cylist.app` before `in.cylist.app.debug`.
 *
 * So the file is read here instead. For each variant, the client whose
 * package name is that variant's application id becomes four BuildConfig
 * fields, and cylist.app.push.Firebase initialises Firebase from them at
 * startup. No file, or no client for the variant, leaves them empty, and push
 * is simply off in that build.
 *
 * The file is never committed (android/.gitignore). It is taken from, in order:
 * the Gradle property `cylist.googleServices`, the environment variable
 * CYLIST_GOOGLE_SERVICES_JSON, then app/google-services.json.
 */
val googleServicesPath: String =
    providers.gradleProperty("cylist.googleServices").orNull
        ?: providers.environmentVariable("CYLIST_GOOGLE_SERVICES_JSON").orNull
        ?: layout.projectDirectory.file("google-services.json").asFile.path
val googleServicesJson: String? = providers.fileContents(layout.projectDirectory.file(googleServicesPath)).asText.orNull

/** The Firebase options for one application id, or empty strings when there are none. */
fun firebaseFieldsFor(applicationId: String): Map<String, String> {
    val empty = mapOf("FIREBASE_APP_ID" to "", "FIREBASE_API_KEY" to "", "FIREBASE_PROJECT_ID" to "", "FIREBASE_SENDER_ID" to "")
    val root = googleServicesJson?.let { JsonSlurper().parseText(it) as? Map<*, *> } ?: return empty
    val project = root["project_info"] as? Map<*, *> ?: return empty
    val client = (root["client"] as? List<*>).orEmpty().filterIsInstance<Map<*, *>>().firstOrNull { client ->
        val info = client["client_info"] as? Map<*, *>
        (info?.get("android_client_info") as? Map<*, *>)?.get("package_name") == applicationId
    } ?: return empty
    val apiKey = (client["api_key"] as? List<*>).orEmpty().filterIsInstance<Map<*, *>>().firstOrNull()?.get("current_key")
    return mapOf(
        "FIREBASE_APP_ID" to ((client["client_info"] as Map<*, *>)["mobilesdk_app_id"] as? String).orEmpty(),
        "FIREBASE_API_KEY" to (apiKey as? String).orEmpty(),
        "FIREBASE_PROJECT_ID" to (project["project_id"] as? String).orEmpty(),
        "FIREBASE_SENDER_ID" to (project["project_number"] as? String).orEmpty(),
    )
}

androidComponents {
    onVariants { variant ->
        firebaseFieldsFor(variant.applicationId.get()).forEach { (name, value) ->
            variant.buildConfigFields?.put(name, BuildConfigField("String", "\"$value\"", "From google-services.json, if any"))
        }
    }
}

dependencies {
    implementation(project(":api"))
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.kotlinx.coroutines.android)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    debugImplementation(libs.compose.ui.tooling)
    // Docs (CYLIST-106): parsed as the web's react-markdown + remark-gfm parses them.
    implementation(libs.commonmark)
    implementation(libs.commonmark.ext.gfm.tables)
    implementation(libs.commonmark.ext.gfm.strikethrough)
    implementation(libs.commonmark.ext.task.list.items)
    implementation(libs.commonmark.ext.autolink)
    implementation(libs.commonmark.ext.footnotes)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
}
