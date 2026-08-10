FROM node:22-alpine AS build
WORKDIR /workspace
COPY admin/package.json admin/package-lock.json ./
RUN npm ci
COPY admin/ ./
RUN npm run build -- --outDir /dist --emptyOutDir

FROM nginxinc/nginx-unprivileged:1.28-alpine AS runtime
COPY deploy/nginx/admin.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dist /usr/share/nginx/html
EXPOSE 8080
