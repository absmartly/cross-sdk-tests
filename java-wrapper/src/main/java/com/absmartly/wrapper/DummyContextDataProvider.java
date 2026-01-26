package com.absmartly.wrapper;

import com.absmartly.sdk.ContextDataProvider;
import com.absmartly.sdk.json.ContextData;
import java8.util.concurrent.CompletableFuture;

public class DummyContextDataProvider implements ContextDataProvider {
    private ContextData nextData;

    public DummyContextDataProvider() {
        this.nextData = null;
    }

    public void setNextData(ContextData data) {
        this.nextData = data;
    }

    @Override
    public CompletableFuture<ContextData> getContextData() {
        if (nextData != null) {
            ContextData data = nextData;
            nextData = null;
            return CompletableFuture.completedFuture(data);
        }
        return CompletableFuture.completedFuture(new ContextData());
    }
}
