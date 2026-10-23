package com.absmartly.wrapper

import io.circe.Json
import io.circe.syntax._
import com.absmartly.sdk.EventLogger

case class WrapperEvent(`type`: String, data: Json, timestamp: Long) {
  def toJson: Json = Json.obj(
    "type" -> Json.fromString(`type`),
    "data" -> data,
    "timestamp" -> Json.fromLong(timestamp)
  )
}

/**
 * The SDK logs from request threads and from Future callbacks (async fetch,
 * publish), so every access goes through one lock and readers only ever get
 * immutable snapshots.
 */
class WrapperEventCollector extends EventLogger {
  private val lock = new AnyRef
  private var events: Vector[WrapperEvent] = Vector.empty

  override def logEvent(eventType: String, data: Json): Unit = lock.synchronized {
    events = events :+ WrapperEvent(eventType, data, System.currentTimeMillis())
  }

  def count(): Int = lock.synchronized(events.length)

  def getEvents(): List[WrapperEvent] = lock.synchronized(events.toList)

  def getEventsSince(previousCount: Int): List[WrapperEvent] = lock.synchronized {
    events.drop(previousCount).toList
  }
}
