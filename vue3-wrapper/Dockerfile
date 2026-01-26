FROM node:18-alpine

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

WORKDIR /vue3-sdk
COPY vue3-sdk/package*.json ./
COPY vue3-sdk/ ./
RUN npm install && NODE_OPTIONS=--openssl-legacy-provider npm run build

WORKDIR /app
COPY cross-sdk-tests/vue3-wrapper/package*.json ./
RUN npm install

# Ensure the local SDKs are properly linked in wrapper's node_modules
RUN rm -rf /app/node_modules/@absmartly/javascript-sdk /app/node_modules/@absmartly/vue3-sdk 2>/dev/null || true && \
    mkdir -p /app/node_modules/@absmartly && \
    ln -sf /javascript-sdk /app/node_modules/@absmartly/javascript-sdk && \
    ln -sf /vue3-sdk /app/node_modules/@absmartly/vue3-sdk

# Symlink Vue to vue3-sdk's node_modules to ensure single Vue instance
RUN rm -rf /vue3-sdk/node_modules/vue /vue3-sdk/node_modules/@absmartly 2>/dev/null || true && \
    mkdir -p /vue3-sdk/node_modules/@absmartly && \
    ln -sf /app/node_modules/vue /vue3-sdk/node_modules/vue && \
    ln -sf /javascript-sdk /vue3-sdk/node_modules/@absmartly/javascript-sdk

COPY cross-sdk-tests/vue3-wrapper/server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
