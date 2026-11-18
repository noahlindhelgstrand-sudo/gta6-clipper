# För körning på egen server, t.ex. Oracle Cloud Always Free (ARM Ampere eller x86).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-montserrat cron tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config.yaml ./
COPY docker/crontab /etc/cron.d/clipper
RUN chmod 0644 /etc/cron.d/clipper

# Lokal lagring i en volym istället för GitHub Releases
ENV CLIPPER__STORAGE__BACKEND=local \
    CLIPPER__STORAGE__LOCAL_DIR=/data \
    CLIPPER__WORK_DIR=/data/work
VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/tini", "--"]
# cron i förgrunden; miljövariabler från `docker run --env-file .env` görs synliga för cron-jobben
CMD ["sh", "-c", "printenv | grep -E '^(TWITCH|YOUTUBE|ANTHROPIC|DISCORD|CLIPPER)' > /etc/environment && cron -f"]
