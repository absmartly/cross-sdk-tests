FROM python:3.11-slim

WORKDIR /app

COPY orchestrator/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/test_runner.py ./

CMD ["python", "test_runner.py"]
