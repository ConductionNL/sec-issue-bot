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
  ghcr.io/conductionnl/sec-issue-bot:sha-<shortsha>
# Watch logs for a successful Slack Socket Mode connection; Ctrl+C to stop
```

## GitHub Actions → GHCR
Workflow file: `.github/workflows/docker-publish.yml`
- Trigger: after CI succeeds on `main` (via `workflow_run`) and on manual `workflow_dispatch`
- Publishes to `ghcr.io/<owner>/sec-issue-bot`
- Immutable tags: pushes `sha-<shortsha>` (and also `latest` for convenience)
- GitOps bump: updates `charts/sec-issue-bot/values.yaml` to the new immutable tag and enforces `image.pullPolicy: IfNotPresent`

Full workflow
```yaml
name: Publish Docker image to GHCR

on:
  # Gate publish on CI success on main branch
  workflow_run:
    workflows: [ "CI" ]
    types: [ completed ]
  workflow_dispatch:

jobs:
  build-and-push:
    # Only run after CI succeeded on main
    if: ${{ github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_branch == 'main' }}
    runs-on: ubuntu-latest
    permissions:
      contents: write
      packages: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_sha }}

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute tags
        id: prep
        shell: bash
        run: |
          SHORT_SHA=$(echo "${{ github.event.workflow_run.head_sha }}" | cut -c1-7)
          OWNER_LC=$(echo "${{ github.repository_owner }}" | tr '[:upper:]' '[:lower:]')
          echo "IMAGE=ghcr.io/${OWNER_LC}/sec-issue-bot" >> $GITHUB_ENV
          echo "TAGS=ghcr.io/${OWNER_LC}/sec-issue-bot:sha-${SHORT_SHA},ghcr.io/${OWNER_LC}/sec-issue-bot:latest" >> $GITHUB_ENV

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile
          push: true
          tags: ${{ env.TAGS }}
          labels: org.opencontainers.image.revision=${{ github.event.workflow_run.head_sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Prepare main branch for image tag bump
        if: ${{ github.event.workflow_run.head_branch == 'main' }}
        run: |
          git fetch origin main
          git checkout -B main origin/main

      - name: Compute immutable tag
        if: ${{ github.event.workflow_run.head_branch == 'main' }}
        shell: bash
        run: |
          SHORT_SHA=$(echo "${{ github.event.workflow_run.head_sha }}" | cut -c1-7)
          echo "TAG=sha-${SHORT_SHA}" >> $GITHUB_ENV

      - name: Bump Helm image.tag to immutable SHA
        if: ${{ github.event.workflow_run.head_branch == 'main' }}
        uses: mikefarah/yq@v4
        env:
          TAG: ${{ env.TAG }}
        with:
          cmd: yq -i '.image.tag = strenv(TAG)' charts/sec-issue-bot/values.yaml

      - name: Ensure image.pullPolicy is IfNotPresent
        if: ${{ github.event.workflow_run.head_branch == 'main' }}
        uses: mikefarah/yq@v4
        with:
          cmd: yq -i '.image.pullPolicy = "IfNotPresent"' charts/sec-issue-bot/values.yaml

      - name: Commit and push image tag bump
        if: ${{ github.event.workflow_run.head_branch == 'main' }}
        run: |
          git config user.name "${GITHUB_ACTOR}"
          git config user.email "${GITHUB_ACTOR}@users.noreply.github.com"
          if git diff --quiet --exit-code charts/sec-issue-bot/values.yaml; then
            echo "No changes to commit"
            exit 0
          fi
          git add charts/sec-issue-bot/values.yaml
          SHORT_SHA=$(echo "${{ github.event.workflow_run.head_sha }}" | cut -c1-7)
          git commit -m "chore: bump image tag to sha-${SHORT_SHA} [skip ci]"
          git push
```

### Immutable deployments
- CI bumps `charts/sec-issue-bot/values.yaml` `image.tag` to `sha-<shortsha>` on every successful `main` build.
- Argo CD syncs to Git and deploys the exact immutable image.
- No rollout annotations needed; a new ReplicaSet is created when the tag in Git changes.


Make GHCR package public
- GitHub → Organization → Packages → sec-issue-bot → Settings → Visibility → Public
- Private alternative: docker login with a personal PAT (scope read:packages, SSO authorized)

## Helm chart (charts/sec-issue-bot)
Minimal values we used (no Service is exposed; bot runs in Slack Socket Mode)
```yaml
image:
  repository: ghcr.io/conductionnl/sec-issue-bot
  tag: sha-<shortsha>  # Set automatically by CI bump step
  pullPolicy: IfNotPresent  # Immutable tags don't need Always

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
- values: avoid overriding `image.*`; set `secretRef` (and/or `env.additional`) if needed. Git manages the image tag.

If ArgoCD cannot access the repo, add it under Settings → Repositories (HTTPS with PAT or SSH), then create/sync the Application. When ArgoCD manages the app, avoid also installing it manually with Helm CLI (uninstall the manual release first to prevent ownership conflicts).

Notes
- CI pushes `sha-<shortsha>` (immutable) and `latest`. Deployments track the immutable tag stored in Git.

## Troubleshooting
- denied on docker pull → package private or PAT/SSO missing
- manifest unknown → tag not published yet (rerun workflow)
- Argo error "can't evaluate field additional" → ensure `env:` is a map with `additional: {}` (don’t set `env` to a string or list)
- Argo repo not accessible → register the repo in ArgoCD Settings → Repositories (or use CLI to add)
- Old pods not restarting → remove ArgoCD Helm parameter overrides for `image.tag`/`image.pullPolicy` to allow Git values to take effect

## Quick checks
```bash
# View logs
kubectl -n sec-issue-bot logs deploy/sec-issue-bot-sec-issue-bot -f

# Inspect objects
kubectl -n sec-issue-bot get deploy,rs,pod

# Check current image tag and pull policy
kubectl -n sec-issue-bot get deploy sec-issue-bot-sec-issue-bot \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}{.spec.template.spec.containers[0].imagePullPolicy}{"\n"}'
```
