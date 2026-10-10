# Personal assistant: a daily Slack and Gmail briefing

The `personal-assistant` SAW-BOM profile gives a user one workspace,
`personal-assistant`, with a NemoClaw sandbox, `assistant`, that keeps a
daily briefing of their Slack and Gmail messages. Its providers are NVIDIA
(inference), Slack and Gmail; it has no web search. Asked to, the agent
updates the briefing every 5 minutes with whatever arrived since the last
update. For the whole demo, from install to the briefing, see
[the personal assistant demo](personal-assistant-demo.md).

What makes it safe to hand an agent the user's Slack and mail:

| Layer | What it does |
|---|---|
| Providers `slack`, `gmail` | Read-only profiles (`charts/governance-policy/profiles`): GET only, on the listed Slack and Gmail API paths, from `node` or `curl` only. |
| Credential refresh | The gateway holds the OAuth client secret and refresh token, and refreshes the access token before it expires (OpenShell `provider refresh`, `oauth2-refresh-token`). Slack's rotated refresh tokens are kept. |
| Placeholders | The sandbox sees `SLACK_BOT_TOKEN` and `GMAIL_ACCESS_TOKEN` as placeholders. The egress proxy puts the current token in only on requests to the profile's endpoints. |
| Harness bundle `daily-briefing` | The skill and two MCP servers (`slack-reader`, `gmail-reader`) are mounted read-only at `/sandbox/harness`. Each server declares the profile that governs it, and the installer refuses the bundle unless the gateway serves that profile and the sandbox has the provider. |

## The bundle

`harness-bundles/daily-briefing` (CI publishes it as an image) and the same
tree inline in `charts/saw-bom/harness/daily-briefing`, which the profile
uses (`harnessRef: {name: daily-briefing}`):

```
harness.yaml                    governance: slack-reader -> slack, gmail-reader -> gmail
plugin.json, mcp.json           Agent Plugins bundle: OpenClaw loads skills/ and mcp.json
skills/daily-briefing/SKILL.md  update, schedule (cron tool, every 5 min), show
mcp/slack-reader.mjs            tool new_messages: today's messages since the last call
mcp/gmail-reader.mjs            tool new_messages: today's mail since the last call
mcp/briefing-lib.mjs            stdio MCP loop and the store in /sandbox/briefing
```

The servers keep their state and the day's messages in
`/sandbox/briefing/` (`state-slack.json`, `state-gmail.json`,
`<day>/items.jsonl`); the agent writes `<day>/briefing.md`.

Harness bundles are mounted when a sandbox is created, and `nemoclaw onboard`
cannot add a mount on podman. For a NemoClaw sandbox with a `harnessRef` the
installer creates the sandbox itself, from the NemoClaw image with the
bundle mounted, and configures OpenClaw in it, as it does after onboarding.
A NemoClaw sandbox created earlier by `nemoclaw onboard` is created again once
(its `/sandbox` is not kept).

## Slack app and Google OAuth

Full setup walkthroughs — Slack app, scopes, token rotation, and the Google
OAuth client with a `gmail.readonly` refresh token — are in
[the personal assistant demo](personal-assistant-demo.md), Step 2.

What the Secrets look like:

| Secret | Keys |
|---|---|
| `slack` | With token rotation: `client_id`, `client_secret`, `refresh_token`. Without: a plain bot token in `bot_token`, used as is with no refresh. |
| `gmail` | `client_id`, `client_secret`, `refresh_token` (scope `gmail.readonly`). Access tokens last an hour; the gateway refreshes them 5 minutes before they expire. |

## Turn it on for a user

`overrides/saw-users.yaml`:

```yaml
users:
  - name: carol
    profiles:
      - personal-assistant
```

saw-users sees from the profile catalog that the sandbox needs its bundle
(`harnessRequired`) and sets `harnessEnabled` and `allowDriverConfig` for the
user. It also syncs the `slack` and `gmail` Secrets from Vault and mounts
them on the VM.

Load the keys into Vault: uncomment the `slack` and `gmail` entries in your
`values-secret` file (see `values-secret.yaml.template`) and run
`./pattern.sh make load-secrets`. They go to `secret/data/hub/slack` and
`secret/data/hub/gmail` (or under the user's `vaultPrefix`). The
`personal-assistant` profile in the portal asks for the same fields and
stores them under the user's own Vault path; for signing in and entering
the keys, see [the personal assistant demo](personal-assistant-demo.md),
Steps 5-7.

Check on the VM, as the installer's identity:

```
openshell provider refresh status gmail --workspace personal-assistant
openshell provider refresh status slack --workspace personal-assistant
```

## Demo

For the demo chat script ("Set up my daily briefing", the live update, the
token and post denials), see [the personal assistant demo](personal-assistant-demo.md),
Step 8. What that step does not show: the sandbox log's denial entries,

```
openshell logs assistant --workspace personal-assistant --source sandbox
```

## Limits

- Under the NemoClaw image's OpenClaw 2026.7.1, the bundle's MCP servers
  (`slack-reader__new_messages`, `gmail-reader__new_messages`) may silently
  NOT load — they are not in the gateway's plugin list. Verify after the
  first apply with the gateway log or `tool_search`; the briefing is then a
  template with empty Slack/Email sections until this skew is resolved.
  The schedule uses the agent's `cron` tool; if that OpenClaw has none,
  schedule the update from the OpenClaw UI's cron page.
- The egress proxy resets scoped npm URLs (`%2f`): if OpenClaw logs
  "Failed to install missing configured plugin 'slack'", disable the
  `slack` channel in `openclaw.json`
  (`"channels": {"defaults": {}, "slack": {"enabled": false}}`) and restart
  via the in-VM installer apply (`systemctl restart saw-apply.service`).
  Re-applying the installer re-creates the sandbox and wipes `/sandbox`.
- The NemoClaw image (`quay.io/rh-ai-quickstart/nemoclaw-sandbox:latest`,
  currently 0.0.110, OpenClaw 2026.7.1) has no newer release carrying
  OpenClaw 2026.9.x yet, so the bundle format's check version is not
  available.
- Running processes keep their environment: a provider attached to a running
  sandbox reaches OpenClaw after its gateway restarts, which the installer
  does when the sandbox's providers change.
- A refresh failure that needs the user (a revoked grant) shows as
  `reauthorize` in `provider refresh status`. Put a new refresh token in
  Vault; the installer configures it on its next run.
