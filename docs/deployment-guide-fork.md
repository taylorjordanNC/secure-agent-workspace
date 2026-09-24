# Secure Agent Workspace Fork Deployment Guide

## Scope

This guide deploys the emulation-fix fork, not the original validated-patterns
repository:

```text
https://github.com/taylorjordanNC/secure-agent-workspace
branch: saw-emulation-fixes
```

The fork is based on the validated-patterns Secure Agent Workspace and is intended
for OpenShift Virtualization clusters where KubeVirt may have to use QEMU software
emulation, such as RHDP CNV or AWS instances without `/dev/kvm`.

The fork includes these deployment changes:

| File | Fork behavior |
|---|---|
| `charts/openshell-saw/values.yaml` | Sets the setup Job deadline to `5400` seconds instead of `1800`. |
| `charts/openshell-saw/templates/cloudinit-sandbox.yaml` | Uses `OPENSHELL_GATEWAY_CONFIG` and configures the Docker driver with the local supervisor binary. |
| `scripts/oidc-login.sh` | Sends PKCE parameters for device-code login and uses longer HTTP timeouts. |
| `charts/saw-bom/profiles/data-science/default/sandbox.yaml` | Enables `notebook`, `cuda-sandbox`, and `toolbox`. |
| `charts/saw-bom/profiles/data-science/cuda-dev/sandbox.yaml` | Enables `cuda-sandbox` and `toolbox`. |

The original repository has a separate guide at
[`deployment-guide-taylor.md`](deployment-guide-taylor.md). Do not apply the
original guide's manual gateway crash-loop repair to this fork unless the deployed
templates do not match this branch.

## Control Node

Use one control node for cluster administration, image mirroring, and VM SSH. The
SSH private key and `virtctl` must be on the same machine. A macOS laptop is also
the easiest place to complete the browser-based login.

Required local tools are:

```text
oc, helm, podman, virtctl, openshell, git, jq, openssl, curl, python3, make
```

`pattern.sh` requires Podman. The Validated Patterns utility container supplies the
framework's `make`, `oc`, and Helm dependencies, but Podman must exist locally.

### RHEL or Fedora

```bash
sudo dnf update -y
sudo dnf install -y git make podman python3-pip python3-pyyaml jq openssl curl wget tar gzip gettext

# Install the OpenShift CLI from the matching client release if it is not present.
wget https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/openshift-client-linux.tar.gz
sudo tar -zxvf openshift-client-linux.tar.gz -C /usr/local/bin/

curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
```

Install `virtctl` from the cluster's console client download after logging in, so
the client version matches the cluster:

```bash
URL=$(oc get consoleclidownload virtctl-clidownloads-kubevirt-hyperconverged \
  -o jsonpath='{.spec.links[*].href}' | tr ' ' '\n' | grep -i linux | grep -iE 'amd64|x86_64' | head -1)
curl -L -k "$URL" -o /tmp/virtctl.tar.gz
tar xzf /tmp/virtctl.tar.gz -C /tmp
sudo install -m 0755 /tmp/virtctl /usr/local/bin/virtctl
```

### macOS

```bash
brew install openshift-cli helm podman jq openssl gettext python3 kubernetes-cli krew
brew tap nvidia/openshell && brew install openshell
export PATH="$(brew --prefix gettext)/bin:$PATH"
kubectl krew install virt
ln -sf ~/.krew/bin/kubectl-virt ~/.krew/bin/virtctl
hash -r
podman machine init 2>/dev/null || true
podman machine start 2>/dev/null || true
```

For macOS, `make copy-images` automatically uses in-cluster Skopeo Jobs rather
than uploading large layers through the Podman machine.

Verify the tools:

```bash
oc version --client
helm version
podman --version
virtctl version --client
openshell --version
```

## Cluster Prerequisites

Use a multi-node OpenShift 4.22+ cluster with at least 16 CPUs and 32 GiB of
memory for a useful emulation test. Software emulation is considerably slower than
hardware virtualization. Cluster-admin access is required for the initial install.

Log in before running cluster commands:

```bash
oc login https://api.<cluster>:6443 -u <admin-user>
oc whoami
```

