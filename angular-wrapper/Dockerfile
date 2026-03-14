FROM node:18-alpine AS sdk-builder

WORKDIR /javascript-sdk
COPY javascript-sdk/package*.json ./
COPY javascript-sdk/ ./
RUN npm install && npm run build

WORKDIR /angular-sdk
COPY angular-sdk/package*.json ./
COPY angular-sdk/ ./
RUN npm install && npm run build

FROM sdk-builder AS tester
WORKDIR /angular-sdk
CMD ["npm", "test"]

FROM sdk-builder AS server
WORKDIR /angular-sdk
RUN node -e "const p=require('./package.json'); p.type='module'; require('fs').writeFileSync('./package.json', JSON.stringify(p,null,2))"
RUN node -e "\
const fs=require('fs'),path=require('path');\
function fix(dir){for(const f of fs.readdirSync(dir)){const fp=path.join(dir,f);if(fs.statSync(fp).isDirectory())fix(fp);else if(fp.endsWith('.js')){let c=fs.readFileSync(fp,'utf8');c=c.replace(/from '(\.[^']+)'/g,(m,p)=>p.endsWith('.js')?m:\"from '\"+p+\".js'\");fs.writeFileSync(fp,c)}}}\
fix('/angular-sdk/lib/esm');"
WORKDIR /app
COPY cross-sdk-tests/angular-wrapper/package*.json ./
RUN npm ci

# Ensure the local SDKs are properly linked in wrapper's node_modules
RUN rm -rf /app/node_modules/@absmartly/javascript-sdk /app/node_modules/@absmartly/angular-sdk 2>/dev/null || true && \
    mkdir -p /app/node_modules/@absmartly && \
    ln -sf /javascript-sdk /app/node_modules/@absmartly/javascript-sdk && \
    ln -sf /angular-sdk /app/node_modules/@absmartly/angular-sdk

# Ensure angular-sdk can resolve its dependencies
RUN rm -rf /angular-sdk/node_modules/@absmartly 2>/dev/null || true && \
    mkdir -p /angular-sdk/node_modules/@absmartly && \
    ln -sf /javascript-sdk /angular-sdk/node_modules/@absmartly/javascript-sdk && \
    ln -sf /app/node_modules/@angular /angular-sdk/node_modules/@angular 2>/dev/null || true

COPY cross-sdk-tests/angular-wrapper/server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
