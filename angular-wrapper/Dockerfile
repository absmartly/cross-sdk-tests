FROM node:24-alpine AS sdk-builder

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

WORKDIR /angular-sdk
COPY angular-sdk/package*.json ./
COPY angular-sdk/ ./
# Build against the pinned javascript-sdk checkout, not the registry copy.
RUN npm install && \
    rm -rf node_modules/@absmartly/javascript-sdk && \
    ln -s /javascript-sdk node_modules/@absmartly/javascript-sdk && \
    npm run build

FROM sdk-builder AS tester
WORKDIR /angular-sdk
CMD ["npm", "test"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/angular-wrapper/package*.json ./
RUN npm install

# Ensure the local SDKs are properly linked in wrapper's node_modules
RUN rm -rf /app/node_modules/@absmartly/javascript-sdk /app/node_modules/@absmartly/angular-sdk 2>/dev/null || true && \
    mkdir -p /app/node_modules/@absmartly && \
    ln -sf /javascript-sdk /app/node_modules/@absmartly/javascript-sdk && \
    ln -sf /angular-sdk /app/node_modules/@absmartly/angular-sdk

# Ensure angular-sdk can resolve its dependencies
RUN rm -rf /angular-sdk/node_modules/@absmartly 2>/dev/null || true && \
    mkdir -p /angular-sdk/node_modules/@absmartly && \
    ln -sf /javascript-sdk /angular-sdk/node_modules/@absmartly/javascript-sdk && \
    rm -rf /angular-sdk/node_modules/@angular /angular-sdk/node_modules/rxjs && \
    ln -s /app/node_modules/@angular /angular-sdk/node_modules/@angular && \
    ln -s /app/node_modules/rxjs /angular-sdk/node_modules/rxjs

COPY cross-sdk-tests/angular-wrapper/server.js cross-sdk-tests/angular-wrapper/render.mjs ./
COPY cross-sdk-tests/javascript-wrapper/holdoutSelfTest.js ./

EXPOSE 3000

CMD ["node", "server.js"]
