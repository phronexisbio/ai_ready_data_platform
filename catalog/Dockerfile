FROM python:3.12-slim

WORKDIR /app

COPY catalog/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY catalog ./catalog

EXPOSE 8000
CMD ["uvicorn", "catalog.api:app", "--host", "0.0.0.0", "--port", "8000"]
