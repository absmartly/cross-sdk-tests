FROM ruby:3.1-slim

RUN apt-get update && apt-get install -y build-essential git && rm -rf /var/lib/apt/lists/*

WORKDIR /ruby-sdk
COPY ruby-sdk/ ./
RUN gem build absmartly.gemspec && gem install absmartly-sdk-*.gem

WORKDIR /liquid-sdk
COPY liquid-sdk/ ./
RUN gem build liquid-sdk.gemspec && gem install absmartly-liquid-sdk-*.gem

WORKDIR /app
COPY cross-sdk-tests/liquid-wrapper/Gemfile* ./
RUN bundle install

COPY cross-sdk-tests/liquid-wrapper/server.rb ./

EXPOSE 3000

CMD ["ruby", "server.rb"]
