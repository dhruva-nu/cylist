// buildSrc is the Android build's own code: what turns a release tag into the
// app's version (ReleaseVersion.kt). It shares the app's version catalog.
dependencyResolutionManagement {
    repositories {
        mavenCentral()
        gradlePluginPortal()
    }
    versionCatalogs {
        create("libs") {
            from(files("../gradle/libs.versions.toml"))
        }
    }
}

rootProject.name = "buildSrc"
