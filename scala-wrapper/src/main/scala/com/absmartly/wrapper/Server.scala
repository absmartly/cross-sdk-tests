package com.absmartly.wrapper

import cats.effect._
import cats.implicits._
import org.http4s._
import org.http4s.dsl.io._
import org.http4s.implicits._
import org.http4s.blaze.server.BlazeServerBuilder
import org.http4s.circe._
import io.circe._
import io.circe.syntax._
import io.circe.generic.auto._
import scala.concurrent.ExecutionContext
import scala.collection.concurrent.TrieMap
import java.util.concurrent.atomic.AtomicInteger
import com.absmartly.sdk._

object Server extends IOApp {

  implicit val ec: ExecutionContext = ExecutionContext.global

  private val contexts = TrieMap[String, (Context, WrapperEventCollector)]()
  private val publishFailFlags = TrieMap[String, Boolean]()
  private val payloads = TrieMap[String, ContextData]()
  private val contextCounter = new AtomicInteger(0)

  implicit val jsonEntityDecoder: EntityDecoder[IO, Json] = jsonOf[IO, Json]

  private def translateEndpoint(endpoint: String): String = {
    endpoint
      .replaceAll("http://localhost:\\d+", "http://localhost:3000")
      .replaceAll("http://127\\.0\\.0\\.1:\\d+", "http://localhost:3000")
  }

  private val noOpPublisher: Option[ContextPublisher] = Some(new ContextPublisher {
    def publish(units: Map[String, String], hashed: Boolean, exposures: List[Exposure], goals: List[Goal], attributes: Option[List[Attribute]]): scala.concurrent.Future[Unit] =
      scala.concurrent.Future.successful(())
  })

  private def makeSdkConfig(endpoint: String, collector: WrapperEventCollector): SDKConfig =
    SDKConfig(
      endpoint = endpoint,
      apiKey = "test-key",
      application = "test-app",
      environment = "test",
      eventLogger = collector,
      publisher = noOpPublisher
    )

  private def parseUnitsMap(cursor: io.circe.ACursor): Map[String, String] = {
    cursor.focus.flatMap(_.asObject).map { obj =>
      obj.toMap.flatMap { case (key, value) =>
        val strVal = value.asString
          .orElse(value.asNumber.map(_.toString))
          .orElse(Some(value.noSpaces))
        strVal.map(key -> _)
      }
    }.getOrElse(Map.empty)
  }

