plugins {
    alias(libs.plugins.kotlin.jvm)
    // Android lint on a plain Kotlin module, so `./gradlew lint` covers it too.
    alias(libs.plugins.android.lint)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

lint {
    warningsAsErrors = true
    disable += setOf("GradleDependency", "NewerVersionAvailable")
}

dependencies {
    testImplementation(libs.junit)
}
