# Polaris AI frontend — Next.js 15 dev image (compose mounts source for HMR).
FROM node:22-alpine

WORKDIR /app

# Install deps first for layer caching (lockfile optional on first build).
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
