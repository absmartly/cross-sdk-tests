FROM node:18-alpine

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

WORKDIR /react-sdk
COPY react-sdk/package*.json react-sdk/yarn.lock* ./
COPY react-sdk/ ./
RUN yarn install && yarn build

WORKDIR /app
COPY cross-sdk-tests/react-wrapper/package*.json ./
RUN npm install

# Symlink React to react-sdk's node_modules to ensure single React instance
RUN rm -rf /react-sdk/node_modules/react /react-sdk/node_modules/react-dom && \
    ln -s /app/node_modules/react /react-sdk/node_modules/react && \
    ln -s /app/node_modules/react-dom /react-sdk/node_modules/react-dom

COPY cross-sdk-tests/react-wrapper/server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
