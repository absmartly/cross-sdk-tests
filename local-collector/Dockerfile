# Local collector (see local_collector.py): stdlib only, nothing to install.
FROM python:3.11-slim

WORKDIR /app
COPY local-collector/local_collector.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "local_collector.py"]
