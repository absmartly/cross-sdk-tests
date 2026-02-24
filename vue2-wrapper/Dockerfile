FROM node:18-alpine AS sdk-builder

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

WORKDIR /vue2-sdk
COPY vue2-sdk/package*.json ./
COPY vue2-sdk/ ./
RUN npm install && NODE_OPTIONS=--openssl-legacy-provider npm run build

FROM sdk-builder AS tester
WORKDIR /vue2-sdk
CMD ["npm", "test"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/vue2-wrapper/package*.json ./
RUN npm install

# Ensure the local SDKs are properly linked in wrapper's node_modules
RUN rm -rf /app/node_modules/@absmartly/javascript-sdk /app/node_modules/@absmartly/vue2-sdk 2>/dev/null || true && \
    mkdir -p /app/node_modules/@absmartly && \
    ln -sf /javascript-sdk /app/node_modules/@absmartly/javascript-sdk && \
    ln -sf /vue2-sdk /app/node_modules/@absmartly/vue2-sdk

# Symlink Vue to vue2-sdk's node_modules to ensure single Vue instance
RUN rm -rf /vue2-sdk/node_modules/vue /vue2-sdk/node_modules/@absmartly 2>/dev/null || true && \
    mkdir -p /vue2-sdk/node_modules/@absmartly && \
    ln -sf /app/node_modules/vue /vue2-sdk/node_modules/vue && \
    ln -sf /javascript-sdk /vue2-sdk/node_modules/@absmartly/javascript-sdk

COPY cross-sdk-tests/vue2-wrapper/server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
