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
import scala.concurrent.duration._
import scala.collection.concurrent.TrieMap
import java.util.concurrent.atomic.AtomicInteger
import com.absmartly.sdk._

object Server extends IOApp {

  implicit val ec: ExecutionContext = ExecutionContext.global

  private val contexts = TrieMap[String, (Context, WrapperEventCollector)]()
  private val publishers = TrieMap[String, ArmablePublisher]()
  private val pendingFetches = TrieMap[String, FiberIO[Unit]]()
  private val payloads = TrieMap[String, Json]()
  private val contextCounter = new AtomicInteger(0)

  final case class InvalidRequest(message: String) extends Exception(message)

  implicit val jsonEntityDecoder: EntityDecoder[IO, Json] = jsonOf[IO, Json]

  private def translateEndpoint(endpoint: String): String = {
    endpoint
      .replaceAll("http://localhost:\\d+", "http://localhost:3000")
      .replaceAll("http://127\\.0\\.0\\.1:\\d+", "http://localhost:3000")
  }

  /**
   * Transport injected through the SDK's public `SDKConfig.publisher` hook.
   * Normally a no-op sink (scenarios have no collector). `/publishFail` arms a
   * one-shot transport failure; the real `Context.publish()` path then runs,
   * receives the failed Future and is responsible for preserving pending events
   * (Context.scala `_flush` re-queues on failure). The wrapper never fakes the
   * publish result itself.
   */
  final class ArmablePublisher(delegate: Option[ContextPublisher] = None) extends ContextPublisher {
    private val failNext = new java.util.concurrent.atomic.AtomicBoolean(false)
    def arm(): Unit = failNext.set(true)
    def publish(units: Map[String, String], hashed: Boolean, exposures: List[Exposure], goals: List[Goal], attributes: Option[List[Attribute]]): scala.concurrent.Future[Unit] =
      if (failNext.getAndSet(false)) scala.concurrent.Future.failed(new RuntimeException("Publish failed"))
      else delegate.fold(scala.concurrent.Future.successful(()))(_.publish(units, hashed, exposures, goals, attributes))
  }

  private def makeSdkConfig(endpoint: String, collector: WrapperEventCollector, publisher: ArmablePublisher): SDKConfig =
    SDKConfig(
      endpoint = endpoint,
      apiKey = "test-key",
      application = "test-app",
      environment = "test",
      eventLogger = collector,
      publisher = Some(publisher)
    )

  private def unitsObject(cursor: io.circe.ACursor): List[(String, Json)] =
    cursor.focus.flatMap(_.asObject).map(_.toList).getOrElse(Nil)

  /** String units go to the SDK's create call; numeric ones are set with the SDK's numeric setUnit. */
  private def parseUnitsMap(cursor: io.circe.ACursor): Map[String, String] =
    unitsObject(cursor).collect { case (k, v) if v.isString => k -> v.asString.get }.toMap

  private def setUnitJson(context: Context, unitType: String, uid: Json): Unit =
    uid.asNumber match {
      case Some(n) => n.toLong match {
        case Some(l) => context.setUnit(unitType, l)
        case None    => context.setUnit(unitType, n.toDouble)
      }
      case None => context.setUnit(unitType, uid.asString.getOrElse(uid.noSpaces))
    }

  private def applyNumericUnits(context: Context, cursor: io.circe.ACursor): Unit =
    unitsObject(cursor).filterNot(_._2.isString).foreach { case (k, v) => setUnitJson(context, k, v) }