The fork installs OpenShift Virtualization through GitOps. Do not install the
operator manually from OperatorHub when the pattern will install it; doing both
creates duplicate OperatorGroups and can leave the CSV in
`TooManyOperatorGroups`.

If the cluster has no hardware KVM, enable supported HCO software emulation after
the OpenShift Virtualization operator is available. Set the JSON patch in a
variable to avoid a pasted newline in the annotation:

```bash
PATCH='[{"op":"add","path":"/spec/configuration/developerConfiguration/useEmulation","value":true}]'
oc annotate hco kubevirt-hyperconverged -n openshift-cnv \
  "kubevirt.kubevirt.io/jsonpatch=$PATCH" --overwrite

oc get kubevirt kubevirt-kubevirt-hyperconverged -n openshift-cnv \
  -o jsonpath='{.spec.configuration.developerConfiguration.useEmulation}{"\n"}'
```

Without this setting, the VM does not boot on emulation-only nodes and the setup
Job eventually fails with a VM readiness or backoff error.

## Clone and Configure

Clone the fork and its deployment branch:

```bash
mkdir -p ~/git
cd ~/git
git clone --branch saw-emulation-fixes \
  https://github.com/taylorjordanNC/secure-agent-workspace.git
cd secure-agent-workspace
```

Generate the SSH keypair. Keep the private key on this control node; the cluster
receives only the public key through the pattern secret flow.

```bash
make generate-keys
```

Create the secret values file outside the repository and edit it:

```bash
cp values-secret.yaml.template ~/values-secret.yaml
```

Configure at least one inference provider. The provider name for NVIDIA Build is
`build`, not `nvidia`, and the model must be a real model identifier. For example:

```yaml
- name: inference
  fields:
  - name: provider
    value: build
  - name: model
    value: nvidia/nemotron-3-super-120b-a12b
  - name: api_key
    path: ~/.ngc-api-key
```

Use exactly one of `value` or `path` for `api_key`. Do not set both. The supported
provider identifiers in the template are `gemini`, `anthropic`, `openai`, `build`,
`openrouter`, `ollama`, and `custom`.

Create the referenced key file if using `path`, and ensure the path is readable by
the user running `pattern.sh`. Never commit `~/values-secret.yaml`, provider key
files, or the generated private key.

## Deploy

Mirror the pre-built gateway, supervisor, NemoClaw, and related images into the
cluster registry:

```bash
make copy-images
```

The fork's branch must be available on the remote used by the Validated Patterns
utility container. Deploy the pattern from the repository root:

```bash
./pattern.sh make install
```

The install creates the operators, Vault and ESO integration, Keycloak, governance
resources, the gateway VM, and the BOM setup Job. It also loads the secret values.
Do not run the manual OperatorGroup block printed by `make check-prereqs` if the
RHBK operator is temporarily not visible. `values-prod.yaml` already declares the
`openshell-agents` OperatorGroup. Two OperatorGroups in that namespace cause the
RHBK CSV to fail.

Monitor the pattern and setup resources. Emulation can take tens of minutes:

```bash
oc get applications -n vp-gitops
oc get dv,vm,vmi,job -n openshell-agents
oc logs -f job/openshell-saw-setup -n openshell-agents
```

Unlike the original repository, a normal emulation deployment of this fork should
not need a manual gateway configuration edit. The fork gives the setup Job 90
minutes and cloud-init writes:

```text
OPENSHELL_GATEWAY_CONFIG=/etc/openshell/gateway.toml
[openshell.drivers.docker]
supervisor_bin = "/usr/local/bin/openshell-supervisor"
```

These settings prevent the gateway from ignoring its TOML configuration and from
pulling the broken mutable `:dev` supervisor image.

## Configure OIDC and the CLI

> **Pin the client to `0.0.103` — this applies to every command in this guide.**
> Each `openshell` call here, *including the ones run inside `make` targets*
> (`make login`, `make *-configure-gateway`, `make governance-*`,
> `make nemoclaw-tui`, etc.), uses whichever `openshell` is first on your `PATH`.
> The gateway is pinned to `0.0.103-rhaiv.0`, and a newer client (for example the
> Homebrew build, currently `0.0.116`) can silently stall during the login
> handshake. Keep `openshell` resolving to `0.0.103` for all work, or results will
> drift as the Homebrew formula updates.

