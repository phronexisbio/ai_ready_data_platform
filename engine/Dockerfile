FROM python:3.12-slim

WORKDIR /app

# wget: KubeRay's default readiness/liveness probes for the Ray head/worker
# pods (infra/k8s-manifests/raycluster.yaml) shell out to it; not present in
# the slim base image.
RUN apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/*

COPY engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY engine ./engine
COPY connectors/storage.py connectors/catalog_client.py ./connectors/
RUN touch ./connectors/__init__.py

ENTRYPOINT ["python", "-m"]
