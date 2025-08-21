### Security Incident Agent - Step 1 (Schema + Extractor) + Slack App (Socket Mode)

This minimal app extracts incident details, asks follow-ups, and runs in Slack Socket Mode. Users interact via Direct Messages only.

#### Setup
1. Create and activate a virtual environment (optional)
2. Install dependencies:
```bash
pip install -r requirements.txt
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
python socket_app.py
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