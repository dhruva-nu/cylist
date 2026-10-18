pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode = RepositoriesMode.FAIL_ON_PROJECT_REPOS
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "cylist"

// `app` is the phone app. `api` is the client for the backend's HTTP API: plain
// Kotlin on the JVM, with no Android in it, so its tests run without an
// emulator or Robolectric.
include(":app", ":api")