Confirm the in-VM gateway version:

```bash
virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
  --identity-file="$HOME/.generated-ssh-keys/sandbox-ssh" \
  --local-ssh-opts="-o StrictHostKeyChecking=no" \
  --command "openshell-gateway --version"     # expect 0.0.103-rhaiv.0
```

Install the matching client to a stable location (Apple Silicon shown; on Linux
use `openshell-x86_64-unknown-linux-musl.tar.gz` or
`openshell-aarch64-unknown-linux-musl.tar.gz` from the same release):

```bash
mkdir -p ~/openshell-cli
cd ~/openshell-cli
curl -sSL -o openshell.tar.gz \
  https://github.com/NVIDIA/OpenShell/releases/download/v0.0.103/openshell-aarch64-apple-darwin.tar.gz
tar xzf openshell.tar.gz
xattr -d com.apple.quarantine ./openshell 2>/dev/null || true
```

Make it the default `openshell` **durably** so new terminals (and next week) still
use it. Prepending to `PATH` in your shell rc wins over `/opt/homebrew/bin`:

```bash
echo 'export PATH="$HOME/openshell-cli:$PATH"' >> ~/.zshrc   # or ~/.bashrc
export PATH="$HOME/openshell-cli:$PATH"                       # apply to this shell now
hash -r                                                       # forget any cached path
```

**Verify before you rely on it — do this in each shell/session you test from:**

```bash
which openshell        # -> $HOME/openshell-cli/openshell  (NOT /opt/homebrew/bin/openshell)
openshell --version    # -> openshell 0.0.103
```

If `which openshell` still shows the Homebrew path, your rc did not load in this
shell (open a new terminal or re-run the `export`/`hash -r` lines). To remove the
ambiguity entirely you may instead `brew uninstall openshell`, or pin explicitly
per command with the full path `~/openshell-cli/openshell ...`. Do **not** mix
versions within a session — in particular, always run `gateway login` / `gateway
add` with the `0.0.103` client.

Log in with the fork's OIDC helper. Browser flow is suitable for a laptop:

```bash
export OPENSHELL_SAW_NAME=openshell-saw
make login OIDC_FLOW=browser
make openshell-saw-configure-gateway
```

The configure target extracts the gateway CA with `virtctl`, registers the route
with the Keycloak OIDC issuer, selects the gateway, and copies the locally cached
OIDC token when available. The SSH key and `virtctl` must remain on the same host.

For a remote bastion, the fork's `scripts/oidc-login.sh` includes the required
PKCE challenge and verifier for Keycloak's device flow:

```bash
make login OIDC_FLOW=device-code
make openshell-saw-configure-gateway
```

The device-code URL can be opened in a browser on another machine. If browser flow
is used remotely instead, forward the callback port or replay the exact callback
URL printed by the browser against the bastion before it expires.

## Validate

