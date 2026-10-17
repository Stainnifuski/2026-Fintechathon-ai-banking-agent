FROM node:22-alpine AS build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# TypeScript also checks DEV-only components; their public JSON fixtures are
# build inputs only and are tree-shaken from the production application.
COPY tests/fixtures/wealth_portfolio_contract_v1/ /app/tests/fixtures/wealth_portfolio_contract_v1/
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
