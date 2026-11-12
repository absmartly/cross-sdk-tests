# Negotiating context server (see server.py). Stdlib only: it serves gzip or
# identity, so it shares the orchestrator's base image and needs no pip install.
# Readiness: the orchestrator waits on GET /health before its first probe.
FROM python:3.11-slim

WORKDIR /app
COPY context-server/negotiation.py context-server/server.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
