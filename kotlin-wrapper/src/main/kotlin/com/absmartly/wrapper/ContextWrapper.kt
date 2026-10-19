package com.absmartly.wrapper

import com.absmartly.sdk.ABsmartly
import com.absmartly.sdk.Context

data class ContextWrapper(
    val context: Context,
    val eventCollector: EventCollector,
    val dataProvider: DummyContextDataProvider?,
    val publisher: CustomPublisher? = null,
    val sdk: ABsmartly? = null,
    val throttledProvider: ThrottledContextDataProvider? = null,
    val fetchesFromEndpoint: Boolean = false
)
