### Security Incident Agent — Usage

This guide explains how to use the Security Incident Agent inside Slack to capture an incident and create a Jira issue.

#### Getting started
Open a Direct Message with the bot. Type `hi` to start a new thread. Read the preface, then type `start` to begin the questionnaire.

#### How it works
The bot shows all the steps that need to be taken in case of a security incident. Step 7 includes a quenionnaire that the bot will help filling out. After the user types `start`, the bot asks short questions in a thread. After the questionnaire is completed, the bot posts the report to Jira. \n
The default input mode is `story`: your answer is rewritten into concise English and shown as a proposal. Confirm with `yes`/`ok` or replace with `new <value>`. If you prefer exact wording, switch to `literal` mode with `mode literal` (or prefix a single answer with `literal: <answer>`).

#### Common commands (in the incident thread)
`help` — Show help
`status` — Show progress and the next step
`show` — Preview the current Markdown
`fields` — List fields (supports numbers like 2.1, 3.4)
`edit <field> <value>` — Change a previous answer. Example: `edit 2.1 Containment steps taken...`
`mode literal|story` — Switch input mode
`showmode` — Show current input mode
`continue` — Repeat the next question
`new <value>` — Replace the current proposal during confirmation (supports: `new story:` / `new literal:`)
`finalize` — Produce the final document
`jira` — Create a Jira issue using the current document

#### Special question: Risk assessment (2.3)
Answer with `yes` or `no`. If `yes`, provide the outcome or explanation (for example, agreements or follow-up actions). In `story` mode, the bot will propose a concise summary for confirmation.

#### Jira integration
When ready, type `jira` in the incident thread. The bot creates an issue (summary plus mapped sections) and attaches the full Markdown as `incident.md`.

#### Tips
You can reference fields by number when using `edit`. In `story` mode, confirm or replace each proposal so the bot can proceed. Use `show` anytime to see the current Markdown.

#### Finish
Type `finalize` to get the final document in the thread. Then use `jira` to create the issue and attach the document.

