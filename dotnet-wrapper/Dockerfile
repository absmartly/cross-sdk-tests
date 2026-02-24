FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin --channel 8.0 --install-dir /usr/share/dotnet
WORKDIR /app

COPY dotnet-sdk/src/ABSmartly.Sdk/ABSmartly.Sdk.csproj ./dotnet-sdk/src/ABSmartly.Sdk/
COPY cross-sdk-tests/dotnet-wrapper/*.csproj ./cross-sdk-tests/dotnet-wrapper/
RUN dotnet restore ./cross-sdk-tests/dotnet-wrapper/dotnet-wrapper.csproj

COPY dotnet-sdk/src/ABSmartly.Sdk/ ./dotnet-sdk/src/ABSmartly.Sdk/
COPY cross-sdk-tests/dotnet-wrapper/ ./cross-sdk-tests/dotnet-wrapper/
RUN dotnet publish ./cross-sdk-tests/dotnet-wrapper/dotnet-wrapper.csproj -c Release -o out

FROM build AS tester
COPY dotnet-sdk/ABSmartly.DotNet.sln ./dotnet-sdk/
COPY dotnet-sdk/tests/ ./dotnet-sdk/tests/
RUN dotnet restore ./dotnet-sdk/tests/ABSmartly.Sdk.Tests/ABSmartly.Sdk.Tests.csproj && \
    dotnet build ./dotnet-sdk/tests/ABSmartly.Sdk.Tests/ --no-restore
CMD ["dotnet", "test", "./dotnet-sdk/tests/ABSmartly.Sdk.Tests/", "--no-build"]

FROM mcr.microsoft.com/dotnet/aspnet:9.0
WORKDIR /app
COPY --from=build /app/out .

ENV ASPNETCORE_URLS=http://+:3000
EXPOSE 3000

ENTRYPOINT ["dotnet", "dotnet-wrapper.dll"]
