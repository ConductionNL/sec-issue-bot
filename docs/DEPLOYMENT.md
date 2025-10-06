# Deployment guide (container → GHCR → Helm → ArgoCD)

This file is meant to be copy-paste friendly for other repos.

## Container and runtime
- App: Slack Socket Mode in `socket_app.py`
- No HTTP ports exposed; outbound connection to Slack (no Service/Ingress)
- Credentials provided via environment/Secret: `OPENAI_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` (Jira optional)

Local smoke test
```bash
# Run the bot locally in a container (requires valid Slack/OpenAI secrets)
docker run --rm \
  -e OPENAI_API_KEY=... \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_APP_TOKEN=xapp-... \
  ghcr.io/conductionnl/sec-issue-bot:latest
# Watch logs for a successful Slack Socket Mode connection; Ctrl+C to stop
```

## GitHub Actions → GHCR
Workflow file: `.github/workflows/docker-publish.yml`
- Builds on branches: master, main, helm (and tags v*.*.*)
- Publishes to `ghcr.io/<owner>/sec-issue-bot`
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
          images: ghcr.io/${{ github.repository_owner }}/sec-issue-bot
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
- GitHub → Organization → Packages → sec-issue-bot → Settings → Visibility → Public
- Private alternative: docker login with a personal PAT (scope read:packages, SSO authorized)

## Helm chart (charts/sec-issue-bot)
Minimal values we used (no Service is exposed; bot runs in Slack Socket Mode)
```yaml
image:
  repository: ghcr.io/conductionnl/sec-issue-bot
  tag: latest
  pullPolicy: Always

replicaCount: 1

# Recommended: mount secrets via a Secret referenced by `secretRef`
# kubectl -n sec-issue-bot create secret generic sec-issue-bot-secrets \
#   --from-literal=OPENAI_API_KEY=... \
#   --from-literal=SLACK_BOT_TOKEN=... \
#   --from-literal=SLACK_APP_TOKEN=...
secretRef: ""

# Optional (non-secret) key/values
env:
  additional: {}
```

## Secrets
Add env secrets as kubernetes Secrets so that the app has access to them
```bash
# Create Secret with required keys
kubectl -n sec-issue-bot create secret generic sec-issue-bot-secrets \
  --from-literal=OPENAI_API_KEY='...' \
  --from-literal=SLACK_BOT_TOKEN='...' \
  --from-literal=SLACK_APP_TOKEN='...'
```


## ArgoCD (CLI)
```bash

# Create the Application (adjust dest-server for your cluster)
argocd app create sec-issue-bot \
  --project default \
  --repo https://github.com/ConductionNL/sec-issue-bot.git \
  --path charts/sec-issue-bot \
  --revision main \
  --dest-server <cluster-api-or-https://kubernetes.default.svc> \
  --dest-namespace sec-issue-bot \
  --sync-policy automated --self-heal --auto-prune \
  --helm-set image.repository=ghcr.io/conductionnl/sec-issue-bot \
  --helm-set image.tag=latest \
  --helm-set secretRef=sec-issue-bot-secrets

# Sync
argocd app sync sec-issue-bot 
```

No Service exposed
- The bot uses Slack Socket Mode and does not create a Service or Ingress.

## ArgoCD (UI)
- repoURL: `https://github.com/ConductionNL/sec-issue-bot.git`
- revision: `main` (use the branch that contains `charts/sec-issue-bot`)
- path: `charts/sec-issue-bot`
- namespace: `sec-issue-bot` (enable CreateNamespace)
- values: set `image.tag: latest` and keep `image.pullPolicy: Always`; add `secretRef` or `env.additional` as needed

If ArgoCD cannot access the repo, add it under Settings → Repositories (HTTPS with PAT or SSH), then create/sync the Application. When ArgoCD manages the app, avoid also installing it manually with Helm CLI (uninstall the manual release first to prevent ownership conflicts).

Auto-deploy with latest
- CI should push `:latest` on the default branch. Keep `image.tag: latest` and `image.pullPolicy: Always` to track the newest build.

## Troubleshooting
- denied on docker pull → package private or PAT/SSO missing
- manifest unknown → tag not published yet (rerun workflow)
- Argo error "can't evaluate field additional" → ensure `env:` is a map with `additional: {}` (don’t set `env` to a string or list)
- Argo repo not accessible → register the repo in ArgoCD Settings → Repositories (or use CLI to add)

## Quick checks
```bash
# View logs
kubectl -n sec-issue-bot logs deploy/sec-issue-bot-sec-issue-bot -f

# Inspect objects
kubectl -n sec-issue-bot get deploy,rs,pod
```
