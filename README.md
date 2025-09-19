### Security Incident Agent - Step 1 (Schema + Extractor) + Slack App (Socket Mode)

This minimal app extracts incident details, asks follow-ups, and runs in Slack Socket Mode. Users interact via Direct Messages only.

#### Setup
1. (Optional) Create and activate a virtual environment
2. Install dependencies with uv:
```bash
# install uv (if not installed yet)
curl -LsSf https://astral.sh/uv/install.sh | sh
# install project dependencies
uv sync
```
3. Provide secrets in `.env`:
```bash
OPENAI_API_KEY=sk-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...   # Socket Mode app-level token with connections:write
# Jira (optional, for issue creation)
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-api-token
JIRA_PROJECT_KEY=SEC
JIRA_ISSUE_TYPE=Task
```

#### Run (Socket Mode)
```bash
uv run python socket_app.py
```
- In your Slack app config:
  - Enable Socket Mode
  - Create an App-Level Token with `connections:write` → set as `SLACK_APP_TOKEN`
  - Add Bot Token scopes: `chat:write`, `channels:history`, `im:history`
  - Install the app to your workspace and copy the `xoxb-...` Bot Token

#### Use in Slack (Direct Messages only)
- Send the bot a DM with a short incident description.
- The bot will ask follow-up questions in a thread.
- Reply in the same thread.
- Type `finaliseer` to output the final Markdown document.
- Type `jira` to create a Jira issue with the Markdown as the issue description (and attached as `incident.md`).

#### What this includes
- Pydantic schema for the incident template fields
- OpenAI-backed extractor with structured JSON output
- Slack app (Socket Mode) handlers wired to the extractor and renderer
- Minimal Jira REST integration to create issues and attach the generated Markdown

This app runs via Slack Socket Mode; no public HTTP endpoint is needed.

### Deployment

This repository includes a build-and-publish workflow to GHCR and a Helm chart for Kubernetes, modeled after the deployment guide in `docs/DEPLOYMENT.md` but adapted for a Slack Socket Mode bot (no Service/Ingress).

#### 1) Build and publish container (GitHub Actions → GHCR)
- Push to `main`, `master`, or `helm` or trigger the workflow manually in GitHub Actions: `Publish Docker image to GHCR`.
- The image will be published to `ghcr.io/<owner>/sec-issue-bot` with semver, branch, SHA, and `latest` (on the default branch) tags.
- If you want the cluster to pull without credentials, set the GHCR package to Public under the repo owner’s Packages → `sec-issue-bot` → Settings → Visibility.

Private images: create an imagePullSecret and reference it in Helm values.
```bash
kubectl -n <namespace> create secret docker-registry ghcr-creds \
  --docker-server=ghcr.io \
  --docker-username=<github-username> \
  --docker-password=<personal-access-token-with-read:packages>
```

#### 2) Kubernetes secrets (required)
Create a Secret with your runtime credentials. Minimum required keys:
```bash
kubectl -n <namespace> create secret generic sec-issue-bot-secrets \
  --from-literal=OPENAI_API_KEY=... \
  --from-literal=SLACK_BOT_TOKEN=xoxb-... \
  --from-literal=SLACK_APP_TOKEN=xapp-... \
  --from-literal=JIRA_URL=https://your-domain.atlassian.net \
  --from-literal=JIRA_EMAIL=you@example.com \
  --from-literal=JIRA_API_TOKEN=...
```
Optional keys: `JIRA_PROJECT_KEY`, `JIRA_ISSUE_TYPE`.

#### 3) Helm install/upgrade
The chart is located at `charts/sec-issue-bot`. Because this app uses Slack Socket Mode, it does not expose a Service or Ingress.
```bash
helm upgrade --install sec-issue-bot charts/sec-issue-bot \
  --namespace <namespace> --create-namespace \
  --set image.repository=ghcr.io/<owner>/sec-issue-bot \
  --set image.tag=latest \
  --set secretRef=sec-issue-bot-secrets

# If your GHCR image is private, also set imagePullSecrets
# --set imagePullSecrets='[{name: ghcr-creds}]'
```

Logs:
```bash
kubectl -n <namespace> logs deploy/<release>-sec-issue-bot
```

#### 4) ArgoCD (optional)
Configure an Application pointing to this repo and path `charts/sec-issue-bot`.
- repoURL: `https://github.com/ConductionNL/sec-issue-bot.git`
- revision: your branch (e.g., `main`)
- path: `charts/sec-issue-bot`
- namespace: your target namespace (enable Create Namespace)
- values: set `image.repository`, `image.tag` and `secretRef`; if private image, set `imagePullSecrets`.

Example values override:
```yaml
image:
  repository: ghcr.io/ConductionNL/sec-issue-bot
  tag: latest
secretRef: sec-issue-bot-secrets
# imagePullSecrets:
#   - name: ghcr-creds
```

Notes
- The container runs `python -u socket_app.py` and establishes an outbound websocket to Slack (no inbound HTTP).
- Ensure egress to Slack is permitted by your cluster/network policies.