  val routes = HttpRoutes.of[IO] {

    case GET -> Root / "health" =>
      Ok(Json.obj(
        "status" -> Json.fromString("healthy"),
        "sdk" -> Json.fromString("scala"),
        "version" -> Json.fromString("0.1.0")
      ))

    case GET -> Root / "capabilities" =>
      Ok(Json.obj("diagnostics" -> Json.fromBoolean(true),
        "publishFail" -> Json.fromBoolean(true),
        "variableKeysMap" -> Json.fromBoolean(true),
        "globalCustomFieldKeys" -> Json.fromBoolean(true),
        "getUnits" -> Json.fromBoolean(true),
        "getAttributes" -> Json.fromBoolean(true),
        "readyError" -> Json.fromBoolean(true)
      ))

    case req @ POST -> Root / "diagnostic" =>
      req.as[Json].flatMap { json =>
        val cursor = json.hcursor
        val op = cursor.downField("operation").as[String].getOrElse("")
        val value = cursor.downField("value").focus.getOrElse(Json.Null)
        val text = value.asString.getOrElse(value.noSpaces)

        val result: Either[String, Json] = op match {
          case "hashUnit" =>
            Right(Json.fromString(Utils.hashUnit(text)))
          case "base64UrlNoPadding" =>
            val encoded = java.util.Base64.getUrlEncoder.withoutPadding.encodeToString(text.getBytes(java.nio.charset.StandardCharsets.UTF_8))
            Right(Json.fromString(encoded))
          case "utf8Bytes" =>
            val bytes = text.getBytes(java.nio.charset.StandardCharsets.UTF_8).map(b => Json.fromInt(b & 0xff))
            Right(Json.arr(bytes: _*))
          case "isObject" =>
            Right(Json.fromBoolean(value.isObject))
          case "isNumeric" =>
            Right(Json.fromBoolean(value.isNumber))
          case "isPromise" =>
            Right(Json.fromBoolean(false))
          case _ =>
            Left(s"Unsupported diagnostic operation: $op")
        }

        result match {
          case Right(v) => Ok(wrapperResponse(v, List.empty))
          case Left(err) => BadRequest(Json.obj("error" -> Json.fromString(err)))
        }
      }

    case req @ PUT -> Root / "context_payload" / payloadId =>
      req.as[Json].flatMap { json =>
        json.hcursor.downField("data").as[ContextData] match {
          case Right(data) =>
            payloads(payloadId) = data
            Ok(Json.obj("success" -> Json.fromBoolean(true)))
          case Left(err) =>
            BadRequest(Json.obj("error" -> Json.fromString(err.getMessage)))
        }
      }

    case GET -> Root / "context_payload" / payloadId / "context" :? _ =>
      payloads.get(payloadId) match {
        case Some(data) => Ok(data.asJson)
        case None => NotFound(Json.obj("error" -> Json.fromString("Payload not found")))
      }

    case req @ POST -> Root / "context_payload" / payloadId / "context" =>
      payloads.get(payloadId) match {
        case Some(data) => Ok(data.asJson)
        case None => NotFound(Json.obj("error" -> Json.fromString("Payload not found")))
      }

    case req @ POST -> Root / "context" =>
      req.as[Json].flatMap { json =>
        val cursor = json.hcursor
        val mode = cursor.downField("mode").as[String].getOrElse("")

        if (mode == "e2e") {
          val e2eEndpoint = Option(System.getenv("ABSMARTLY_E2E_ENDPOINT"))
          val e2eApiKey = Option(System.getenv("ABSMARTLY_E2E_API_KEY"))
          val e2eApplication = Option(System.getenv("ABSMARTLY_E2E_APPLICATION"))
          val e2eEnvironment = Option(System.getenv("ABSMARTLY_E2E_ENVIRONMENT"))

          (e2eEndpoint, e2eApiKey, e2eApplication, e2eEnvironment) match {
            case (Some(endpoint), Some(apiKey), Some(application), Some(environment)) =>
              val units = parseUnitsMap(cursor.downField("units"))
              val attrs = cursor.downField("attributes").as[Map[String, Json]].getOrElse(Map.empty)

              IO.fromFuture(IO {
                val collector = new WrapperEventCollector()
                val sdkConfig = SDKConfig(
                  endpoint = endpoint,
                  apiKey = apiKey,
                  application = application,
                  environment = environment,
                  eventLogger = collector
                )
                val sdk = new SDK(sdkConfig)
                sdk.createContext(units).map { context =>
                  attrs.foreach { case (name, value) => context.setAttribute(name, value) }
                  (context, collector)
                }
              }).flatMap { case (context, collector) =>
                val contextId = s"ctx-${contextCounter.getAndIncrement()}"
                contexts(contextId) = (context, collector)
                Ok(wrapperResponse(
                  Json.obj(
                    "contextId" -> Json.fromString(contextId),
                    "ready" -> Json.fromBoolean(context.isReady()),
                    "failed" -> Json.fromBoolean(context.isFailed()),
                    "finalized" -> Json.fromBoolean(context.isFinalized())
                  ),
                  collector.getEvents()
                ))
              }.handleErrorWith { err =>
                InternalServerError(Json.obj("error" -> Json.fromString(err.getMessage)))
              }

            case _ =>
              NotImplemented(Json.obj("error" -> Json.fromString("e2e mode not configured")))
          }
        } else {

        val units = parseUnitsMap(cursor.downField("units"))

        val options = ContextOptions(
          units = units,
          attributes = cursor.downField("options").downField("attributes").as[Map[String, Json]].getOrElse(Map.empty),
          overrides = cursor.downField("options").downField("overrides").as[Map[String, Int]].getOrElse(Map.empty),
          cassignments = cursor.downField("options").downField("cassignments").as[Map[String, Int]].getOrElse(Map.empty)
        )

        val createContext: IO[(Context, WrapperEventCollector)] = cursor.downField("data").as[ContextData] match {
          case Right(data) =>
            IO {
              val collector = new WrapperEventCollector()
              val sdk = new SDK(makeSdkConfig("http://localhost:3000", collector))
              val context = sdk.createContextWith(units, data, options)
              (context, collector)
            }

          case Left(_) =>
            cursor.downField("endpoint").as[String] match {
              case Right(endpoint) =>
                val payloadThrottle = cursor.downField("options").downField("payloadThrottle").as[Int].getOrElse(0)
                val translatedEndpoint = translateEndpoint(endpoint)

                if (payloadThrottle > 0) {
                  IO {
                    val collector = new WrapperEventCollector()
                    val sdk = new SDK(makeSdkConfig(translatedEndpoint, collector))
                    val context = new Context(sdk, None, units, options, collector)
                    scala.concurrent.Future {
                      try {
                        Thread.sleep(payloadThrottle)
                        val data = sdk.fetchContextData()
                        context.setData(data)
                      } catch {
                        case e: Exception =>
                          context.setDataFailed(Some(new RuntimeException("Context load failed")))
                      }
                    }
                    (context, collector)
                  }
                } else {
                  IO.fromFuture(IO {
                    val collector = new WrapperEventCollector()
                    val sdk = new SDK(makeSdkConfig(translatedEndpoint, collector))
                    sdk.createContext(units, options).map { context =>
                      (context, collector)
                    }
                  })
                }
              case Left(_) =>
                val failLoad = cursor.downField("failLoad").as[Boolean].getOrElse(false)
                IO {
                  val collector = new WrapperEventCollector()
                  val emptyData = ContextData(experiments = List.empty)
                  val sdk = new SDK(makeSdkConfig("http://localhost:3000", collector))
                  if (failLoad) {
                    val context = new Context(sdk, None, units, options, collector)
                    context.setDataFailed(Some(new RuntimeException("Context load failed")))
                    collector.logEvent("error", Json.obj("message" -> Json.fromString("Context load failed")))
                    Thread.sleep(50)
                    (context, collector)
                  } else {
                    val context = sdk.createContextWith(units, emptyData, options)
                    (context, collector)
                  }
                }
            }
        }

        createContext.flatMap { case (context, collector) =>
          val contextId = s"ctx-${contextCounter.getAndIncrement()}"
          contexts(contextId) = (context, collector)

          Ok(wrapperResponse(
            Json.obj(
              "contextId" -> Json.fromString(contextId),
              "ready" -> Json.fromBoolean(context.isReady()),
              "failed" -> Json.fromBoolean(context.isFailed()),
              "finalized" -> Json.fromBoolean(context.isFinalized())
            ),
            collector.getEvents()
          ))
        }.handleErrorWith { err =>
          InternalServerError(Json.obj("error" -> Json.fromString(err.getMessage)))
        }
        }
      }

    case DELETE -> Root / "context" / contextId =>
      contexts.remove(contextId)
      publishFailFlags.remove(contextId)
      Ok(Json.obj("result" -> Json.fromString("deleted")))

    case req @ POST -> Root / "context" / contextId / "setUnit" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          val unitTypeOpt = cursor.downField("unitType").as[String].toOption
          val uidOpt = cursor.downField("uid").focus.map { v =>
            v.asString.getOrElse(v.asNumber.map(_.toString).getOrElse(v.noSpaces))
          }
          (unitTypeOpt, uidOpt) match {
            case (Some(unitType), Some(uid)) =>
              try {
                context.setUnit(unitType, uid)
                Ok(wrapperResponse(Json.Null, List.empty))
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing unitType or uid")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "getUnit" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("unitType").as[String] match {
            case Right(unitType) =>
              val unit = context.getUnit(unitType)
              val result = unit.map { v =>
                v.toIntOption.map(Json.fromInt)
                  .orElse(v.toLongOption.map(Json.fromLong))
                  .orElse(v.toDoubleOption.map(Json.fromDoubleOrNull))
                  .getOrElse(Json.fromString(v))
              }.getOrElse(Json.Null)
              Ok(wrapperResponse(result, List.empty))
            case Left(_) =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing unitType")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "attribute" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("name").as[String], cursor.downField("value").focus) match {
            case (Right(name), Some(value)) =>
              try {
                context.setAttribute(name, value)
                Ok(wrapperResponse(Json.Null, List.empty))
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing name or value")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "getAttribute" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("name").as[String] match {
            case Right(name) =>
              val attr = context.getAttribute(name)
              Ok(wrapperResponse(attr.getOrElse(Json.Null), List.empty))
            case Left(_) =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing name")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "treatment" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("experimentName").as[String] match {
            case Right(experimentName) =>
              try {
                // The scala SDK's treatment() returns 0 on a finalized context
                // (Context.scala:196) instead of failing, so enforce the spec's
                // "finalized => error" here before delegating (scenario 189).
                if (context.isFinalized()) {
                  BadRequest(Json.obj("error" -> Json.fromString("Context finalized")))
                } else {
                  val prevCount = collector.getEvents().length
                  val variant = context.treatment(experimentName)
                  val newEvents = collector.getEventsSince(prevCount)
                  Ok(wrapperResponse(Json.fromInt(variant), newEvents))
                }
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case Left(_) =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing experimentName")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "peek" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("experimentName").as[String] match {
            case Right(experimentName) =>
              try {
                val variant = context.peek(experimentName)
                Ok(wrapperResponse(Json.fromInt(variant), List.empty))
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case Left(_) =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing experimentName")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "variableValue" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("key").as[String], cursor.downField("defaultValue").focus) match {
            case (Right(key), Some(defaultValueJson)) =>
              try {
                val prevCount = collector.getEvents().length
                val defaultValue = defaultValueToString(defaultValueJson)
                val value = context.variableValue(key, defaultValue)
                val newEvents = collector.getEventsSince(prevCount)
                Ok(wrapperResponse(parseVariableResult(value), newEvents))
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing key or defaultValue")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "peekVariableValue" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("key").as[String], cursor.downField("defaultValue").focus) match {
            case (Right(key), Some(defaultValueJson)) =>
              try {
                val defaultValue = defaultValueToString(defaultValueJson)
                val value = context.peekVariableValue(key, defaultValue)
                Ok(wrapperResponse(parseVariableResult(value), List.empty))
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing key or defaultValue")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "track" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          cursor.downField("goalName").as[String] match {
            case Right(goalName) =>
              try {
                // The SDK decodes non-object properties to None and tracks
                // anyway, so enforce the spec's type check here: a present
                // properties value that is not a JSON object (number, string,
                // array) must fail (scenarios 37/38/39).
                cursor.downField("properties").focus match {
                  case Some(js) if !js.isNull && !js.isObject =>
                    BadRequest(Json.obj("error" -> Json.fromString(s"Goal '$goalName' properties must be of type object.")))
                  case _ =>
                    val properties = cursor.downField("properties").as[Map[String, Json]].toOption
                    val prevCount = collector.getEvents().length
                    context.track(goalName, properties)
                    val newEvents = collector.getEventsSince(prevCount)
                    Ok(wrapperResponse(Json.Null, newEvents))
                }
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case Left(_) =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing goalName")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "override" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("variant").as[Int]) match {
            case (Right(experimentName), Right(variant)) =>
              try {
                context.setOverride(experimentName, variant)
                Ok(wrapperResponse(Json.Null, List.empty))
              } catch {
                case e: Exception =>
                  val msg = Option(e.getMessage).getOrElse("")
                  val lower = msg.toLowerCase
                  if (lower.contains("closed") || lower.contains("closing") || lower.contains("finalized")) {
                    Ok(wrapperResponse(Json.Null, List.empty))
                  } else {
                    BadRequest(Json.obj("error" -> Json.fromString(msg)))
                  }
              }
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing experimentName or variant")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "customAssignment" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("variant").as[Int]) match {
            case (Right(experimentName), Right(variant)) =>
              try {
                context.setCustomAssignment(experimentName, variant)
                Ok(wrapperResponse(Json.Null, List.empty))
              } catch {
                case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
              }
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing experimentName or variant")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "customFieldValue" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("fieldName").as[String]) match {
            case (Right(experimentName), Right(fieldName)) =>
              val value = context.customFieldValue(experimentName, fieldName)
              Ok(wrapperResponse(value.getOrElse(Json.Null), List.empty))
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing experimentName or fieldName")))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "customFieldKeys" =>
      withContext(contextId) { case (context, _) =>
        val keys = context.customFieldKeys()
        Ok(wrapperResponse(keys.asJson, List.empty))
      }

    case req @ POST -> Root / "context" / contextId / "customFieldValueType" =>
      withContext(contextId) { case (context, _) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("fieldName").as[String]) match {
            case (Right(experimentName), Right(fieldName)) =>
              val valueType = context.customFieldValueType(experimentName, fieldName)
              Ok(wrapperResponse(valueType.map(Json.fromString).getOrElse(Json.Null), List.empty))
            case _ =>
              BadRequest(Json.obj("error" -> Json.fromString("Missing experimentName or fieldName")))
          }
        }
      }

    case POST -> Root / "context" / contextId / "variableKeys" =>
      withContext(contextId) { case (context, _) =>
        try {
          val keys = context.variableKeys()
          val result = keys.keys.toList.asJson
          Ok(wrapperResponse(result, List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case GET -> Root / "context" / contextId / "pending" =>
      withContext(contextId) { case (context, _) =>
        Ok(wrapperResponse(Json.fromInt(context.pending()), List.empty))
      }

    case GET -> Root / "context" / contextId / "isFinalized" =>
      withContext(contextId) { case (context, _) =>
        Ok(wrapperResponse(Json.fromBoolean(context.isFinalized()), List.empty))
      }

    case GET -> Root / "context" / contextId / "isReady" =>
      withContext(contextId) { case (context, _) =>
        Ok(wrapperResponse(Json.fromBoolean(context.isReady()), List.empty))
      }

    case GET -> Root / "context" / contextId / "isFailed" =>
      withContext(contextId) { case (context, _) =>
        Ok(wrapperResponse(Json.fromBoolean(context.isFailed()), List.empty))
      }

    case GET -> Root / "context" / contextId / "experiments" =>
      withContext(contextId) { case (context, _) =>
        try {
          val exps = context.experiments()
          Ok(wrapperResponse(exps.asJson, List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case POST -> Root / "context" / contextId / "getUnits" =>
      withContext(contextId) { case (context, _) =>
        try {
          val units = context.getUnits()
          val result = units.map { case (k, v) =>
            val parsed = try { Json.fromInt(v.toInt) }
            catch { case _: NumberFormatException =>
              try { Json.fromDoubleOrNull(v.toDouble) }
              catch { case _: NumberFormatException => Json.fromString(v) }
            }
            k -> parsed
          }
          Ok(wrapperResponse(Json.obj(result.toSeq: _*), List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case POST -> Root / "context" / contextId / "getAttributes" =>
      withContext(contextId) { case (context, _) =>
        try {
          val attrs = context.getAttributes()
          Ok(wrapperResponse(attrs.map { case (k, v) => k -> v }.asJson, List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case POST -> Root / "context" / contextId / "readyError" =>
      withContext(contextId) { case (context, _) =>
        try {
          val error = context.readyError()
          val result = error.map(e => Json.obj("isError" -> Json.True, "message" -> Json.fromString(e.getMessage))).getOrElse(Json.Null)
          Ok(wrapperResponse(result, List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case POST -> Root / "context" / contextId / "variableKeysMap" =>
      withContext(contextId) { case (context, _) =>
        try {
          val keys = context.variableKeys()
          val result = keys.map { case (k, v) => k -> v.asJson }.asJson
          Ok(wrapperResponse(result, List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case POST -> Root / "context" / contextId / "globalCustomFieldKeys" =>
      withContext(contextId) { case (context, _) =>
        try {
          val keys = context.customFieldKeys()
          Ok(wrapperResponse(keys.asJson, List.empty))
        } catch {
          case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
        }
      }

    case POST -> Root / "context" / contextId / "publishFail" =>
      withContext(contextId) { case (_, _) =>
        publishFailFlags(contextId) = true
        Ok(wrapperResponse(Json.Null, List.empty))
      }

    case POST -> Root / "context" / contextId / "publish" =>
      withContext(contextId) { case (context, collector) =>
        if (publishFailFlags.getOrElse(contextId, false)) {
          publishFailFlags.remove(contextId)
          InternalServerError(Json.obj("error" -> Json.fromString("publish failed")))
        } else {
          val prevCount = collector.getEvents().length
          IO.fromFuture(IO(context.publish())).flatMap { _ =>
            val newEvents = collector.getEventsSince(prevCount)
            Ok(wrapperResponse(Json.Null, newEvents))
          }.handleErrorWith { err =>
            InternalServerError(Json.obj("error" -> Json.fromString(err.getMessage)))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "refresh" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          // Apply the request's newData to the context so refresh operates on the
          // updated payload (not the stale one the context was created with).
          val newData = json.hcursor.downField("newData").as[ContextData].toOption
          IO {
            try {
              val prevCount = collector.getEvents().length
              newData match {
                case Some(data) => context.refresh(data)
                case None       => context.refresh()
              }
              val newEvents = collector.getEventsSince(prevCount)
              Ok(wrapperResponse(Json.Null, newEvents))
            } catch {
              case e: Exception => BadRequest(Json.obj("error" -> Json.fromString(e.getMessage)))
            }
          }.flatten
        }
      }

    case POST -> Root / "context" / contextId / "finalize" =>
      withContext(contextId) { case (context, collector) =>
        val prevCount = collector.getEvents().length
        IO.fromFuture(IO(context.finalizeContext())).flatMap { _ =>
          val newEvents = collector.getEventsSince(prevCount)
          Ok(wrapperResponse(Json.Null, newEvents))
        }.handleErrorWith { err =>
          InternalServerError(Json.obj("error" -> Json.fromString(err.getMessage)))
        }
      }
  }

  private def withContext(contextId: String)(f: ((Context, WrapperEventCollector)) => IO[Response[IO]]): IO[Response[IO]] = {
    contexts.get(contextId) match {
      case Some(pair) => f(pair)
      case None => NotFound(Json.obj("error" -> Json.fromString("Context not found")))
    }
  }

  private def parseVariableResult(value: String): Json = {
    io.circe.parser.parse(value) match {
      case Right(json) => json
      case Left(_) => Json.fromString(value)
    }
  }

  private def defaultValueToString(value: Json): String = {
    value.asString
      .orElse(value.asNumber.map(_.toString))
      .orElse(value.asBoolean.map(_.toString))
      .getOrElse(value.noSpaces)
  }

  private def wrapperResponse(result: Json, events: List[WrapperEvent]): Json = {
    Json.obj(
      "result" -> result,
      "events" -> events.map(_.toJson).asJson
    )
  }

  def run(args: List[String]): IO[ExitCode] = {
    BlazeServerBuilder[IO]
      .bindHttp(3000, "0.0.0.0")
      .withHttpApp(routes.orNotFound)
      .serve
      .compile
      .drain
      .as(ExitCode.Success)
  }
}
