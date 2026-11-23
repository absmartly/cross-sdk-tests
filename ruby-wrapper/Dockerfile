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

COPY cross-sdk-tests/ruby-wrapper/server.rb cross-sdk-tests/ruby-wrapper/holdout_self_test.rb cross-sdk-tests/ruby-wrapper/switchback_self_test.rb cross-sdk-tests/ruby-wrapper/server_test.rb ./

ENV RUBYLIB=/ruby-sdk/lib:$RUBYLIB

RUN ruby server_test.rb

FROM server AS wrapper-tests
COPY cross-sdk-tests/ruby-wrapper/probe_regression_test.rb ./
RUN ruby probe_regression_test.rb && touch /wrapper-tests.passed

# The runtime image depends on wrapper-tests (through its marker), so BuildKit and buildah cannot
# skip the stage and a failing wrapper test fails the image build. Test files stay out of the runtime.
FROM server
COPY --from=wrapper-tests /wrapper-tests.passed /wrapper-tests.passed

EXPOSE 3000

CMD ["ruby", "server.rb"]