  val routes = HttpRoutes.of[IO] {

    case GET -> Root / "health" =>
      Ok(Json.obj(
        "status" -> Json.fromString("healthy"),
        "sdk" -> Json.fromString("scala"),
        "version" -> Json.fromString("0.1.0")
      ))

    case GET -> Root / "capabilities" =>
      Ok(Json.obj("diagnostics" -> Json.fromBoolean(true),
        "diagnosticsUtf8" -> Json.fromBoolean(true),
        "diagnosticsBase64" -> Json.fromBoolean(true),
        "diagnosticsTypeChecks" -> Json.fromBoolean(true),
        "diagnosticsIsPromise" -> Json.fromBoolean(true),
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
            Right(Json.fromString(value.asNumber.fold(Utils.hashUnit(text))(n => Utils.hashUnit(n.toDouble))))
          case "base64UrlNoPadding" =>
            Right(Json.fromString(Utils.base64UrlNoPadding(Utils.utf8Bytes(text))))
          case "utf8Bytes" =>
            Right(Json.arr(Utils.utf8Bytes(text).map(b => Json.fromInt(b & 0xff)).toIndexedSeq: _*))
          case "isObject" =>
            Right(Json.fromBoolean(Utils.isObject(value)))
          case "isNumeric" =>
            Right(Json.fromBoolean(Utils.isNumeric(value)))
          case "isPromise" =>
            Right(Json.fromBoolean(Utils.isPromise(value)))
          case _ =>
            Left(s"Unsupported diagnostic operation: $op")
        }

        result match {
          case Right(v) => Ok(wrapperResponse(v, List.empty))
          case Left(err) => BadRequest(errorBody(err))
        }
      }

    // Payloads are stored as raw JSON so only the SDK's own fetch decodes them.
    case req @ PUT -> Root / "context_payload" / payloadId =>
      req.as[Json].flatMap { json =>
        json.hcursor.downField("data").focus match {
          case Some(data) =>
            payloads(payloadId) = data
            Ok(Json.obj("success" -> Json.fromBoolean(true)))
          case None =>
            BadRequest(errorBody("Missing data"))
        }
      }

    case GET -> Root / "context_payload" / payloadId / "context" :? _ =>
      payloads.get(payloadId) match {
        case Some(data) => Ok(data)
        case None => NotFound(errorBody("Payload not found"))
      }

    case req @ POST -> Root / "context_payload" / payloadId / "context" =>
      payloads.get(payloadId) match {
        case Some(data) => Ok(data)
        case None => NotFound(errorBody("Payload not found"))
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
                val baseConfig = SDKConfig(
                  endpoint = endpoint,
                  apiKey = apiKey,
                  application = application,
                  environment = environment,
                  eventLogger = collector
                )
                // Real collector transport, wrapped so /publishFail can arm a
                // one-shot failure in e2e mode too.
                val publisher = new ArmablePublisher(Some(new DefaultContextPublisher(baseConfig)))
                val sdk = new SDK(baseConfig.copy(publisher = Some(publisher)))
                sdk.createContext(units).map { context =>
                  applyNumericUnits(context, cursor.downField("units"))
                  attrs.foreach { case (name, value) => context.setAttribute(name, value) }
                  (context, collector, publisher)
                }
              }).flatMap { case (context, collector, publisher) =>
                val contextId = s"ctx-${contextCounter.getAndIncrement()}"
                contexts(contextId) = (context, collector)
                publishers(contextId) = publisher
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
                InternalServerError(errorBody(err.getMessage))
              }

