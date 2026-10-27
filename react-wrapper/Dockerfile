FROM node:24-alpine AS sdk-builder

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build && \
    mv "$(npm pack --silent --ignore-scripts)" /tmp/javascript-sdk.tgz

WORKDIR /react-sdk
COPY react-sdk/package*.json react-sdk/yarn.lock* ./
COPY react-sdk/ ./
# Build against the pinned javascript-sdk checkout (packed like a registry
# release), not the registry copy the lockfile would pull.
RUN yarn install && \
    rm -rf node_modules/@absmartly/javascript-sdk && \
    mkdir -p node_modules/@absmartly/javascript-sdk && \
    tar -xzf /tmp/javascript-sdk.tgz -C node_modules/@absmartly/javascript-sdk --strip-components=1
RUN yarn build

FROM sdk-builder AS tester
WORKDIR /react-sdk
CMD ["yarn", "test"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/react-wrapper/package*.json ./
RUN npm install

# Symlink React to react-sdk's node_modules to ensure single React instance
RUN rm -rf /react-sdk/node_modules/react /react-sdk/node_modules/react-dom && \
    ln -s /app/node_modules/react /react-sdk/node_modules/react && \
    ln -s /app/node_modules/react-dom /react-sdk/node_modules/react-dom

COPY cross-sdk-tests/react-wrapper/server.js ./
COPY cross-sdk-tests/javascript-wrapper/holdoutSelfTest.js ./

EXPOSE 3000

CMD ["node", "server.js"]
