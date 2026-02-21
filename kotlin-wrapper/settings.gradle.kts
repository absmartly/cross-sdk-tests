rootProject.name = "kotlin-wrapper"

val sdkPath = if (File("../kotlin-sdk").exists()) {
    "../kotlin-sdk"
} else if (File("../../kotlin-sdk").exists()) {
    "../../kotlin-sdk"
} else {
    error("Cannot find kotlin-sdk directory")
}

includeBuild(sdkPath) {
    dependencySubstitution {
        substitute(module("com.absmartly:absmartly-sdk-kotlin")).using(project(":"))
    }
}
