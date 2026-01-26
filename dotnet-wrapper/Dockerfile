FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /app

COPY dotnet-sdk/src/ABSmartly.Sdk/ABSmartly.Sdk.csproj ./dotnet-sdk/src/ABSmartly.Sdk/
COPY cross-sdk-tests/dotnet-wrapper/*.csproj ./cross-sdk-tests/dotnet-wrapper/
RUN dotnet restore ./cross-sdk-tests/dotnet-wrapper/dotnet-wrapper.csproj

COPY dotnet-sdk/src/ABSmartly.Sdk/ ./dotnet-sdk/src/ABSmartly.Sdk/
COPY cross-sdk-tests/dotnet-wrapper/ ./cross-sdk-tests/dotnet-wrapper/
RUN dotnet publish ./cross-sdk-tests/dotnet-wrapper/dotnet-wrapper.csproj -c Release -o out

FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY --from=build /app/out .

ENV ASPNETCORE_URLS=http://+:3000
EXPOSE 3000

ENTRYPOINT ["dotnet", "dotnet-wrapper.dll"]
