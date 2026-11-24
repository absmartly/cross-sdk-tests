FROM node:24-alpine AS sdk-builder

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

FROM sdk-builder AS tester
WORKDIR /javascript-sdk
CMD ["npx", "jest", "--coverage", "--forceExit", "--maxWorkers=2"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/javascript-wrapper/package*.json ./
RUN npm install

COPY cross-sdk-tests/javascript-wrapper/server.js ./
COPY cross-sdk-tests/javascript-wrapper/holdoutSelfTest.js ./
COPY cross-sdk-tests/javascript-wrapper/assignmentRulesSelfTest.js ./
COPY cross-sdk-tests/javascript-wrapper/switchbackSelfTest.js ./
COPY cross-sdk-tests/javascript-wrapper/sharedVariableKeysSelfTest.js ./

FROM server AS wrapper-tests
COPY cross-sdk-tests/javascript-wrapper/switchbackSelfTest.test.js ./
COPY cross-sdk-tests/javascript-wrapper/sharedVariableKeysSelfTest.test.js ./
RUN node --test switchbackSelfTest.test.js sharedVariableKeysSelfTest.test.js && touch /wrapper-tests.passed

# The runtime image depends on wrapper-tests (through its marker), so BuildKit and buildah cannot
# skip the stage and a failing wrapper test fails the image build. Test files stay out of the runtime.
FROM server
COPY --from=wrapper-tests /wrapper-tests.passed /wrapper-tests.passed

EXPOSE 3000

CMD ["node", "server.js"]
