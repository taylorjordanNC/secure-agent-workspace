# Securing AI Workspaces on Red Hat OpenShift

## A Guide to Deploying/Testing NVIDIA SAW

Organizations scaling AI coding and knowledge agents face a fundamental challenge: secure isolation. Because AI agents can execute arbitrary code and trigger external tool calls, traditional container-based isolation is often insufficient. Without strict boundaries, a compromised agent or runaway script can expose enterprise systems, leak secrets, or cause cluster-wide disruptions.

The NVIDIA Secure Agent Workspace (SAW) reference architecture solves this by provisioning dedicated KubeVirt virtual machines for each user on Red Hat OpenShift, running NVIDIA OpenShell with NemoClaw and OpenClaw AI agents.

## 1. Introduction

The Secure Agent Workspace delivers strong isolation guarantees by ensuring that each user's agent runs within its own dedicated boundary. Key platform pillars include:

* **VM-level isolation:** Powered by OpenShift Virtualization (KubeVirt), delivering process- and network-level isolation per user via Fedora 44 bootc images.
* **Centralized identity management:** Authentication via Red Hat Build of Keycloak (RHBK) using OpenID Connect (OIDC).
* **Secure secrets management:** API keys flow through HashiCorp Vault and the External Secrets Operator (ESO), keeping sensitive data out of Git and Helm values.
* **Flexible inference integration:** Supports multiple inference providers (Gemini, Anthropic, OpenAI, NVIDIA Build, OpenRouter, Ollama, and custom endpoints) alongside optional web-search integrations.

