FROM elixir:1.15-alpine AS build

RUN apk add --no-cache build-base git

WORKDIR /app

COPY elixir-sdk /app/elixir-sdk

COPY cross-sdk-tests/elixir-wrapper /app/elixir-wrapper

WORKDIR /app/elixir-wrapper

RUN mix local.hex --force && \
    mix local.rebar --force

RUN mix deps.get --only prod

RUN MIX_ENV=prod mix compile

FROM build AS tester
WORKDIR /app/elixir-sdk
RUN mix local.hex --force && \
    mix local.rebar --force && \
    mix deps.get && \
    mix compile
CMD ["mix", "test"]

FROM elixir:1.15-alpine

RUN apk add --no-cache libgcc libstdc++

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

COPY --from=build /app/elixir-wrapper /app/elixir-wrapper
COPY --from=build /app/elixir-sdk /app/elixir-sdk

RUN chown -R appuser:appgroup /app

WORKDIR /app/elixir-wrapper

RUN mix local.hex --force && \
    mix local.rebar --force

EXPOSE 3000

USER appuser

ENV MIX_ENV=prod

CMD ["mix", "run", "--no-halt"]