> Every `openshell` command below (and in the Governance section) uses the client
> on your `PATH`. Before testing, confirm it is the pinned `0.0.103`:
> `which openshell && openshell --version` — see
> [Configure OIDC and the CLI](#configure-oidc-and-the-cli) if it is not.

First check that the gateway is reachable and the BOM was applied:

```bash
openshell sandbox list
openshell sandbox list --workspace cuda-dev
```

`sandbox list` queries only one workspace at a time, defaulting to `default`.
It does not aggregate across workspaces, so the `cuda-dev` sandboxes are
invisible until you pass `--workspace cuda-dev`. An empty result from the bare
command is therefore expected while the CUDA instances live in `cuda-dev`.

Expected output once the setup Job has finished applying the BOM — the `default`
workspace lists three sandboxes, all in phase `Ready`:

```text
NAME          CREATED              PHASE
notebook      2026-09-18 20:05:46  Ready
cuda-sandbox  2026-09-18 20:17:00  Ready
toolbox       2026-09-18 20:26:31  Ready
```

And `--workspace cuda-dev` lists the remaining two, also `Ready`:

```text
NAME          CREATED              PHASE
cuda-sandbox  ...                  Ready
toolbox       ...                  Ready
```

What the phases mean and what to do about them:

- **`Ready`** — the sandbox container is up and the gateway can schedule work to
  it. This is the target state for all five entries.
- **`Pending` / `Creating`** — the setup Job (or a manual create) is still
  pulling images and starting the container. Under QEMU software emulation this
  can take several minutes per sandbox; re-run the list command until it settles.
- **Empty list on the `default` workspace but sandboxes exist** — either you did
  not pass `--workspace`, or the setup Job died before its BOM-apply phase (see
  Troubleshooting). Confirm the Job completed before assuming a sandbox is missing.
- **`Error: ... invalid token: ExpiredSignature`** — the sandboxes are fine; your
  cached OIDC token expired. Re-authenticate without re-registering the gateway:

  ```bash
  openshell gateway login openshell-saw --gateway-insecure
  ```

  This opens the browser for the Keycloak login and stores a fresh token; the
  list commands then succeed. Do not use `gateway add` to fix an expired token —
  it fails with `Gateway 'openshell-saw' already exists`, which is expected and
  is not the problem.

The fork enables five sandbox entries across two workspaces:

| Workspace | Sandbox | Type | Created by the setup Job |
|---|---|---|---|
| `default` | `notebook` | `openclaw` | Yes |
| `default` | `cuda-sandbox` | `nemoclaw` | Yes |
| `default` | `toolbox` | `generic` | Yes |
| `cuda-dev` | `cuda-sandbox` | `nemoclaw` | Yes |
| `cuda-dev` | `toolbox` | `generic` | Yes |

The name `cuda-sandbox` is present once in each workspace; always pass
`--workspace cuda-dev` when selecting the CUDA development instance.

Launch the agents:

```bash
# NemoClaw in the cuda-dev workspace
OPENSHELL_SAW_NAME=openshell-saw \
SANDBOX_NAME=cuda-sandbox \
WORKSPACE=cuda-dev \
make nemoclaw-tui

# OpenClaw in the default workspace
OPENSHELL_SAW_NAME=openshell-saw \
SANDBOX_NAME=notebook \
make openclaw-tui

# OpenClaw web UI
OPENSHELL_SAW_NAME=openshell-saw \
SANDBOX_NAME=notebook \
GUI_PORT=18790 \
make openclaw-gui
```

What to expect in the terminal:

- **TUI targets (`nemoclaw-tui`, `openclaw-tui`)** first resolve the sandbox
  (using `SANDBOX_NAME`, or the first sandbox in the workspace if unset) and
  print a connect line, then hand the terminal over to the interactive agent:

  ```text
  Connecting to sandbox 'cuda-sandbox' workspace 'cuda-dev' (nemoclaw)...
  ```

  The prompt does not return — you are now inside the NemoClaw/OpenClaw TUI
  running in the sandbox over an `ssh-proxy` tunnel. Exit the agent (or press
  `Ctrl-C`) to drop back to your shell. If the workspace has no sandbox you get
  `Error: No sandboxes found on gateway '...' workspace '...'.` instead.

- **Web UI targets (`nemoclaw-gui`, `openclaw-gui`, `openshell-saw-gui`)** fetch
  the dashboard token out of the sandbox, then open a local port-forward and
  block. Expected output:

  ```text
  Fetching dashboard token...

  OpenClaw UI: http://localhost:18790/#token=<hex-token>
  Press Ctrl-C to stop.
  ```

  Open that `http://localhost:<GUI_PORT>/#token=...` URL in a browser on the same
  host — the `#token=` fragment authenticates the session, so use the whole URL.
  The command stays in the foreground running the `ssh -N -L` tunnel (no prompt
  returns and no further output is normal); press `Ctrl-C` to tear down the
  forward. If it prints `Error: Could not extract token.` the sandbox setup has
  not finished configuring openclaw yet — re-run `openshell sandbox list` and
  wait for `Ready`, then retry. Choose a distinct `GUI_PORT` per sandbox if you
  want more than one UI open at once; the target kills any process already bound
  to that port before forwarding.

  If the terminal prints the URL normally but the browser shows
  **`ERR_EMPTY_RESPONSE`** (or the page never loads), the tunnel is fine — the
  problem is that nothing is serving HTTP on `127.0.0.1:18789` *inside* the
  sandbox. The web UI (the OpenClaw "Control UI" / dashboard) is served by the
  OpenClaw **gateway daemon**. The setup job (`apply_bom.py`) starts the
  daemon for every enabled `openclaw`-type sandbox and installs a systemd unit
  (`openshell-ui-forward.service`) on the gateway VM that forwards the
  `<name>-dashboard` Route's port into the **primary** sandbox via the
  gateway's native `openshell forward` RPC — that sandbox's UI is reachable
  from the Route URL with no manual steps. Other sandboxes have the daemon
  running but are only reachable through the tunnel targets above. If a
  sandbox's daemon is nonetheless down (or the target sandbox is not the
  forwarded primary), start it from inside the sandbox:

  ```bash
  # Confirm nothing is listening / no daemon:
  openshell sandbox exec -n notebook --no-tty -- \
    sh -c 'ss -ltn | grep 18789 || echo "18789 not listening"'

  # Start the in-sandbox OpenClaw gateway. Use the account's canonical home
  # (the sandbox user's home is /sandbox) and do NOT set OPENCLAW_HOME /
  # OPENCLAW_STATE_DIR / OPENCLAW_CONFIG_PATH, or the daemon refuses to manage
  # itself. systemctl --user is unavailable in the container, so run it directly:
  openshell sandbox exec -n notebook --tty -- \
    sh -c 'export HOME=/sandbox; unset OPENCLAW_HOME OPENCLAW_STATE_DIR OPENCLAW_CONFIG_PATH; openclaw gateway'
  ```

  Leave that gateway process running (it holds the terminal), then re-run
  `make openclaw-gui` in another terminal; the dashboard now loads. Under QEMU
  software emulation the gateway can take a while to bind `18789` — wait for the
  listener to appear before retrying the browser.

  Note the tunnel targets are name-sensitive: the targets resolve `SANDBOX_NAME`
  (falling back to the gateway name) and `WORKSPACE` (falling back to
  `default`). A tunnel started with the defaults while your agent lives in
  another workspace connects to the *wrong sandbox* — the browser then shows
  `ERR_EMPTY_RESPONSE` because that sandbox's daemon is not running. Always pass
  `SANDBOX_NAME`/`WORKSPACE` explicitly when your agent is not the default.