            case _ =>
              NotImplemented(errorBody("e2e mode not configured"))
          }
        } else {

        val units = parseUnitsMap(cursor.downField("units"))

        val options = ContextOptions(
          units = units,
          attributes = cursor.downField("options").downField("attributes").as[Map[String, Json]].getOrElse(Map.empty),
          overrides = cursor.downField("options").downField("overrides").as[Map[String, Int]].getOrElse(Map.empty),
          cassignments = cursor.downField("options").downField("cassignments").as[Map[String, Int]].getOrElse(Map.empty)
        )

        val publisher = new ArmablePublisher()
        val failLoad = cursor.downField("failLoad").as[Boolean].getOrElse(false)
        val dataJson = cursor.downField("data").focus
        val endpointOpt = cursor.downField("endpoint").as[String].toOption

        // Returns the context plus, for a throttled context, the SDK fetch to
        // start once the context is registered.
        val createContext: IO[(Context, WrapperEventCollector, Option[IO[Unit]])] = if (failLoad) {
          // The SDK's own fetch against a closed port fails like a real
          // network failure; SDK.createContext then yields a failed context.
          IO.fromFuture(IO {
            val collector = new WrapperEventCollector()
            val sdk = new SDK(SDKConfig(
              endpoint = "http://127.0.0.1:1", apiKey = "test-key", application = "test-app",
              environment = "test", retries = 0, timeout = 1000,
              eventLogger = collector, publisher = Some(publisher)))
            sdk.createContext(units, options).map(context => (context, collector, None))
          })
        } else dataJson match {
          case Some(js) =>
            js.as[ContextData] match {
              case Right(data) =>
                IO {
                  val collector = new WrapperEventCollector()
                  val sdk = new SDK(makeSdkConfig("http://localhost:3000", collector, publisher))
                  val context = sdk.createContextWith(units, data, options)
                  (context, collector, None)
                }
              case Left(err) =>
                IO.raiseError(InvalidRequest(s"Invalid data: ${err.getMessage}"))
            }

          case None =>
            endpointOpt match {
              case Some(endpoint) =>
                val payloadThrottle = cursor.downField("options").downField("payloadThrottle").as[Int].getOrElse(0)
                val translatedEndpoint = translateEndpoint(endpoint)

                if (payloadThrottle > 0) {
                  IO {
                    val collector = new WrapperEventCollector()
                    val sdk = new SDK(makeSdkConfig(translatedEndpoint, collector, publisher))
                    // A not-yet-ready context, readied by the SDK's own async
                    // fetch after the throttle delay, without holding a thread.
                    val context = new Context(sdk, None, units, options, collector)
                    val fetch = IO.sleep(payloadThrottle.millis) *>
                      IO.fromFuture(IO(sdk.fetchContextDataAsync())).attempt.flatMap {
                        case Right(data) => IO(context.setData(data))
                        case Left(err)   => IO(context.setDataFailed(Some(err)))
                      }
                    (context, collector, Some(fetch))
                  }
                } else {
                  IO.fromFuture(IO {
                    val collector = new WrapperEventCollector()
                    val sdk = new SDK(makeSdkConfig(translatedEndpoint, collector, publisher))
                    sdk.createContext(units, options).map { context =>
                      (context, collector, None)
                    }
                  })
                }
              case None =>
                IO.raiseError(InvalidRequest("One of data, endpoint or failLoad is required"))
            }
        }

        createContext.flatMap { case (context, collector, deferredFetch) =>
          applyNumericUnits(context, cursor.downField("units"))
          val contextId = s"ctx-${contextCounter.getAndIncrement()}"
          contexts(contextId) = (context, collector)
          publishers(contextId) = publisher

          val startFetch = deferredFetch.fold(IO.unit) { fetch =>
            fetch.guarantee(IO(pendingFetches.remove(contextId)).void).start
              .flatMap(fiber => IO(pendingFetches(contextId) = fiber))
          }

          startFetch *> Ok(wrapperResponse(
            Json.obj(
              "contextId" -> Json.fromString(contextId),
              "ready" -> Json.fromBoolean(context.isReady()),
              "failed" -> Json.fromBoolean(context.isFailed()),
              "finalized" -> Json.fromBoolean(context.isFinalized())
            ),
            collector.getEvents()
          ))
        }.handleErrorWith {
          case err: UnsupportedOperationException =>
            NotImplemented(errorBody(err.getMessage))
          case err: InvalidRequest =>
            BadRequest(errorBody(err.getMessage))
          case err =>
            InternalServerError(errorBody(err.getMessage))
        }
        }
      }

    case DELETE -> Root / "context" / contextId =>
      contexts.remove(contextId)
      publishers.remove(contextId)
      // Drop a throttled fetch that has not started yet.
      pendingFetches.remove(contextId).fold(IO.unit)(_.cancel) *>
        Ok(Json.obj("result" -> Json.fromString("deleted")))

    case req @ POST -> Root / "context" / contextId / "setUnit" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          val unitTypeOpt = cursor.downField("unitType").as[String].toOption
          val uidOpt = cursor.downField("uid").focus
          (unitTypeOpt, uidOpt) match {
            case (Some(unitType), Some(uid)) =>
              respondWithEvents(collector) {
                setUnitJson(context, unitType, uid)
                Json.Null
              }
            case _ =>
              BadRequest(errorBody("Missing unitType or uid"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "getUnit" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("unitType").as[String] match {
            case Right(unitType) =>
              respondWithEvents(collector) {
                context.getUnitValue(unitType).getOrElse(Json.Null)
              }
            case Left(_) =>
              BadRequest(errorBody("Missing unitType"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "attribute" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("name").as[String], cursor.downField("value").focus) match {
            case (Right(name), Some(value)) =>
              respondWithEvents(collector) {
                context.setAttribute(name, value)
                Json.Null
              }
            case _ =>
              BadRequest(errorBody("Missing name or value"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "getAttribute" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("name").as[String] match {
            case Right(name) =>
              respondWithEvents(collector)(context.getAttribute(name).getOrElse(Json.Null))
            case Left(_) =>
              BadRequest(errorBody("Missing name"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "treatment" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("experimentName").as[String] match {
            case Right(experimentName) =>
              respondWithEvents(collector)(Json.fromInt(context.treatment(experimentName)))
            case Left(_) =>
              BadRequest(errorBody("Missing experimentName"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "peek" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          json.hcursor.downField("experimentName").as[String] match {
            case Right(experimentName) =>
              respondWithEvents(collector)(Json.fromInt(context.peek(experimentName)))
            case Left(_) =>
              BadRequest(errorBody("Missing experimentName"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "variableValue" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("key").as[String], cursor.downField("defaultValue").focus) match {
            case (Right(key), Some(defaultValue)) =>
              respondWithEvents(collector) {
                variableResult(context.variableValue(key, defaultValue.noSpaces))
              }
            case _ =>
              BadRequest(errorBody("Missing key or defaultValue"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "peekVariableValue" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("key").as[String], cursor.downField("defaultValue").focus) match {
            case (Right(key), Some(defaultValue)) =>
              respondWithEvents(collector) {
                variableResult(context.peekVariableValue(key, defaultValue.noSpaces))
              }
            case _ =>
              BadRequest(errorBody("Missing key or defaultValue"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "track" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          cursor.downField("goalName").as[String] match {
            case Right(goalName) =>
              // The SDK's track takes Option[Map[String, Json]], so a non-object
              // cannot reach it: reject at the JSON-to-type boundary.
              cursor.downField("properties").focus match {
                case Some(js) if !js.isNull && !js.isObject =>
                  BadRequest(errorBody(s"Goal '$goalName' properties must be of type object."))
                case _ =>
                  val properties = cursor.downField("properties").as[Map[String, Json]].toOption
                  respondWithEvents(collector) {
                    context.track(goalName, properties)
                    Json.Null
                  }
              }
            case Left(_) =>
              BadRequest(errorBody("Missing goalName"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "override" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("variant").as[Int]) match {
            case (Right(experimentName), Right(variant)) =>
              respondWithEvents(collector) {
                context.setOverride(experimentName, variant)
                Json.Null
              }
            case _ =>
              BadRequest(errorBody("Missing experimentName or variant"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "customAssignment" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("variant").as[Int]) match {
            case (Right(experimentName), Right(variant)) =>
              respondWithEvents(collector) {
                context.setCustomAssignment(experimentName, variant)
                Json.Null
              }
            case _ =>
              BadRequest(errorBody("Missing experimentName or variant"))
          }
        }
      }

    case req @ POST -> Root / "context" / contextId / "customFieldValue" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("fieldName").as[String]) match {
            case (Right(experimentName), Right(fieldName)) =>
              respondWithEvents(collector)(context.customFieldValue(experimentName, fieldName).getOrElse(Json.Null))
            case _ =>
              BadRequest(errorBody("Missing experimentName or fieldName"))
          }
        }
      }

    case POST -> Root / "context" / contextId / "customFieldKeys" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(context.customFieldKeys().asJson)
      }

    case req @ POST -> Root / "context" / contextId / "customFieldValueType" =>
      withContext(contextId) { case (context, collector) =>
        req.as[Json].flatMap { json =>
          val cursor = json.hcursor
          (cursor.downField("experimentName").as[String], cursor.downField("fieldName").as[String]) match {
            case (Right(experimentName), Right(fieldName)) =>
              respondWithEvents(collector) {
                context.customFieldValueType(experimentName, fieldName).map(Json.fromString).getOrElse(Json.Null)
              }
            case _ =>
              BadRequest(errorBody("Missing experimentName or fieldName"))
          }
        }
      }

    case POST -> Root / "context" / contextId / "variableKeys" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(context.variableKeys().keys.toList.asJson)
      }

    case GET -> Root / "context" / contextId / "pending" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(Json.fromInt(context.pending()))
      }

    case GET -> Root / "context" / contextId / "isFinalized" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(Json.fromBoolean(context.isFinalized()))
      }

    case GET -> Root / "context" / contextId / "isReady" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(Json.fromBoolean(context.isReady()))
      }

    case GET -> Root / "context" / contextId / "isFailed" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(Json.fromBoolean(context.isFailed()))
      }

    case GET -> Root / "context" / contextId / "experiments" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(context.experiments().asJson)
      }

    case POST -> Root / "context" / contextId / "getUnits" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector) {
          Json.obj(context.getUnitValues().toSeq: _*)
        }
      }

    case POST -> Root / "context" / contextId / "getAttributes" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(context.getAttributes().asJson)
      }

    case POST -> Root / "context" / contextId / "readyError" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector) {
          context.readyError()
            .map(e => Json.obj("isError" -> Json.True, "message" -> Json.fromString(e.getMessage)))
            .getOrElse(Json.Null)
        }
      }

    case POST -> Root / "context" / contextId / "variableKeysMap" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(context.variableKeys().map { case (k, v) => k -> v.asJson }.asJson)
      }

    case POST -> Root / "context" / contextId / "globalCustomFieldKeys" =>
      withContext(contextId) { case (context, collector) =>
        respondWithEvents(collector)(context.customFieldKeys().asJson)
      }

    case POST -> Root / "context" / contextId / "publishFail" =>
      withContext(contextId) { case (_, collector) =>
        publishers.get(contextId) match {
          case Some(p) =>
            respondWithEvents(collector) {
              p.arm()
              Json.Null
            }
          case None =>
            NotImplemented(errorBody("publishFail unsupported for this context (no injectable publisher)"))
        }
      }

    case POST -> Root / "context" / contextId / "publish" =>
      withContext(contextId) { case (context, collector) =>
        awaitWithEvents(collector)(context.publish())
      }

    case req @ POST -> Root / "context" / contextId / "refresh" =>
      withContext(contextId) { case (context, collector) =>
        // The orchestrator has already stored newData at the payload endpoint;
        // the SDK's own refresh() fetches it.
        IO.blocking(collector.count()).flatMap { prevCount =>
          IO.blocking(context.refresh()).attempt.flatMap(respond(collector, prevCount, Json.Null))
        }
      }

    case POST -> Root / "context" / contextId / "finalize" =>
      withContext(contextId) { case (context, collector) =>
        awaitWithEvents(collector)(context.finalizeContext())
      }
  }

  private def withContext(contextId: String)(f: ((Context, WrapperEventCollector)) => IO[Response[IO]]): IO[Response[IO]] = {
    contexts.get(contextId) match {
      case Some(pair) => f(pair)
      case None => NotFound(errorBody("Context not found"))
    }
  }

  /**
   * Runs one SDK operation and responds with its result plus exactly the
   * events the SDK logged during the call. An SDK exception becomes a 400.
   */
  private def respondWithEvents(collector: WrapperEventCollector)(op: => Json): IO[Response[IO]] =
    IO(collector.count()).flatMap { prevCount =>
      IO(op).attempt.flatMap(r => respond(collector, prevCount, r.getOrElse(Json.Null))(r.map(_ => ())))
    }

  /** Awaits an SDK Future; the response carries the events logged meanwhile. */
  private def awaitWithEvents(collector: WrapperEventCollector)(op: => scala.concurrent.Future[Unit]): IO[Response[IO]] =
    IO(collector.count()).flatMap { prevCount =>
      IO.fromFuture(IO(op)).attempt.flatMap(respond(collector, prevCount, Json.Null))
    }

  private def respond(collector: WrapperEventCollector, prevCount: Int, result: Json)(outcome: Either[Throwable, Unit]): IO[Response[IO]] = {
    val events = collector.getEventsSince(prevCount).map(_.toJson).asJson
    outcome match {
      case Right(_) => Ok(Json.obj("result" -> result, "events" -> events))
      case Left(e)  => BadRequest(Json.obj(
        "error" -> Json.fromString(Option(e.getMessage).getOrElse(e.toString)),
        "events" -> events))
    }
  }

  /**
   * The SDK's variable API is String-only: a resolved variable comes back as
   * the JSON text of its config value (`value.noSpaces`), and an unresolved key
   * returns the caller's default verbatim. Defaults are therefore passed in as
   * JSON text too, so every result is valid JSON and decodes back to its exact
   * type: a string "123" stays a string, and a default round-trips unchanged.
   */
  private def variableResult(value: String): Json =
    io.circe.parser.parse(value) match {
      case Right(json) => json
      case Left(err) =>
        throw new IllegalStateException(s"SDK returned a variable value that is not JSON: $value (${err.message})")
    }

  /** An error that no SDK call produced: there are no SDK events to return. */
  private def errorBody(message: String): Json =
    Json.obj("error" -> Json.fromString(message), "events" -> Json.arr())

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