This article focuses on a Red Hat OpenShift deployment with the NVIDIA SAW Validated Pattern. Red Hat Services can assist in deploying the pattern and applying use cases applicable to the customer enterprise environment. The validated pattern can be found at [https://github.com/validatedpatterns-sandbox/secure-agent-workspace](https://github.com/validatedpatterns-sandbox/secure-agent-workspace).

## 2. Setting Up Your Control Node (Bastion or Laptop)

You need **one** machine with the admin CLIs (`oc`, `helm`, `openshell`, `virtctl`) and cluster access to drive the deployment and validation. Choose one of three options below.

**Choose your control node — 3 options:**

| Option | What it is | Best for | CLI install | Client login (§6) |
|---|---|---|---|---|
| **A. RHEL 10 bastion** | RHDP demo-platform **"Base RHEL 10"** catalog item, used as the bastion | Cleanest supported **x86 Linux** env; recommended for running `pattern.sh` / `make install` | `dnf` + scripts below (identical to Fedora) | Browser callback / callback-replay |
| **B. Fedora 44 bastion** | A Fedora 44 VM/instance | Same as A, if that's what you have | `dnf` + scripts below | Browser callback / callback-replay |
| **C. macOS laptop** | Your local Mac | You already have the tooling locally; **best for the client login** (native browser) | Homebrew (below) | Native browser + `localhost` callback |

**Two hard rules, whichever you pick:**

1. The sandbox **SSH private key** (from `make generate-keys`, stored only on the machine that ran it — the cluster keeps only the public key) and **`virtctl`** must live on the **same** machine. Never split them, or you hit `identity file not accessible` / `Permission denied`. Copying the key between hosts is usually blocked — the bastion's port 22 is typically unreachable from a laptop.
2. The **client login needs a browser on the same machine as the `http://localhost:<port>` callback.**

**Recommended split:** deploy from a **RHEL 10 (Option A)** or Fedora bastion, then do the **client login + `sandbox list` from your Mac (Option C)** per §6. If your Mac already has `oc`, `virtctl`, `podman`, the SSH key, and the pattern repo, you can skip the bastion and do everything locally.

### Option A — RHEL 10 bastion (RHDP demo platform)

Order the **"Base RHEL 10"** catalog item in the Red Hat Demo Platform (RHDP), SSH into the provisioned host, then follow **Step 1 / Step 2 (dnf)** below unchanged — RHEL 10 uses `dnf` exactly like Fedora.

### Option B — Fedora 44 bastion

SSH into your Fedora 44 instance and follow **Step 1 / Step 2 (dnf)** below.

### Option C — macOS laptop (Homebrew)

Skip the dnf steps below and install the CLIs with Homebrew instead:

```bash
brew install openshift-cli helm podman jq openssl gettext python3 kubernetes-cli krew
brew tap nvidia/openshell && brew install openshell   # ships the NEWEST client — see §6 version-match caveat
export PATH="$(brew --prefix gettext)/bin:$PATH"
kubectl krew install virt && ln -sf ~/.krew/bin/kubectl-virt ~/.krew/bin/virtctl && hash -r
podman machine init 2>/dev/null || true
podman machine start 2>/dev/null || true
oc version --client ; helm version ; virtctl version --client ; openshell --version ; podman --version
```

Then continue at §3. (macOS also has `podman`/`docker` for any local image builds.)

### Step 1 (dnf — Options A & B): Base system & package updates

Log into your RHEL 10 / Fedora 44 instance and update the system packages:

```bash
sudo dnf update -y
sudo dnf install -y git make podman python3-pip python3-pyyaml jq openssl curl wget tar gzip gettext
```

### Step 2 (dnf — Options A & B): Install essential CLI binaries

Run the following to install the OpenShift (`oc`), Helm, and OpenShell CLIs on your bastion:

```bash
# podman is required by pattern.sh, even though make itself runs in the utility container.
podman --version

# 1. Install OpenShift (oc) CLI
wget https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/openshift-client-linux.tar.gz
sudo tar -zxvf openshift-client-linux.tar.gz -C /usr/local/bin/
oc version

# 2. Install Helm 3 CLI
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version

# 3. Install OpenShell CLI (latest release)
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
openshell --version
```

> **Note — install `virtctl` too.** The list above omits `virtctl`, but you need it for `make openshell-saw-configure-gateway`, the setup job's guest SSH, and any manual VM debugging. Install the version that **matches your cluster** (avoids a version-skew warning):
> ```bash
> URL=$(oc get consoleclidownload virtctl-clidownloads-kubevirt-hyperconverged \
>   -o jsonpath='{.spec.links[*].href}' | tr ' ' '\n' | grep -i linux | grep -iE 'amd64|x86_64' | head -1)
> curl -L -k "$URL" -o /tmp/virtctl.tar.gz && tar xzf /tmp/virtctl.tar.gz -C /tmp
> sudo install -m 0755 /tmp/virtctl /usr/local/bin/virtctl && virtctl version --client
> ```
> On a **Mac**, if you installed it via krew (`kubectl krew install virt`) the binary is `~/.krew/bin/kubectl-virt` and there is no `virtctl` on your PATH. Create one (krew's bin dir is already on PATH):
> ```bash
> ln -sf ~/.krew/bin/kubectl-virt ~/.krew/bin/virtctl && hash -r && virtctl version --client
> ```

### Step 3: Log in to your cluster

Authenticate the `oc` CLI against your cluster API before running any prerequisite checks or the deployment. On a demo/lab cluster with a self-signed API cert, accept the insecure connection when prompted:

```bash
oc login https://api.<cluster>.dyn.redhatworkshops.io:6443 -u admin
# Enter the admin password when prompted.
oc whoami   # confirms you are logged in
```

## 3. Prerequisites & Multicloud CNV Instance Setup

Before deploying the agent workspace, you must initialize your target OpenShift Container Platform cluster with the appropriate sizing and cloud-provider choices.

### Selecting your environment options

When ordering or provisioning your cluster instance via the Red Hat Demo Platform (RHDP) catalog, choose the following recommended specifications to ensure optimal resource headroom for virtualization workloads:

* **Cloud provider:** Select **CNV** (Container-Native Virtualization), which runs directly on OpenShift Virtualization as the underlying hypervisor layer. AWS will work as well. Both require the KVM emulator to be enabled.
* **Cluster size:** Choose a **multi-node OpenShift** customized instance configured with **16 CPUs** and **32 GiB of memory** minimum, to properly support nested execution and active user sandboxes.
* **OpenShift version:** Select **OpenShift 4.22+ (Default)**.

### Clone the pattern repository

On your control node, clone the validated pattern repository:

```bash
mkdir -p ~/git && cd ~/git
git clone https://github.com/validatedpatterns-sandbox/secure-agent-workspace
cd secure-agent-workspace
```

## 4. Installing OpenShift Virtualization & KVM Emulator Setup

To run virtual machines side-by-side with your standard container workloads, you must deploy the OpenShift Virtualization operator and configure the required hypervisor/emulator options.

> **Recommended: let the pattern install OpenShift Virtualization.** This validated pattern installs OpenShift Virtualization **itself** via GitOps (`values-prod.yaml` declares the `openshift-cnv` namespace, its OperatorGroup, and the `kubevirt-hyperconverged` subscription). The cleanest path is to **skip the manual OperatorHub install** in Step 1 below and let the pattern install CNV during §5. The manual steps are documented here only for environments where you install CNV ahead of the pattern.

### Step 1: Install the OpenShift Virtualization operator (manual, optional)

1. Log in to the OpenShift web console using your cluster administrator credentials.
2. Navigate to **Operators → OperatorHub**.
3. Search for **OpenShift Virtualization**, select it, and click **Install**.
4. Ensure the installation namespace is set to `openshift-cnv` (or the default operator namespace) and click **Install** to apply it across the stable channel.
5. **KVM software emulation:** see the corrected procedure in Step 2 below. **Do not** set a `KVM_EMULATOR` environment variable on the HCO *operator Deployment* (via the console **Environment** tab or `oc set env`): OLM reverts it and it spawns duplicate ReplicaSets. Software emulation is enabled on the **HyperConverged CR**, not on the operator pod.

> **Duplicate-OperatorGroup warning.** If you install OpenShift Virtualization manually from OperatorHub *and* the pattern also installs it via GitOps, you end up with **two OperatorGroups** in `openshift-cnv` and the CSV fails with `TooManyOperatorGroups: csv created in namespace with multiple operatorgroups`. Prefer skipping the manual install. If you already hit the collision, delete the **manually-created** OperatorGroup — the pattern's is named `openshift-cnv-operator-group` and carries an `argocd.argoproj.io/tracking-id` annotation:
> ```bash
> oc get operatorgroup -n openshift-cnv   # identify the one WITHOUT the argocd tracking-id
> oc delete operatorgroup <manual-og-name> -n openshift-cnv
> ```

### Step 2: Configure the HyperConverged CR & KVM emulator

Once the operator is running, create the HyperConverged custom resource to initialize the virtualization control plane. If you are deploying on non-bare-metal cloud instances where hardware acceleration is restricted, enable QEMU software emulation.

> **Enable QEMU software emulation** (required on non-bare-metal / cloud instances with no `/dev/kvm`, e.g. RHDP CNV or AWS). Set `useEmulation` on the **HyperConverged CR** via the supported HCO jsonpatch annotation — not on the operator Deployment. Put the JSON in a variable so no stray newline sneaks into the path (a pasted newline causes `admission webhook denied ... invalid jsonPatch ... invalid state detected`):
> ```bash
> PATCH='[{"op":"add","path":"/spec/configuration/developerConfiguration/useEmulation","value":true}]'
> oc annotate hco kubevirt-hyperconverged -n openshift-cnv \
>   "kubevirt.kubevirt.io/jsonpatch=$PATCH" --overwrite
> # verify it propagated to the KubeVirt CR:
> oc get kubevirt kubevirt-kubevirt-hyperconverged -n openshift-cnv \
>   -o jsonpath='{.spec.configuration.developerConfiguration.useEmulation}{"\n"}'
> ```
> Without this, the sandbox VM never boots on emulation-only nodes and the `openshell-saw-setup` job eventually fails with `BackoffLimitExceeded`.

## 5. Deployment

### Step 1: Check prerequisites

With `oc` logged in, run the prerequisite check after the pattern-managed operators
have had time to install:

```bash
make check-prereqs
```

On a fresh cluster this may first report that the RHBK (Keycloak) operator is not yet installed in `openshell-agents`:

```text
Checking operators...
  OpenShift Virtualization: installed
Error: RHBK operator found in namespace 'keycloak' but not in 'openshell-agents'.
```

> **Known trap — do NOT blindly apply the OperatorGroup that `check-prereqs` suggests.** The suggested block creates an OperatorGroup named `openshell-agents-og`, but the pattern **already** creates `openshell-agents-operator-group` for that namespace. Two OperatorGroups cause the RHBK/Keycloak CSV to fail with `TooManyOperatorGroups`. Two ways to avoid it:
> - **Preferred:** don't run `make check-prereqs` until the pattern has finished syncing (`oc get applications -n vp-gitops` should show all applications `Synced/Healthy`). ArgoCD installs RHBK into `openshell-agents` on its own; the `check-prereqs` error is usually just a timing artifact. If you must apply something manually, apply **only the Subscription**, never the OperatorGroup.
> - If you already applied it and hit `TooManyOperatorGroups`, delete the manual OperatorGroup (keep the ArgoCD-managed one — it has an `argocd.argoproj.io/tracking-id` annotation), then let the CSV recover:
> ```bash
> oc delete operatorgroup openshell-agents-og -n openshell-agents
> oc get csv -n openshell-agents | grep rhbk   # should go Failed → Succeeded
> # if it stays Failed, nudge it (the Subscription recreates it):
> #   oc delete csv <rhbk-csv-name> -n openshell-agents
> ```

Once RHBK is installed, a healthy `make check-prereqs` reports:

```text
Checking operators...
  OpenShift Virtualization: installed
  RHBK (Keycloak): installed in openshell-agents
  SSH key: not found. Run 'make generate-keys'.
Checking image registry route...
  Image registry route: enabled
Prerequisites OK.
```

### Step 2: Generate SSH keys

```bash
make generate-keys
```

This writes the sandbox keypair to `.generated-ssh-keys/sandbox-ssh` (private) and `.generated-ssh-keys/sandbox-ssh.pub` (public). Keep the private key on this machine — it must live alongside `virtctl` (see §2, hard rule 1).

### Step 3: Configure secrets

```bash
cp values-secret.yaml.template ~/values-secret.yaml
# Edit ~/values-secret.yaml — set at least one provider API key and the SSH keys.
```

The `inference` secret should look like this (values shown for NVIDIA Build):

```yaml
- name: inference
  fields:
  - name: provider
    value: build          # NVIDIA's provider id is "build", NOT "nvidia"
  - name: model
    value: nvidia/nemotron-3-super-120b-a12b   # a real model id; "NemoClaw" is not valid
  - name: api_key
    value: nvapi-VD_*******   # provide EITHER value: OR path: — never both
```

> **Secret gotchas** (these cause `./pattern.sh make install` to fail at *Parse secrets data*):
> - **Provider id:** use `build` for NVIDIA. The valid ids are `gemini, anthropic, openai, build (NVIDIA), openrouter, ollama, custom` (see the template comment in `values-secret.yaml.template`). `nvidia` is not a valid provider id.
> - **Model:** must be a real model id (e.g. `nvidia/nemotron-3-super-120b-a12b`), not a product name like `NemoClaw`.
> - **`api_key` must have exactly ONE of `value:` or `path:`.** Having both (e.g. an inline `value:` *and* `path: ~/.ngc-api-key`) makes the validated-patterns secret loader fail to parse.
>
> **Security:** `~/values-secret.yaml` and any key file (e.g. `~/.ngc-api-key`) contain live credentials — **never commit them**; keep both in `.gitignore`. If a key is ever pasted into a shared or committed location, **revoke and rotate it immediately**.

### Step 4: Copy pre-built images to the cluster (~5 min)

Mirrors images from `quay.io/rh-ai-quickstart` to the internal registry. No build is needed — images are pre-built by maintainers.

```bash
make copy-images
```

### Step 5: Deploy the pattern

The deploy runs inside the Validated Patterns utility container. The utility container
must be able to resolve the target branch from the Git remote. The stock repository
uses `main`; do not deploy a local-only branch without publishing it first.

```bash
# If using a different published branch, select it explicitly:
# export TARGET_BRANCH=main
./pattern.sh make install
```

> **Upstream emulation caveat.** The stock pattern provisions the sandbox VM and
> configures the in-VM `openshell-gateway` through cloud-init and the setup Job.
> On QEMU **software emulation** (RHDP CNV / AWS, with no `/dev/kvm`) the stock
> chart has three independent problems:
>
> - `job.activeDeadlineSeconds` is `1800`; golden-image bootstrap and binary pulls
>   can exceed 30 minutes, causing `DeadlineExceeded`.
> - Cloud-init writes `OPENSHELL_CONFIG_FILE`, but this gateway build reads
>   `OPENSHELL_GATEWAY_CONFIG`, so `gateway.toml` is not loaded.
> - The Docker driver has no `supervisor_bin` entry and tries to pull the mutable
>   `ghcr.io/nvidia/openshell/supervisor:dev` image, which lacks `/openshell-sandbox`
>   and crash-loops with a 404.
>
> The VM can still become `Running`, so this is recoverable. Let the install finish
> or fail, then apply the one-time VM remediation in §6 Step 3.
>
> **Why the fixes are post-install on the stock repo.** The utility container deploys
> charts from the remote branch — not your local working tree — so local edits to
> `cloudinit-sandbox.yaml` or `values.yaml` never reach the deployment. The manual
> interventions on the stock repo are therefore post-install, and each maps to a
> permanent chart fix proposed on the fork:
>
> | Fork change | Stock-repo intervention |
> |---|---|
> | Cloud-init writes `OPENSHELL_GATEWAY_CONFIG` (not `OPENSHELL_CONFIG_FILE`) | In-VM `gateway.env` fix — §6 Step 3 |
> | Cloud-init writes the `[openshell.drivers.docker] supervisor_bin` table | In-VM `gateway.toml` fix — §6 Step 3 |
> | `values.yaml` raises `activeDeadlineSeconds` to `5400` | Patch the recreated setup Job — §7 empty-`sandbox list` fix |
> | BOM enables `default/cuda-sandbox`, `default/toolbox`, `cuda-dev/toolbox` | Optional (workshop set only) — see the BOM note in §6 |
> | `scripts/oidc-login.sh` sends PKCE on the device-code flow | Optional (headless logins only) — see §6 Step 2 |
>
> Under emulation, wait substantially longer than five minutes, then inspect
> `oc get job openshell-saw-setup -n openshell-agents` and
> `oc get applications -n vp-gitops`. A failed setup Job or a `Degraded` application
> is expected until the remediation and BOM re-run are complete.

### Step 6: (If needed) recover a stuck deployment

If the VM/setup job is genuinely wedged, re-mirror images and let ArgoCD rebuild the resources.

> **Caution — only clean up if the resources are *genuinely* stuck.** Under **software emulation** the DataVolume import + VM boot + `openshell-saw-setup` job can take **far longer than 5 minutes** (tens of minutes). Deleting the golden DataVolume forces a full re-import and can leave the DV `Failed`/broken mid-reimport. Before running the deletes below, confirm the VM is actually wedged: `oc get vm,vmi,dv -n openshell-agents`. A VMI at `phase=Running ready=True` with the setup job still "Waiting for guest SSH" just means the guest is slow to come up under emulation, not that anything is broken.

```bash
# 1. Re-mirror images (now that the namespace is stable)
make copy-images

# 2. Clean up the stuck resources
oc delete dv openshell-gateway-docker-golden -n openshell-agents
oc delete job openshell-saw-setup -n openshell-agents
oc delete vm openshell-saw -n openshell-agents

# 3. Enable self-heal so ArgoCD recreates them
oc patch application openshell-saw -n vp-gitops --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":true}}}}'

# 4. Wait ~5 min (longer under emulation) for DV import + VM boot + setup job
oc get dv,vm,job -n openshell-agents -w
```

## 6. Validation

### What this deploys (workspaces & sandboxes)

Sandboxes are **declarative**, not created ad-hoc. The `saw-bom` chart selects one or more **profiles** (`charts/saw-bom/values.yaml`, default: `data-science`); each profile defines **workspaces → providers → sandboxes** under `charts/saw-bom/profiles/<profile>/`. At deploy time the `openshell-saw-setup` Job runs `apply_bom.py` inside the gateway VM and creates every sandbox marked `enabled: true`. To add a sandbox, set `enabled: true` in the relevant `.../sandbox.yaml` and let ArgoCD re-sync — you do not hand-create it.

The active `data-science` profile provisions:

| Workspace | Sandbox | Type | Image | Provider | `enabled`? | Created on deploy |
|---|---|---|---|---|---|---|
| `default` | **`notebook`** | openclaw | `openclaw-openshell:latest` | nvidia | ✅ true | **Yes** |
| `default` | `cuda-sandbox` | nemoclaw (agent openclaw) | `nemoclaw-sandbox:latest` | nvidia | ❌ false | No |
| `default` | `toolbox` | generic | `base` | nvidia | ❌ false | No |
| `cuda-dev` | **`cuda-sandbox`** | nemoclaw (agent openclaw) | `nemoclaw-sandbox:latest` | nvidia | ✅ true | **Yes** |
| `cuda-dev` | `toolbox` | generic | `base` | nvidia | ❌ false | No |

So a healthy deploy comes up with exactly two sandboxes: **`default/notebook`** and **`cuda-dev/cuda-sandbox`**. Sandbox types: `openclaw` (OpenClaw agent onboarded inside the sandbox), `nemoclaw` (NVIDIA NemoClaw onboarded on the gateway VM via docker; `agent: openclaw` names the in-sandbox agent it drives), and `generic` (a plain shell sandbox on the `base` image, no agent). All sandboxes here use the `nvidia` provider (model `nvidia/nemotron-3-super-120b-a12b`, credential from the `inference` secret configured in §5).

> **Workshop sandbox set (optional).** The table above is the **stock** repo state: `default/cuda-sandbox`, `default/toolbox`, and `cuda-dev/toolbox` are disabled. The workshop fork enables those three (`enabled: true`), giving five sandboxes. Because the utility container and Argo CD deploy charts from the remote branch — not your local working tree — enabling them on the stock repo requires publishing those edits to a branch the deploy and Argo CD track, or creating the additional sandboxes manually with `make openshell-saw-create` using matching provider/model parameters. For the standard deployment, the two upstream sandboxes above are sufficient.

> **If `sandbox list` returns nothing**, the sandboxes were never created — almost always because the `openshell-saw-setup` Job failed before `apply_bom.py` ran (common under emulation), the `saw-bom-profiles` ConfigMap is missing, or you're querying the wrong workspace. See **Troubleshooting: empty `sandbox list`** at the end of this section.

### Step 1: Install a version-matched CLI client

The in-VM gateway is pinned to `0.0.103-rhaiv.0`; install the **matching** upstream client. A newer client (e.g. Homebrew's latest) can silently stall on the login handshake.

First confirm the gateway's version from whichever machine has `virtctl` + the SSH key:

```bash
virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
  --identity-file=$HOME/.generated-ssh-keys/sandbox-ssh \
  --local-ssh-opts="-o StrictHostKeyChecking=no" \
  --command "openshell-gateway --version"        # -> openshell-gateway 0.0.103-rhaiv.0
```

Then download and use the matching client:

```bash
# macOS (Apple Silicon):
mkdir -p ~/openshell-cli && cd ~/openshell-cli
curl -sSL -o openshell.tar.gz \
  https://github.com/NVIDIA/OpenShell/releases/download/v0.0.103/openshell-aarch64-apple-darwin.tar.gz
tar xzf openshell.tar.gz
xattr -d com.apple.quarantine ./openshell 2>/dev/null || true
export PATH="$HOME/openshell-cli:$PATH"
./openshell --version                            # -> openshell 0.0.103

# Linux x86_64:  openshell-x86_64-unknown-linux-musl.tar.gz
# Linux aarch64: openshell-aarch64-unknown-linux-musl.tar.gz
```

### Step 2: Register the gateway (OIDC mode) and authenticate

Derive the route hosts from the cluster, then register in OIDC mode. This opens a browser to Keycloak — log in as a realm user (e.g. `alice` / `alice`).

```bash
GW_HOST=$(oc get route openshell-saw-gateway -n openshell-agents -o jsonpath='{.spec.host}')
KC_HOST=$(oc get route -n openshell-agents -o jsonpath='{range .items[*]}{.spec.host}{"\n"}{end}' | grep keycloak | head -1)
echo "gateway=$GW_HOST" ; echo "keycloak=$KC_HOST"

./openshell gateway add https://$GW_HOST:443 \
  --name openshell-saw \
  --oidc-issuer https://$KC_HOST/realms/openshell \
  --oidc-client-id openshell-cli \
  --gateway-insecure

./openshell gateway select openshell-saw
```

> **Why `--oidc-issuer` (and not a bare `https://` add).** A bare `https://…` endpoint makes `openshell gateway add` treat the gateway as an **"edge-authenticated (cloud) gateway"** and open a gateway-hosted `/auth/connect?...` page that **spins forever** ("OpenShell — Authenticating…", auto-refreshing every 2s) — this gateway is a plain **OIDC+mTLS** gateway, not an edge proxy, so it never redirects you to Keycloak (Keycloak logs stay silent; the gateway logs only repeated `GET /auth/connect → 200`). `--oidc-issuer` drives the standard Keycloak authorization-code+PKCE login with a `http://localhost:<port>` redirect (which *is* in the `openshell-cli` client allowlist). mTLS here is *requested but not required* at the TLS layer — the OIDC token authorizes the call — so no client cert needs pre-provisioning.

> **Headless / remote bastion (no local browser).** In the original source,
> `scripts/oidc-login.sh` does not send the PKCE challenge required by this
> Keycloak client's device flow. Therefore `make login OIDC_FLOW=device-code`
> fails with a PKCE/client error. Use `OIDC_FLOW=browser` with a browser on the
> control node, forward the callback port over SSH, or use the callback-replay
> workaround: open the printed authorization URL elsewhere and `curl` the exact
> resulting `127.0.0.1:<port>/callback?...&code=...` URL from a second bastion
> terminal before the callback request expires. The alternative
> `make openshell-saw-configure-gateway` target needs `virtctl` **and**
> `~/.generated-ssh-keys/sandbox-ssh` on the same machine; set
> `export OPENSHELL_SAW_NAME=openshell-saw` first.
>
> **Manual PKCE fix (optional, device-code logins only).** `scripts/oidc-login.sh`
> runs on the control node, so a local edit takes effect immediately. In
> `do_device_login`: call `generate_pkce` (already defined for the browser flow),
> add `-d code_challenge=${CODE_CHALLENGE} -d code_challenge_method=S256` to the
> device-authorization request, add `-d code_verifier=${CODE_VERIFIER}` to the
> token poll, and poll with `-sSL` (not `-fsSL`) so `slow_down`/`expired_token`
> error bodies are parsed. The fork carries the exact diff for this change.

### Step 3: Verify connectivity

```bash
./openshell --gateway-insecure sandbox list
```

A successful upstream deploy creates `default/notebook` and
`cuda-dev/cuda-sandbox`; an empty result means the setup Job did not apply the BOM.

> **Gateway crash-loop fix — REQUIRED on the stock/upstream pattern under emulation; also the fix if `sandbox list` returns `transport error` / `tls handshake eof`.** The gateway process inside the VM is crash-looping (this is *not* a client mTLS problem). **Root cause** (confirmed): the `openshell-saw-setup` job died with `DeadlineExceeded` (its `activeDeadlineSeconds=1800` is far too short for the golden-image bootstrap + binary pulls under **software emulation**), so it never finished wiring up the gateway config. Two follow-on defects are left behind in the VM's `~/.config/openshell/`:
> 1. `gateway.env` sets the config-file pointer with the **wrong variable name** — `OPENSHELL_CONFIG_FILE` — but this gateway build reads **`OPENSHELL_GATEWAY_CONFIG`** (see `openshell-gateway --help`). So `gateway.toml` is never loaded.
> 2. `gateway.toml` has **no `[openshell.drivers.docker]` table**, so the docker driver has no `supervisor_bin` and falls back to *"Refreshing mutable docker supervisor image"* → pulls the broken default `ghcr.io/nvidia/openshell/supervisor:dev` (missing `/openshell-sandbox` → `Docker responded with status code 404`) and exits `status=1/FAILURE` on a 5-second restart loop.
>
> **Note:** setting `OPENSHELL_SANDBOX_IMAGE` in `gateway.env` does **nothing** — the gateway has no such env var. The correct supervisor binary is already installed at `/usr/local/bin/openshell-supervisor` (`openshell-sandbox 0.0.103-rhaiv.0`); point the docker driver at it. Diagnose and fix from whichever machine has `virtctl` + the SSH key:
> ```bash
> # 1. Confirm the crash loop (404 supervisor error + high restart counter, not listening on 17670):
> virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
>   --identity-file=$HOME/.generated-ssh-keys/sandbox-ssh \
>   --local-ssh-opts="-oStrictHostKeyChecking=no" --local-ssh-opts="-oUserKnownHostsFile=/dev/null" \
>   --command='journalctl --user -u openshell-gateway --no-pager -n 30; sudo ss -tlnp | grep 17670 || echo "not listening"'
>
> # 2. Add the docker driver table (points at the local supervisor binary) and fix the config env var:
> virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
>   --identity-file=$HOME/.generated-ssh-keys/sandbox-ssh \
>   --local-ssh-opts="-oStrictHostKeyChecking=no" --local-ssh-opts="-oUserKnownHostsFile=/dev/null" \
>   --command='
>     CFG=$HOME/.config/openshell/gateway.toml; ENVF=$HOME/.config/openshell/gateway.env;
>     cp "$CFG" "$CFG.bak"; cp "$ENVF" "$ENVF.bak";
>     grep -q "openshell.drivers.docker" "$CFG" || printf "\n[openshell.drivers.docker]\nsupervisor_bin = \"/usr/local/bin/openshell-supervisor\"\n" >> "$CFG";
>     grep -v -E "^OPENSHELL_SANDBOX_IMAGE=|^OPENSHELL_CONFIG_FILE=|^OPENSHELL_GATEWAY_CONFIG=" "$ENVF" > "$ENVF.tmp";
>     echo "OPENSHELL_GATEWAY_CONFIG=/home/cloud-user/.config/openshell/gateway.toml" >> "$ENVF.tmp"; mv "$ENVF.tmp" "$ENVF";
>     systemctl --user daemon-reload; systemctl --user reset-failed openshell-gateway.service;
>     systemctl --user restart openshell-gateway.service; sleep 12;
>     systemctl --user show openshell-gateway.service -p ActiveState -p SubState -p NRestarts;
>     sudo ss -tlnp | grep 17670 || echo "still not listening"'
> ```
> A healthy result is `ActiveState=active / SubState=running / NRestarts=0`, listening on `0.0.0.0:17670`, and logs showing `Compute driver connected configured_driver=docker` with **no** "Refreshing mutable docker supervisor image" line. You can confirm from anywhere with `echo | openssl s_client -connect <gateway-route-host>:443` — a successful handshake shows `subject=CN=openshell-server`. **The permanent chart fix would be:** raise the setup Job's `activeDeadlineSeconds` for emulation, fix `OPENSHELL_CONFIG_FILE` to `OPENSHELL_GATEWAY_CONFIG`, and have cloud-init write the `[openshell.drivers.docker] supervisor_bin` table. The fork guide documents those changes.

### Step 4: List sandboxes per workspace

Once sandboxes exist, list them per workspace. (`--gateway-insecure` logs a benign "TLS certificate verification is disabled" warning — expected on lab clusters.)

```bash
# default workspace
openshell --gateway-insecure sandbox list
# NAME      CREATED              PHASE
# notebook  2026-09-01 13:08:49  Ready

# cuda-dev workspace
openshell --gateway-insecure sandbox list --workspace cuda-dev
# NAME          CREATED              PHASE
# cuda-sandbox  2026-09-01 13:08:01  Ready
```

### Step 5: Launch agent sandboxes (NemoClaw / OpenClaw)

```bash
# NemoClaw sandbox — TUI (workspace cuda-dev). Press CTRL-D to exit the TUI.
OPENSHELL_SAW_NAME=openshell-saw \
SANDBOX_NAME=cuda-sandbox \
WORKSPACE=cuda-dev \
make nemoclaw-tui

# OpenClaw sandbox — TUI and GUI (workspace default)
OPENSHELL_SAW_NAME=openshell-saw \
SANDBOX_NAME=notebook \
make openclaw-tui

OPENSHELL_SAW_NAME=openshell-saw \
SANDBOX_NAME=notebook \
GUI_PORT=18790 \
make openclaw-gui
```

The legacy aliases (`make openshell-saw-tui` / `make openshell-saw-gui`) default to NemoClaw. They require `OPENSHELL_SAW_NAME` to be exported and an existing sandbox in the target workspace, otherwise they fail with `OPENSHELL_SAW_NAME is required` or `No sandboxes found`:

```bash
export OPENSHELL_SAW_NAME=openshell-saw
make openshell-saw-tui    # runs nemoclaw-tui against workspace 'default'
make openshell-saw-gui    # runs nemoclaw-gui
```

You can also open the dashboard directly via its route:

```bash
oc get route ${OPENSHELL_SAW_NAME}-dashboard -n openshell-agents -o jsonpath='https://{.spec.host}'
```

### Step 6: Automated tests

Run the headless end-to-end test (it creates and tears down its own sandbox):

```bash
make test
```

> **E2E test caveats.** `scripts/e2e-test.sh` reads the provider API key only from
> an inline `value:` field; it does not resolve a `path:` field from
> `values-secret.yaml`. For the test, temporarily use an inline value or export
> the provider variables expected by your local test workflow. The script also
> defaults to a repository-local SSH key path, while `make generate-keys` writes
> to `$HOME/.generated-ssh-keys`; pass the generated key explicitly:
> ```bash
> SSH_KEY_PATH="$HOME/.generated-ssh-keys/sandbox-ssh" make test
> ```
>
> **Note:** on a cluster where Keycloak was installed by the pattern (not by `make test`), the E2E test's own Keycloak install step can fail with an ownership error such as `Secret "keycloak-db-secret" ... cannot be imported into the current release: ... missing key "app.kubernetes.io/managed-by"`. This is a conflict between the test's Helm release and the pattern-managed Keycloak, not a deployment failure.

Run the offline template validation (no cluster needed):

```bash
./tests/test-oidc-templates.sh
# Example summary:  47 passed, 6 failed
# The failures are known gaps in a few OIDC/provider-secret template assertions
# and do not block a working deployment.
```

### Troubleshooting: empty `sandbox list`

If `sandbox list` connects cleanly (no TLS error) but returns **no sandboxes in any workspace**, the sandboxes were never created — the gateway is healthy but the BOM was never applied. On QEMU **software emulation** the usual cause is that the `openshell-saw-setup` Job hit `DeadlineExceeded` **before** its BOM-apply phase (`setup-bom-profiles.sh` → `apply_bom.py`), so no workspaces or sandboxes exist. Diagnose top-down:

```bash
# 1. Is the setup Job Failed, and why?
oc get job openshell-saw-setup -n openshell-agents
oc get job openshell-saw-setup -n openshell-agents \
  -o jsonpath='{.status.conditions[*].reason}{"\n"}'   # DeadlineExceeded => timed out

# 2. Is the VM/gateway actually up? (it usually is — that's why login works)
oc get vm,vmi -n openshell-agents
virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
  --identity-file=$HOME/.generated-ssh-keys/sandbox-ssh \
  --local-ssh-opts="-oStrictHostKeyChecking=no" --local-ssh-opts="-oUserKnownHostsFile=/dev/null" \
  --command='systemctl --user show openshell-gateway.service -p ActiveState -p SubState -p NRestarts; sudo ss -tlnp | grep 17670 || echo "not listening"'

# 3. Did the BOM ever get applied on the VM? (missing files => it did NOT run)
virtctl -n openshell-agents ssh cloud-user@vm/openshell-saw \
  --identity-file=$HOME/.generated-ssh-keys/sandbox-ssh \
  --local-ssh-opts="-oStrictHostKeyChecking=no" --local-ssh-opts="-oUserKnownHostsFile=/dev/null" \
  --command='ls -la ~/bom-profiles ~/apply_bom.py 2>&1 || echo "BOM never applied"'

# 4. Is the BOM ConfigMap present for a re-run to consume?
oc get configmap saw-bom-profiles -n openshell-agents
```

**Fix — re-run the setup Job so it reaches the BOM-apply phase.** Because the VM and golden image are already up, a re-run reaches BOM apply quickly. Re-running can restart the gateway, so re-apply the §6 Step 3 remediation after the Job completes if the gateway returns to a crash loop.

```bash
# Raise the deadline first. The stock/upstream chart defaults to 1800s, which is too
# short under emulation.
# The Job is ArgoCD-managed; deleting it triggers selfHeal to recreate it from the chart.
oc delete job openshell-saw-setup -n openshell-agents

# Wait for ArgoCD selfHeal to recreate it, then (stock pattern only) extend the deadline
# on the fresh Job so it doesn't time out again before BOM apply:
oc get job openshell-saw-setup -n openshell-agents -w   # wait until it reappears, Ctrl-C
oc patch job openshell-saw-setup -n openshell-agents --type merge \
  -p '{"spec":{"activeDeadlineSeconds":5400}}'

# Follow progress until it reaches "BOM profiles applied":
oc logs -f job/openshell-saw-setup -n openshell-agents

# Then confirm the two upstream sandboxes exist:
openshell --gateway-insecure sandbox list                       # -> notebook (default)
openshell --gateway-insecure sandbox list --workspace cuda-dev  # -> cuda-sandbox
```

If a re-run keeps timing out even with a raised deadline, the sandbox containers themselves are slow to come up under emulation. Check `oc logs job/openshell-saw-setup` for the BOM phase and give it more time. The permanent chart-level fixes are documented in `deployment-guide-fork.md`.
