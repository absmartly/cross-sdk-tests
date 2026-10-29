FROM python:3.11-slim AS sdk-builder

# PYTHON_SDK_DIR lets local validation point at an SDK worktree in the build context.
ARG PYTHON_SDK_DIR=python3-sdk
WORKDIR /python3-sdk
COPY ${PYTHON_SDK_DIR}/ ./
RUN pip install --no-cache-dir -e .

FROM sdk-builder AS tester
RUN pip install --no-cache-dir pytest
WORKDIR /python3-sdk
CMD ["python", "-c", "import sys, pytest; code = pytest.main([\"test/\", \"-v\", \"-p\", \"no:cacheprovider\"]); sys.stdout.flush(); sys.stderr.flush(); import os; os._exit(code)"]

FROM sdk-builder AS server
WORKDIR /app
COPY cross-sdk-tests/python-wrapper/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cross-sdk-tests/python-wrapper/server.py ./

EXPOSE 3000

CMD ["python", "server.py"]
