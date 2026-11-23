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

COPY cross-sdk-tests/python-wrapper/server.py cross-sdk-tests/python-wrapper/holdout_self_test.py cross-sdk-tests/python-wrapper/switchback_self_test.py ./

FROM server AS wrapper-tests
COPY cross-sdk-tests/python-wrapper/test_switchback_self_test.py ./
RUN python -m unittest -v test_switchback_self_test && touch /wrapper-tests.passed

# The runtime image depends on wrapper-tests (through its marker), so BuildKit and buildah cannot
# skip the stage and a failing wrapper test fails the image build. Test files stay out of the runtime.
FROM server
COPY --from=wrapper-tests /wrapper-tests.passed /wrapper-tests.passed

EXPOSE 3000

CMD ["python", "server.py"]
