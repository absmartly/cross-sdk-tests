FROM python:3.11-slim AS sdk-builder

WORKDIR /python3-sdk
COPY python3-sdk/ ./
RUN pip install --no-cache-dir -e .

FROM sdk-builder AS tester
RUN pip install --no-cache-dir pytest
WORKDIR /python3-sdk
CMD ["python", "-m", "pytest", "test/", "-v", "-p", "no:cacheprovider"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/python-wrapper/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cross-sdk-tests/python-wrapper/server.py ./

EXPOSE 3000

CMD ["python", "server.py"]
