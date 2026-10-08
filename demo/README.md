# Demo options

Two ways to demo the Secure Agent Workspace. Both use the same platform
install; they differ in guardrail depth and provider strategy.

## Option A: daily briefing (external Slack + Gmail)

Step-by-step walkthrough: [personal-assistant-demo.md](personal-assistant-demo.md)
(how the briefing works under the hood: [daily-briefing.md](daily-briefing.md)).

- From an empty OpenShift cluster to an agent that keeps a daily briefing of
  one user's Slack and Gmail.
- SAW-BOM `personal-assistant` profile: one NemoClaw sandbox with the
  `daily-briefing` harness bundle (a skill plus the `slack-reader` and
  `gmail-reader` MCP servers), NVIDIA inference, read-only Slack and Gmail
  providers, and token placeholders the agent never resolves.
- Self-service via the Red Hat Developer Hub portal: the user `dana` picks
  the profile, enters her keys, and gets her own workspace.
- Proves the platform plus the portal story. Plan about 30 minutes of
  demo-account setup (Slack app, Google Cloud OAuth client) before the run.

## Option B: fully on-cluster GTC arc

Runbook: [gtc-runbook.md](gtc-runbook.md).

- A guided arc that proves the security controls end to end with zero
  external dependencies: Mattermost chat, Mailpit email and Radicale
  calendar all run on the cluster.
- Beats: PTO catch-up across chat and mail, a governance block that denies
  the calendar provider, VM containment of the agent, a policy-as-data
  capability grant (calendar profile), and workspace provisioning.
- The grant is the story: the interceptor loads profiles at startup, the
  new capability arrives as data (a YAML file), and the same request that
  was denied is allowed after the sync.

## How they differ

- Same platform: one pattern install, Keycloak users, NemoClaw sandboxes,
  the interceptor enforcing policy on every provider call.
- Option A points the providers at external services (Slack, Gmail) and
  shows the self-service portal; it proves platform + portal.
- Option B keeps everything on-cluster and walks the guardrails live
  (deny, contain, grant, provision); it proves the controls.

## Charts

The demo charts are standalone helm installs from this directory:

```
helm install mailpit demo/charts/mailpit -n openshell-agents
helm install radicale demo/charts/radicale -n openshell-agents
helm install mattermost demo/charts/mattermost -n openshell-agents
```

On OpenShift with restricted-v2, Mattermost may need the service account
anyuid grant; the charts already set the security contexts and probe delays
for that case.

## Profiles

The governance profiles for the on-cluster providers are copied here as the
portable artifacts-to-place: `mailpit.yaml`, `mattermost.yaml`,
`calendar.yaml`. Copy them into `charts/governance-policy/profiles/` BEFORE
deploying the sandbox (the interceptor loads profiles at startup), or after
deployment and let Argo auto-sync.

- For EXISTING sandboxes, the sync is not enough: use the two-step
  `provider create` + `sandbox provider attach` (see the runbook).
- If the new capability is still denied after the sync, restart the
  interceptor pod: profile hot-reload does not always fire.
- In Option B, `calendar.yaml` is the live beat-4 commit: leave it out of
  the pre-placed set if you want the genuine "this capability was not there
  before" moment.

The copies in `charts/governance-policy/profiles/` are the live source the
cluster's Argo self-heals from; keep them in place.