### The dashboard Route (no tunnel needed for the primary sandbox)

The `<name>-dashboard` Route is wired end-to-end by setup: Route →
OpenShift Virtualization masquerade → gateway VM port 18789 →
`openshell-ui-forward.service` (systemd, `Restart=always`) → authenticated
`openshell forward` into the primary sandbox's nested namespace → the
loopback-bound Control UI daemon. Get the URL and token:

```bash
TOKEN=$(openshell sandbox exec -n cuda-sandbox --workspace cuda-dev --no-tty -- \
  cat /sandbox/.openclaw/openclaw.json 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('gateway',{}).get('auth',{}).get('token',''))")
echo "https://$(oc get route openshell-saw-dashboard -n openshell-agents \
  -o jsonpath='{.spec.host}')/#token=$TOKEN"
```

The `gateway.controlUi.allowedOrigins` value is set from the Route host by the
setup job, so the browser origin validates. The `uiForward.sandbox` /
`uiForward.workspace` chart values choose which sandbox the Route serves —
one sandbox only, since the VM exposes a single UI port.

### Why the agents feel slow

Interactions in the NemoClaw/OpenClaw TUI (and the web UI) are noticeably
sluggish on this deployment — each keystroke echo and, especially, each agent
turn can lag by seconds. This is expected and is not a misconfiguration:

- **Software CPU emulation is the dominant cost.** This cluster has no hardware
  virtualization (`/dev/kvm` / nested virt is unavailable), so OpenShift
  Virtualization runs the gateway VM under **QEMU TCG software emulation**.
  Everything on that VM — the OpenShell gateway, the Docker sandbox containers,
  and the NemoClaw/OpenClaw runtime — executes emulated rather than natively,
  which is many times slower and CPU-bound. This is the same emulation that
  forced the larger `activeDeadlineSeconds` in the setup Job.
