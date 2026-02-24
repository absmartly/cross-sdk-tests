FROM ruby:3.1-slim AS sdk-builder

RUN apt-get update && apt-get install -y build-essential git && rm -rf /var/lib/apt/lists/*

WORKDIR /ruby-sdk
COPY ruby-sdk/ ./
RUN bundle install

FROM sdk-builder AS tester
WORKDIR /ruby-sdk
CMD ["bundle", "exec", "rspec"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/ruby-wrapper/Gemfile* ./
RUN bundle install

COPY cross-sdk-tests/ruby-wrapper/server.rb ./

ENV RUBYLIB=/ruby-sdk/lib:$RUBYLIB

EXPOSE 3000

CMD ["ruby", "server.rb"]
