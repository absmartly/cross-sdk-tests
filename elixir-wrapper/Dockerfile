FROM elixir:1.17-alpine AS build

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

# The wrapper's own ExUnit tests (test/*.exs), in a copy of the wrapper so the test build stays out of
# the runtime image, which depends on this stage through its marker.
FROM build AS wrapper-tests
RUN cp -r /app/elixir-wrapper /app/elixir-wrapper-tests
WORKDIR /app/elixir-wrapper-tests
RUN mix deps.get && MIX_ENV=test mix test && touch /wrapper-tests.passed

FROM elixir:1.17-alpine

RUN apk add --no-cache libgcc libstdc++

COPY --from=wrapper-tests /wrapper-tests.passed /wrapper-tests.passed

WORKDIR /app

COPY --from=build /app/elixir-wrapper /app/elixir-wrapper
COPY --from=build /app/elixir-sdk /app/elixir-sdk
COPY --from=build /root/.mix /root/.mix
COPY --from=build /root/.hex /root/.hex

WORKDIR /app/elixir-wrapper

RUN mix local.hex --force && \
    mix local.rebar --force && \
    mix deps.get --only prod && \
    MIX_ENV=prod mix compile

EXPOSE 3000

ENV MIX_ENV=prod

CMD ["mix", "run", "--no-halt"]