- **Every interaction crosses several hops.** A turn travels local CLI →
  `openshell ssh-proxy` tunnel → gateway VM → Docker sandbox → agent runtime,
  and back. Each hop adds latency on top of the emulation tax.
- **Model inference is a remote call.** Each agent turn calls the configured
  NVIDIA inference endpoint for a large model
  (`nvidia/nemotron-3-super-120b-a12b`), adding network round-trip and model
  latency independent of the cluster.

The takeaway: the slowness is inherent to running this pattern on an
emulation-only cluster for validation. On a KVM-capable cluster (bare metal or a
nested-virt-enabled hypervisor) the same workload runs far faster; nothing in the
fork's configuration needs changing to "fix" the speed here.

"Legacy" here refers to the older, generic convenience aliases in the
Makefile — not to the age of the repository. The repo is current; these are
simply the original one-shot targets that predate the workspace-aware
`nemoclaw-tui` / `openclaw-tui` / `openclaw-gui` targets above. They remain for
backward compatibility. `make openshell-saw-tui` and `make openshell-saw-gui`
hard-code NemoClaw in the `default` workspace, so they cannot reach a sandbox in
`cuda-dev`; prefer the workspace-aware targets. If you do use an alias, set
`OPENSHELL_SAW_NAME` first.

Run the automated test and offline template checks:

```bash
make test
./tests/test-oidc-templates.sh
```

`tests/test-oidc-templates.sh` is a fast, **offline** validation of the OIDC and
secret-handling chart templates. It only needs `helm` — no cluster, no
credentials, nothing is deployed — so it is safe to run before a deploy or in
CI. For each case it runs `helm template` on a chart and then asserts that the
rendered YAML does (or deliberately does not) contain expected content. It
covers seven groups:

- **Keycloak chart** — the `Keycloak` and `KeycloakRealmImport` CRs render, the
  `openshell-cli` client and user/admin roles exist, the test users are present,
  and PKCE, device-code flow, and user registration are enabled.
- **Pattern secrets** — the `ExternalSecret` resources render (inference, SSH
  pub/priv keys, web-search) and reference the vault backend.
- **Sandbox chart (`openshell-saw`)** in several configurations — without OIDC,
  with OIDC, with an ESO-provided provider secret, and with a web-search secret.
- **Secret name validation** — malformed secret names are expected to fail
  rendering (negative tests).

It prints `OK`/`FAILED` per assertion and a final summary; a non-zero exit means
a template regression. It validates chart *rendering*, not the live gateway, so
it complements — it does not replace — the on-cluster `sandbox list` checks
above.

The E2E test creates a separate sandbox and cleans it up. Its temporary Keycloak
install can report an ownership conflict when the pattern-managed Keycloak already
exists; that conflict is a test-release issue, not proof that the pattern deploy
failed.

The test script reads the provider API key only from an inline `value:` field; it
does not resolve a `path:` field from `values-secret.yaml`. Temporarily use an
inline value for the test or provide the test's provider input another way. The
script also defaults to a repository-local SSH key path, while `make generate-keys`
writes to `$HOME/.generated-ssh-keys`; pass the generated key explicitly:

```bash
SSH_KEY_PATH="$HOME/.generated-ssh-keys/sandbox-ssh" make test
```

## Governance: testing policy and permission changes

The gateway does not decide access itself — it delegates to a **governance
interceptor**. The wiring lives in three places:

- `charts/openshell-saw/templates/cloudinit-sandbox.yaml` renders the VM's
  `gateway.toml`, which points the gateway at the interceptor with
  `binding_policy = "allowlist"` and `failure_policy = "fail_closed"` (default
  deny; if the interceptor is unreachable, deny). Enabled via `governance.enabled`
  in `charts/openshell-saw/values.yaml`.
- `charts/governance-interceptor/` is the enforcing service (a Deployment on gRPC
  `:18081`, plus a NetworkPolicy allowing only the VM and setup-Job pods).
