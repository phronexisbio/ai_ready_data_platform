FROM python:3.12-slim

WORKDIR /app

COPY connectors/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY connectors ./connectors

ENTRYPOINT ["python", "-m"]
