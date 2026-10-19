plugins {
    // Kotlin on the JVM at the version Gradle itself embeds. Nothing here is a
    // Gradle plugin, so `kotlin-dsl` and its plugin descriptors are not needed.
    `embedded-kotlin`
}

dependencies {
    testImplementation(libs.junit)
}
