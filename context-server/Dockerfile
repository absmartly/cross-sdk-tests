# Negotiating context server (see server.py). Python 3.14 ships zstd in the
# standard library; brotli is the one extra dependency, so all four codings
# (zstd, br, gzip, identity) can be served.
FROM python:3.14-slim

WORKDIR /app
RUN pip install --no-cache-dir brotli==1.2.0
COPY context-server/server.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
