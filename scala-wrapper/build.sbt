name := "absmartly-scala-wrapper"

version := "0.1.0"

scalaVersion := "2.13.12"

// Wrapper-specific dependencies
libraryDependencies ++= Seq(
  // HTTP server
  "org.http4s" %% "http4s-blaze-server" % "0.23.15",
  "org.http4s" %% "http4s-circe" % "0.23.15",
  "org.http4s" %% "http4s-dsl" % "0.23.15",

  // JSON
  "io.circe" %% "circe-core" % "0.14.6",
  "io.circe" %% "circe-generic" % "0.14.6",
  "io.circe" %% "circe-parser" % "0.14.6",

  // Logging
  "ch.qos.logback" % "logback-classic" % "1.4.11"
)

// Reference the SDK (path is relative to Docker build context)
lazy val sdk = ProjectRef(file("../scala-sdk"), "scala-sdk")

lazy val root = (project in file("."))
  .dependsOn(sdk)

// Assembly settings for fat JAR
assembly / assemblyMergeStrategy := {
  case PathList("META-INF", xs @ _*) => MergeStrategy.discard
  case "application.conf" => MergeStrategy.concat
  case "reference.conf" => MergeStrategy.concat
  case x => MergeStrategy.first
}

assembly / mainClass := Some("com.absmartly.wrapper.Server")
assembly / assemblyJarName := "scala-wrapper.jar"
