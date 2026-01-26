FROM python:3.11-slim

WORKDIR /python3-sdk
COPY python3-sdk/ ./
RUN pip install --no-cache-dir -e .

WORKDIR /app
COPY cross-sdk-tests/python-wrapper/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cross-sdk-tests/python-wrapper/server.py ./

EXPOSE 3000

CMD ["python", "server.py"]
