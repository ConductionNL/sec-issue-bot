# Deployment guide (container → GHCR → Helm → ArgoCD)

This file is meant to be copy-paste friendly for other repos.

## Container and runtime
- App: FastAPI in `app.py`
- Container listens on port 80 (non-root, NET_BIND_SERVICE); no secrets in env
- Credentials (Solr, Fireworks) are sent in request body, not env

Local smoke test
```bash
# run image: host 8000 → container 80
docker run --rm -p 8000:80 ghcr.io/conductionnl/docurag:helm
curl -f http://localhost:8000/openapi.json
```

## GitHub Actions → GHCR
Workflow file: `.github/workflows/docker-publish.yml`
- Builds on branches: master, main, helm (and tags v*.*.*)
- Publishes to `ghcr.io/<owner>/docurag`
- Adds `:latest` on default branch (master/main)
- Has manual `workflow_dispatch`

Snippet
```yaml
name: Publish Docker image to GHCR
on:
  push:
    branches: [ "master", "main", "helm" ]
    tags: [ 'v*.*.*' ]
  workflow_dispatch:
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository_owner }}/docurag
          tags: |
            type=ref,event=branch
            type=ref,event=tag
            type=sha
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Make GHCR package public
- GitHub → Organization → Packages → docurag → Settings → Visibility → Public
- Private alternative: docker login with a personal PAT (scope read:packages, SSO authorized)

PAT login (private images)
```bash
echo <PAT> | docker login ghcr.io -u <GITHUB_USERNAME> --password-stdin
```

## Helm chart (charts/docusearch)
Minimal values for in-cluster usage (ClusterIP, no Ingress)
```yaml
image:
  repository: ghcr.io/conductionnl/docurag
  tag: helm
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80
  targetPort: 80

env:
  UVICORN_PORT: "80"
  LOG_LEVEL: INFO
  TOP_K: "5"
  MODEL_NAME: intfloat/multilingual-e5-small
  LLM_NAME: llama-v3p3-70b-instruct

ingress:
  enabled: false
```

Install/upgrade
```bash
helm upgrade --install docusearch charts/docusearch \
  --namespace docurag --create-namespace \
  --set image.repository=ghcr.io/conductionnl/docurag \
  --set image.tag=helm
```

In-cluster DNS
- Service name: `<release>-docusearch` (e.g., `docusearch-docusearch`)
- URL: `http://<service>.<namespace>.svc` (port 80)

## ArgoCD (UI)
- repoURL: `https://github.com/ConductionNL/DocuRAG.git`
- revision: `helm`
- path: `charts/docusearch`
- namespace: `docurag` (CreateNamespace)
- values: see Helm section above

Auto-deploy with latest
- Workflow adds `:latest` on default branch; set `image.tag: latest` when you want to track default branch builds

## Troubleshooting
- denied on docker pull → package private or PAT/SSO missing
- manifest unknown → tag not published yet (rerun workflow)
- connection refused/reset → port mismatch; ensure `UVICORN_PORT=80` and Service targetPort=80
- no Service endpoints → pod not ready or selector/name mismatch
- cross-namespace timeout → check NetworkPolicy

## Quick smoke tests (cluster)
```bash
# same namespace
kubectl -n docurag run curl --rm -it --image=curlimages/curl:8.10.1 -- \
  curl -sf http://docusearch-docusearch/openapi.json

# from another namespace (e.g., test-mcc)
kubectl -n test-mcc run curl --rm -it --image=curlimages/curl:8.10.1 -- \
  curl -sf http://docusearch-docusearch.docurag.svc/openapi.json
```
