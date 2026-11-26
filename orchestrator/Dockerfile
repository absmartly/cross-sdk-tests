FROM python:3.11-slim

WORKDIR /app

COPY orchestrator/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# negotiation.py is the context server's Accept-Encoding module, shared so the
# orchestrator reads recorded requests with the server's own rules.
COPY orchestrator/test_runner.py orchestrator/expected-capabilities.json context-server/negotiation.py ./

CMD ["python", "test_runner.py"]
