FROM node:18-alpine AS sdk-builder

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

EXPOSE 3000

CMD ["node", "server.js"]
