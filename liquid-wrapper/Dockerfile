FROM ruby:3.1-slim AS sdk-builder

RUN apt-get update && apt-get install -y build-essential git && rm -rf /var/lib/apt/lists/*

WORKDIR /ruby-sdk
COPY ruby-sdk/ ./
RUN bundle install

WORKDIR /liquid-sdk
COPY liquid-sdk/ ./

FROM sdk-builder AS tester
WORKDIR /liquid-sdk
RUN bundle install
CMD ["bundle", "exec", "rspec"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/liquid-wrapper/Gemfile* ./
RUN bundle install

COPY cross-sdk-tests/liquid-wrapper/server.rb ./

ENV RUBYLIB=/ruby-sdk/lib:/liquid-sdk/lib:$RUBYLIB

EXPOSE 3000

CMD ["ruby", "server.rb"]
