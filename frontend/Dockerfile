# The Vite dev server, for docker-compose.yml.
#
# There is deliberately no production frontend image. `npm run build` output is
# copied into the API image by backend/Dockerfile, so production is one
# container serving both halves from one origin — see that file.

FROM node:22-alpine

WORKDIR /app

# Dependencies first, and into the image rather than the bind mount: compose
# keeps /app/node_modules on a volume so the host's copy — built against a
# different Node — never leaks in.
COPY package.json package-lock.json ./
RUN npm ci

COPY . ./

EXPOSE 5173

# No --host here: vite.config.ts sets `host: true`, so the server already binds
# every interface — a server on the container's loopback is one nothing outside
# the container can reach.
CMD ["npm", "run", "dev"]
