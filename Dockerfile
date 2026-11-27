# DoNotMix: engine + dashboard + official Flagsmith CLI + MixMart demo
FROM ghcr.io/flagsmith/flagsmith-cli:v2.0.0 AS flagsmith-cli

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git jq ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=flagsmith-cli /usr/local/bin/flagsmith /usr/local/bin/flagsmith
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install -e ".[demo]" pytest
COPY . .
RUN useradd -m donotmix && mkdir -p /state && chown -R donotmix /app /state
USER donotmix
EXPOSE 8765
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["dashboard"]
