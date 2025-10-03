FROM python:3.13-alpine3.22

# Build dependencies
RUN apk add gcc musl-dev linux-headers

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy over code
COPY . /app
WORKDIR /app

# Install python dependencies
RUN uv sync --locked

CMD ["uv", "run", "libqretprop/server.py"]