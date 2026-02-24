package com.absmartly.wrapper

import scala.collection.mutable
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

class WrapperEventCollector extends EventLogger {
  private val events: mutable.ListBuffer[WrapperEvent] = mutable.ListBuffer()

  override def logEvent(eventType: String, data: Json): Unit = {
    events += WrapperEvent(eventType, data, System.currentTimeMillis())
  }

  def getEvents(): List[WrapperEvent] = events.toList

  def getEventsSince(previousCount: Int): List[WrapperEvent] = {
    events.drop(previousCount).toList
  }

  def clear(): Unit = {
    events.clear()
  }
}
