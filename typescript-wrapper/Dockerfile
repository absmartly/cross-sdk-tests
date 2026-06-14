FROM node:20-alpine AS sdk-builder

WORKDIR /typescript-sdk
COPY typescript-sdk/package.json typescript-sdk/package-lock.json* ./
RUN npm install
COPY typescript-sdk/ ./
RUN npm run build

FROM sdk-builder AS tester
CMD ["npm", "test"]

FROM node:20-alpine AS server
WORKDIR /app
COPY --from=sdk-builder /typescript-sdk /typescript-sdk
COPY cross-sdk-tests/typescript-wrapper/package.json ./
RUN npm install
COPY cross-sdk-tests/typescript-wrapper/server.js ./
EXPOSE 3000
CMD ["node", "server.js"]
