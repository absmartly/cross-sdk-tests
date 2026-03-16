package com.absmartly.wrapper

import com.absmartly.sdk.ContextData
import com.absmartly.sdk.ContextDataProvider
import java.util.concurrent.CompletableFuture
import java.util.concurrent.atomic.AtomicReference

class DummyContextDataProvider : ContextDataProvider {
    private val nextData = AtomicReference<ContextData?>(null)

    fun setNextData(data: ContextData) {
        nextData.set(data)
    }

    override fun getContextData(): CompletableFuture<ContextData> {
        val data = nextData.getAndSet(null)
        return CompletableFuture.completedFuture(data ?: ContextData())
    }
}
