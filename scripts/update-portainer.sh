#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="portainer"
IMAGE="portainer/portainer-ce:lts"
DATA_VOLUME="portainer_data"
HOST_PORT="9443"
CONTAINER_PORT="9443"

echo "=== Starting Portainer update ==="

echo "[1/5] Current version:"
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    docker exec "$CONTAINER_NAME" /portainer --version || true
else
    echo "Container does not exist yet"
fi

echo "[2/5] Pulling image..."
docker pull "$IMAGE"

echo "[3/5] Stopping container..."
if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    docker stop "$CONTAINER_NAME"
else
    echo "Container is not running"
fi

echo "[4/5] Removing container..."
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    docker rm "$CONTAINER_NAME"
else
    echo "Container does not exist"
fi

echo "[5/5] Starting new container..."
docker volume create "$DATA_VOLUME" >/dev/null

docker run -d --name "$CONTAINER_NAME" --restart=always -p "$HOST_PORT:$CONTAINER_PORT" -v /var/run/docker.sock:/var/run/docker.sock -v "$DATA_VOLUME:/data" "$IMAGE"

echo "=== Update complete ==="

sleep 3

echo "New version:"
docker exec "$CONTAINER_NAME" /portainer --version

echo "Container status:"
docker ps --filter "name=$CONTAINER_NAME"