- `charts/governance-policy/` holds the rules: `policy.yaml` (sandbox
  filesystem/process policy) and `profiles/*.yaml` (the per-provider network
  allowlist — host/port/path, access level, credentials, binaries). These render
  into the `governance-interceptor-policy` and `governance-interceptor-profiles`
  ConfigMaps, mounted read-only into the interceptor.

### Confirm governance is live

```bash
oc get deploy,pods -n openshell-agents -l app.kubernetes.io/name=governance-interceptor
oc get configmap governance-interceptor-profiles -n openshell-agents        # 6 profile keys

# The gateway should list the vended profiles, all sourced from interceptor/governance:
openshell --gateway-insecure provider list-profiles
```

You can also confirm the interceptor block is present in the VM's live config:

```bash
virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
  --identity-file=$HOME/.generated-ssh-keys/sandbox-ssh \
  -c 'grep -n -A6 "interceptors\|binding_policy\|failure_policy" ~/.config/openshell/gateway.toml'
```

### Test 1 — the allowlist is enforced (non-destructive)

Creating a provider of an **allowed** type succeeds; a type not backed by any
profile is rejected by the interceptor. This needs no policy change and cleans up
after itself:

```bash
# Allowed (gemini is a vended profile):
openshell --gateway-insecure provider create --name test-gemini --type gemini \
  --credential GEMINI_API_KEY=demo-key            # -> ✓ Created provider test-gemini

# Blocked (not in any profile):
openshell --gateway-insecure provider create --name test-blocked --type custom \
  --credential key=value                          # -> × unsupported provider type or profile: custom
openshell --gateway-insecure provider create --name test-blocked2 --type claude-code \
  --credential ANTHROPIC_API_KEY=demo-key
  # -> × providers may only use vended provider profiles: brave, gemini, github, nvidia, slack, web-search

# Clean up:
openshell --gateway-insecure provider delete test-gemini
```

The final error message is the allowlist itself, returned by the interceptor.

### Test 2 — change an endpoint permission and verify

Inspect a profile's endpoints authoritatively from the gateway. Note that
`list-profiles -o json` prints a TLS warning line to stdout before the JSON, so
strip everything before the first `[`:

```bash
openshell --gateway-insecure provider list-profiles -o json 2>/dev/null \
  | sed -n '/^\[/,$p' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); p=[x for x in d if x["id"]=="web-search"][0]; print(len(p["endpoints"]), "endpoints"); [print(" -",e["host"],e["access"]) for e in p["endpoints"]]'
```

Each profile is signed by the interceptor (`openshell.nvidia.com/profile-signature`),
so any endpoint edit is re-signed when the interceptor reloads — a further signal
the change was actually processed.

There are two ways to apply a permission change. **The GitOps path is the correct
one for anything you intend to keep**; the direct-edit path is only for fast,
throwaway experiments.

**GitOps via your fork (persistent, PR-ready).** Edit the profile under
`charts/governance-policy/profiles/`, commit, and push to the branch ArgoCD
tracks (your fork). ArgoCD syncs the `governance-policy` application and the
interceptor's file-watcher reloads. The helper script and Make targets automate
this:

```bash
make governance-list-profiles
make governance-remove-profile PROFILE_NAME=github     # revoke
make governance-add-profile    PROFILE_NAME=github     # restore from git history
make governance-create-profile PROFILE_NAME=jira PROFILE_FILE=jira.yaml
```

> **Fork caveat:** `scripts/governance-profile.sh` pushes with `git push origin
> HEAD`. In this fork workflow `origin` is the upstream you cannot push to, so
> point it at your fork first — either set the script's remote to `fork` or run
> the equivalent steps by hand (edit file → commit → `git push fork
> saw-emulation-fixes` → let ArgoCD sync). Verify with `provider list-profiles`.

**Direct ConfigMap edit (fast, ephemeral).** You can patch the live
`governance-interceptor-profiles` ConfigMap and the interceptor will reload from
its mounted files — but the `governance-policy` app has ArgoCD auto-sync
(`automated:{}`), so a plain edit is drift that eventually gets synced away. Pause
auto-sync first, and revert by re-applying the Git content explicitly (do **not**
assume re-enabling auto-sync reverts immediately — self-heal is off, so the
periodic reconcile only overwrites drift on its own slow cycle). The reliable
sequence is pause → edit → test → restore Git content → re-enable:

