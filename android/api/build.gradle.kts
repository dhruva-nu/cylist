import org.openapitools.generator.gradle.plugin.tasks.GenerateTask

plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.openapi.generator)
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

// The client is generated from openapi.json on every build and never
// committed: the snapshot is the source, and `make openapi` refreshes it from
// the backend. Nothing under build/generated is edited by hand.
val generatedClient = layout.buildDirectory.dir("generated/openapi")

val generateClient = tasks.named<GenerateTask>("openApiGenerate") {
    generatorName = "kotlin"
    inputSpec = layout.projectDirectory.file("openapi.json").asFile.path
    outputDir = generatedClient.get().asFile.path
    packageName = "cylist.api.generated"
    apiPackage = "cylist.api.generated.apis"
    modelPackage = "cylist.api.generated.models"
    configOptions = mapOf(
        "library" to "jvm-okhttp4",
        "serializationLibrary" to "kotlinx_serialization",
        "dateLibrary" to "string",
        "enumUnknownDefaultCase" to "true",
        "omitGradleWrapper" to "true",
        "sourceFolder" to "src/main/kotlin",
    )
    // Free-form JSON (a payload, a dict[str, Any]) has no Kotlin type that
    // kotlinx.serialization can write, so it stays JSON.
    typeMappings = mapOf("AnyType" to "JsonElement", "object" to "JsonObject")
    importMappings = mapOf(
        "JsonElement" to "kotlinx.serialization.json.JsonElement",
        "JsonObject" to "kotlinx.serialization.json.JsonObject",
    )
    // Only the sources: the generator's own Gradle files, README and docs
    // would be a second build nobody runs.
    globalProperties = mapOf("apis" to "", "models" to "", "supportingFiles" to "")
    generateApiTests = false
    generateModelTests = false
    generateApiDocumentation = false
    generateModelDocumentation = false
}

sourceSets {
    main {
        kotlin.srcDir(generateClient.map { "${it.outputDir.get()}/src/main/kotlin" })
    }
}

lint {
    warningsAsErrors = true
    disable += setOf("GradleDependency", "NewerVersionAvailable")
}

dependencies {
    api(libs.okhttp)
    api(libs.kotlinx.serialization.json)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
}
