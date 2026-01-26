FROM ruby:3.1-slim

RUN apt-get update && apt-get install -y build-essential git && rm -rf /var/lib/apt/lists/*

# Copy and install Ruby SDK dependencies
WORKDIR /ruby-sdk
COPY ruby-sdk/ ./
RUN bundle install

# Install wrapper dependencies
WORKDIR /app
COPY cross-sdk-tests/ruby-wrapper/Gemfile* ./
RUN bundle install

COPY cross-sdk-tests/ruby-wrapper/server.rb ./

# Add Ruby SDK lib to load path
ENV RUBYLIB=/ruby-sdk/lib:$RUBYLIB

EXPOSE 3000

CMD ["ruby", "server.rb"]
