plugins {
    kotlin("jvm") version "1.9.22"
    id("com.github.johnrengelman.shadow") version "7.1.2"
    application
}

group = "com.absmartly.wrapper"
version = "1.0.0"

application {
    mainClass.set("com.absmartly.wrapper.ApplicationKt")
}

repositories {
    mavenLocal()
    mavenCentral()
}

dependencies {
    implementation("com.absmartly:absmartly-sdk-kotlin")

    implementation("io.ktor:ktor-server-core:2.3.7")
    implementation("io.ktor:ktor-server-netty:2.3.7")
    implementation("io.ktor:ktor-server-content-negotiation:2.3.7")
    implementation("io.ktor:ktor-serialization-jackson:2.3.7")

    implementation("io.ktor:ktor-client-core:2.3.7")
    implementation("io.ktor:ktor-client-cio:2.3.7")
    implementation("io.ktor:ktor-client-content-negotiation:2.3.7")

    implementation("com.fasterxml.jackson.module:jackson-module-kotlin:2.16.1")

    implementation("ch.qos.logback:logback-classic:1.4.14")
}

java {
    sourceCompatibility = JavaVersion.VERSION_11
    targetCompatibility = JavaVersion.VERSION_11
}

tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile> {
    kotlinOptions {
        jvmTarget = "11"
    }
}
