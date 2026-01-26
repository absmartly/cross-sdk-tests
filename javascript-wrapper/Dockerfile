FROM node:18-alpine

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

WORKDIR /app
COPY cross-sdk-tests/javascript-wrapper/package*.json ./
RUN npm install

COPY cross-sdk-tests/javascript-wrapper/server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
