# Negotiating context server (see server.py). It serves br, gzip or identity;
# br needs the pinned `brotli` wheel (context-server/requirements.txt), the
# only package it installs. Readiness: the orchestrator waits on GET /health
# before its first probe.
FROM python:3.11-slim

WORKDIR /app
COPY context-server/requirements.txt ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY context-server/negotiation.py context-server/server.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
