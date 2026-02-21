package com.absmartly.wrapper

import com.absmartly.sdk.Context

data class ContextWrapper(
    val context: Context,
    val eventCollector: EventCollector
)
