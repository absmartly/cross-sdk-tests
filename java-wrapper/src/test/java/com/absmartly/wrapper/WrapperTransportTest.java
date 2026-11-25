package com.absmartly.wrapper;

import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.*;

import com.absmartly.sdk.JdkHttpTransport;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class WrapperTransportTest {
    @ParameterizedTest
    @CsvSource({"true,true,1", "true,false,1", "false,true,1", "false,false,0"})
    void closeClosesTheSharedHttpTransportOnce(boolean fetchOverHttp, boolean publishOverHttp, int closes) {
        JdkHttpTransport http = mock(JdkHttpTransport.class);
        try (var transports = mockStatic(JdkHttpTransport.class)) {
            transports.when(JdkHttpTransport::create).thenReturn(http);
            new WrapperTransport(fetchOverHttp, publishOverHttp, false, 0).close();
        }
        verify(http, times(closes)).close();
    }
    @ParameterizedTest
    @CsvSource({"true,false,false,true", "true,false,true,true", "true,true,false,false",
        "true,true,true,false", "false,false,false,false", "false,false,true,false",
        "false,true,false,false", "false,true,true,false"})
    void refreshDataInvariantFollowsInlineDataAndFetchMode(boolean inline, boolean fetchOverHttp,
            boolean publishOverHttp, boolean expected) {
        try (WrapperTransport transport = new WrapperTransport(fetchOverHttp, publishOverHttp, false, 0)) {
            ContextWrapper wrapper = new ContextWrapper(null, null, transport, null, inline);
            assertEquals(expected, wrapper.acceptsNewData());
            assertSame(transport, wrapper.getTransport());
        }
    }

    @ParameterizedTest
    @CsvSource({"true", "false"})
    void contextWithoutWrapperTransportNeverAcceptsRefreshData(boolean inline) {
        assertFalse(new ContextWrapper(null, null, null, null, inline).acceptsNewData());
    }
}
