package com.absmartly.wrapper;

import static org.mockito.Mockito.*;

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
}