```bash
# 1. Pause auto-sync on the governance-policy app (requires cluster-admin):
oc patch application governance-policy -n vp-gitops --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'

# 2. Patch one profile key in the ConfigMap (example: add a host to web-search).
#    Build the new profile YAML in /tmp/web-search-new.yaml, then:
oc get configmap governance-interceptor-profiles -n openshell-agents -o json \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); d["data"]["web-search.yaml"]=open("/tmp/web-search-new.yaml").read(); json.dump(d,sys.stdout)' \
  | oc apply -f -

# 3. Wait ~60s (projected-volume sync + file-watcher reload), then re-check with
#    the list-profiles command above until the endpoint set changes (e.g. 3 -> 4).

# 4. Deterministically restore the ConfigMap to the Git content:
oc get configmap governance-interceptor-profiles -n openshell-agents -o json \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); d["data"]["web-search.yaml"]=open("charts/governance-policy/profiles/web-search.yaml").read(); json.dump(d,sys.stdout)' \
  | oc apply -f -

# 5. Re-enable auto-sync:
oc patch application governance-policy -n vp-gitops --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{}}}}'
```

Confirmed working: `web-search` went 3 → 4 endpoints (with the interceptor
re-signing the profile) after step 2, and back to 3 after step 4.

The endpoint-level *network* enforcement itself happens in the sandbox: sandboxes
run with `HTTPS_PROXY` pointing at the governance egress proxy, which permits only
allowlisted hosts for the attached provider (a non-allowlisted host returns a
proxy `403`). A clean egress demonstration requires a provider attached to the
sandbox with valid credentials; `scripts/demo-governance.sh` walks through that
end-to-end (sections 8–9).

## Troubleshooting

### Setup Job failed or sandboxes are absent

Check the Job reason and whether the BOM ConfigMap exists:

```bash
oc get job openshell-saw-setup -n openshell-agents
oc get job openshell-saw-setup -n openshell-agents \
  -o jsonpath='{.status.conditions[*].reason}{"\n"}'
oc get configmap saw-bom-profiles -n openshell-agents
oc get vm,vmi,dv -n openshell-agents
```

If the VM and golden image are ready but the Job timed out before applying the BOM,
rerun the Job. The fork chart already renders the 5400-second deadline:

```bash
oc delete job openshell-saw-setup -n openshell-agents
oc get job openshell-saw-setup -n openshell-agents -w
oc logs -f job/openshell-saw-setup -n openshell-agents
```

If the VM is running but the gateway is not listening, inspect the service and
confirm the fork's fixes are present:

```bash
virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
  --identity-file="$HOME/.generated-ssh-keys/sandbox-ssh" \
  --local-ssh-opts="-o StrictHostKeyChecking=no" \
  --command='grep -E "OPENSHELL_(GATEWAY_CONFIG|CONFIG_FILE)" ~/.config/openshell/gateway.env; grep -A2 "drivers.docker" ~/.config/openshell/gateway.toml; systemctl --user status openshell-gateway.service --no-pager; sudo ss -tlnp | grep 17670 || true'
```

If this shows `OPENSHELL_CONFIG_FILE`, no Docker supervisor table, or a gateway
build other than the fork's expected version, verify that ArgoCD is syncing the
`saw-emulation-fixes` revision and that the deployment was not made from
`origin/main`.

### Duplicate OperatorGroups

List the OperatorGroups before deleting anything:

```bash
oc get operatorgroup -n openshell-agents
oc get operatorgroup -n openshift-cnv
```

Keep the ArgoCD-managed OperatorGroup, identified by its
`argocd.argoproj.io/tracking-id` annotation, and delete only a manually-created
duplicate. Do not delete the pattern-managed group.

### Clean up

To remove one sandbox release or the complete pattern, use the repository's targets:

```bash
export OPENSHELL_SAW_NAME=openshell-saw
make openshell-saw-delete
./pattern.sh make uninstall
```

The uninstall path can take time because KubeVirt VMs, DataVolumes, and operator
webhooks have finalizers. Do not force-delete resources unless you have confirmed
that the normal cleanup is stuck.
