# Harness skills & tools via SAW-BOM

Architecture and implementation plan for versioned agent-harness configuration —
the missing bridge between VM/BOM config and OpenClaw / NemoClaw runtime state.

**Status:** Design proposal (not implemented)  
**Related:** [Astra SAW deployment discovery](astra-saw-deployment-discovery.md) §5,
[BOM architecture](bom-architecture.md), [SAW blueprint implementation](saw-blueprint-implementation.md)

## Verdict

Governance (OpenShell interceptor + policy profiles) and harness configuration are
**separate control planes**. SAW-BOM already delivers workspace / provider / sandbox
YAML into the guest over virtiofs, but nothing declares, versions, or reconciles
skills and tools into the agent harness.

Image-baked plugins and one-shot `openclaw onboard` / `nemoclaw onboard` in
`apply_bom.py` are not a managed contract. Adding YAML fields alone will not
configure the harness.

## What exists vs what is missing

| Layer | Today | Gap for skills/tools |
|---|---|---|
| SAW-BOM profile | `workspace.yaml` / `providers.yaml` / `sandbox.yaml` | No `skills/`, `plugins/`, `tools/`, or `harness.yaml`; sandbox schema has no `harnessRef` |
| Guest delivery | ConfigMap → virtiofs → `saw_guest/inputs` | No artifact staging or digest for harness bundles |
| `apply_bom.py` | Create sandbox + onboard + config set model/token | No reconcile of skill/tool sets; no revoke; no `appliedRevision` |
| OpenShell governance | Network / binary / FS policy via interceptor | Does not install harness skills; must still allowlist tool endpoints |
| Sandbox image | Plugins / tools baked at build time | Cannot update without image rebuild; not tenant-declarative |

## Target control-plane split

Keep three update planes separate — they have different restart and persistence semantics.

### OpenShell plane

- **Owns:** Workspace, providers, sandbox lifecycle, inference route, signed network/FS/process policy.
- **Does not own:** Which `SKILL.md` files or MCP tools the harness loads.

### Harness plane (new)

- **Owns:** Versioned skills directory, tool plugins (`.mjs` / `.js` code), MCP server declarations, enablement, restart-of-harness policy.
- **Applied by:** Guest harness adapter after the sandbox exists (exec / copy / harness CLI).

### Image plane

- **Owns:** Runtime binaries and base plugins that cannot be hot-patched safely.
- **Rule:** Prefer BOM-bundled skills/tools for tenant config; reserve the image for platform prerequisites.

## End-to-end architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Git: SAW-BOM │────►│ saw-bom chart│────►│ ArgoCD sync  │
│ skills+tools │     │ ConfigMaps   │     │              │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │ virtiofs     │
                                          │ /run/saw/    │
                                          │ profiles     │
                                          └──────┬───────┘
                                                 │
                                                 ▼
┌────────────────┐     ┌────────────────┐     ┌────────────────┐
│ Governance     │────►│ OpenShell      │◄────│ saw-guest      │
│ interceptor    │     │ gateway+policy │     │ reconciler     │
└────────────────┘     └───────┬────────┘     └───────┬────────┘
                               │                      │
                               │                      ▼
                               │               ┌────────────────┐
                               │               │ apply_bom +    │
                               │               │ harness adapter│◄── NEW
                               │               └───────┬────────┘
                               │                      │
                               ▼                      ▼
                        ┌─────────────────────────────────────┐
                        │ Sandbox (OpenClaw / NemoClaw)       │
                        │ managed skills/ + tools/ applied    │
                        └─────────────────────────────────────┘
```

Solid edges are data/control flow. The harness adapter applies the bundle into
the sandbox; OpenShell still enforces policy on tool traffic.

## What a bundle carries

A harness bundle has three kinds of content. They are not interchangeable:
only a plugin adds a tool the agent can call by itself.

| Kind | Files | What it is | How OpenClaw loads it |
|---|---|---|---|
| Skill | `skills/<name>/SKILL.md` (+ optional reference files) | Instructions and prompts; no code | Read from the skills directory |
| Tool plugin | `plugins/<id>/package.json`, `openclaw.plugin.json`, `index.mjs` | Code: an ES module that calls `api.registerTool(...)` | `openclaw plugins install <dir>` |
| MCP server declaration | `tools/<name>.yaml` | Where an existing MCP server is and how to reach it; the tools live in that server | Translated by the adapter into `openclaw mcp set <name> '<json>'` |

A `tools/*.yaml` file on its own does not give the agent a tool. OpenClaw
registers tools from plugins (or from MCP servers it is configured to reach),
so code tools ship as plugins.

## Proposed SAW-BOM layout

```
charts/saw-bom/
  profiles/<profile>/<workspace>/
    workspace.yaml
    providers.yaml
    sandbox.yaml              # + harnessRef
  harness/<bundle>/
    harness.yaml              # kind: HarnessBundle
    skills/<name>/SKILL.md
    plugins/<id>/             # tool plugin (code)
      package.json            # "openclaw": { "extensions": ["./index.mjs"] }
      openclaw.plugin.json    # id, contracts.tools
      index.mjs               # registers the tools
    tools/<name>.yaml         # MCP server declarations
  templates/…
```

Bundle content is hashed (sha256 of the canonical tree). Sandbox entries
reference `harnessRef: { name, digest }` so updates are explicit and versioned
with the BOM.

### `sandbox.yaml` addition

```yaml
- name: notebook
  type: openclaw
  enabled: true
  image: quay.io/…/openclaw-openshell@sha256:…
  data:
    name: notebook-data
    mountPath: /sandbox/persist
    retainOnDelete: true
  providers:
    - nvidia
  harnessRef:
    name: data-science-default
    digest: sha256:…
```

### `harness.yaml` (sketch)

```yaml
apiVersion: saw.redhat.com/v1alpha1
kind: HarnessBundle
metadata:
  name: data-science-default
spec:
  agent: openclaw
  skills:
    - name: pattern-author
      path: skills/pattern-author
  plugins:
    - name: saw-echo                # must equal the id in openclaw.plugin.json
      path: plugins/saw-echo
      tools: [saw_echo]             # must equal contracts.tools; verify checks each one
      # governanceProfile: …        # required when the plugin makes network calls
  tools:
    - name: web-search
      path: tools/web-search.yaml
      governanceProfile: web-search
```

## MCP servers (`tools/*.yaml`)

`tools/<name>.yaml` is SAW's declaration of an MCP server. OpenClaw has no
tool-YAML format of its own; it keeps MCP servers in its config, managed with
`openclaw mcp`. The adapter translates the file:

```yaml
apiVersion: saw.redhat.com/v1alpha1
kind: HarnessTool
metadata:
  name: web-search
spec:
  mcp:
    url: https://<host allowed by the web-search profile>/mcp
    transport: streamable-http     # or command + args for a stdio server
    include: [search]              # optional: tools to expose
  governanceProfile: web-search    # SAW only; not passed to OpenClaw
```

| Step | Command |
|---|---|
| Apply | `openclaw mcp set web-search '{"url":"https://…/mcp","transport":"streamable-http"}'`, then `openclaw mcp configure web-search --include 'search'` |
| Revoke | `openclaw mcp unset web-search` |
| Verify | `openclaw mcp list` shows the server with the staged settings (`openclaw mcp doctor web-search --probe` checks it answers) |

The command names come from the OpenClaw 2026.9.5 CLI docs and have not yet
been exercised in the sandbox. The URL's host must be in the endpoints of the
named `governanceProfile`, or OpenShell blocks the traffic.

## Tool plugins (`.mjs` / `.js`)

A tool plugin is a directory with three files. The example below is the
`saw-echo` sample used to test PR #53 (`e2e/samples/saw-echo/`). It has no
dependencies and makes no network calls, so it proves the delivery path on
its own.

`package.json` points OpenClaw at the entry module:

```json
{
  "name": "saw-echo",
  "version": "0.1.0",
  "type": "module",
  "private": true,
  "openclaw": { "extensions": ["./index.mjs"] }
}
```

`openclaw.plugin.json` is the manifest. `contracts.tools` names every tool the
plugin registers:

```json
{
  "id": "saw-echo",
  "name": "SAW echo",
  "description": "Sample tool delivered by a SAW-BOM harness bundle. Echoes its input; no network.",
  "categories": ["other"],
  "contracts": { "tools": ["saw_echo"] },
  "activation": { "onStartup": true },
  "configSchema": { "type": "object", "additionalProperties": false }
}
```

`index.mjs` exports `{ id, register(api) }` and registers the tool. The
parameters are a plain JSON Schema object, so nothing is imported:

```js
const plugin = {
  id: "saw-echo",
  register(api) {
    api.registerTool({
      name: "saw_echo",
      description: "Echo a message back. Proves a harness-delivered .mjs tool loads in the sandbox.",
      parameters: {
        type: "object",
        properties: { message: { type: "string", description: "Text to echo" } },
        required: ["message"],
        additionalProperties: false,
      },
      async execute(_toolCallId, params) {
        const text = `saw-echo: ${params.message}`;
        return { content: [{ type: "text", text }], details: { message: params.message } };
      },
    });
  },
};

export default plugin;
```

### `.mjs` or `.js`

The entry must be an ES module. `.mjs` is always ESM. `.js` is ESM only when
`package.json` sets `"type": "module"`. Use `.mjs`, and keep `"type": "module"`
so any helper `.js` files in the plugin load the same way.

Ship each plugin self-contained. The adapter does not run `npm install` (the
sandbox has no general network access, and the bundle digest must cover all
the code that runs). A plugin that needs libraries is bundled into one file
(for example with esbuild) before it goes into the BOM.

### Install, revoke, verify (OpenClaw 2026.9.5, checked in the sandbox)

| Step | Command | Notes |
|---|---|---|
| Install / update | `openclaw plugins install <staged dir> --force --accept-capabilities` | Copies into `/sandbox/.openclaw/extensions/<id>`, enables it, and applies it to the running gateway ("Applied in Gateway generation N"); no sandbox restart |
| Revoke | `openclaw plugins uninstall <id> --force` | For plugins that left the bundle; removes it cleanly |
| Verify | `openclaw plugins inspect <id>` + a tool list check | `inspect` shows the plugin as installed and enabled (Shape: non-capability) but does not list its tools, so verify must also check every name in `tools:` is registered |

Without `--force`, OpenClaw refuses a local, non-ClawHub path ("Install
cancelled; rerun with --force after reviewing the source"). Without
`--accept-capabilities`, it refuses a plugin that asks for capabilities. In a
non-interactive install the review is the BOM change itself: the bundle digest
pins exactly the source and capabilities that were reviewed in Git.

Stock plugins live under `/usr/local/lib/openclaw/node_modules/openclaw/dist/extensions`
(image plane). BOM plugins live under `/sandbox/.openclaw/extensions`, next to
the `openshell` plugin. The adapter only installs and uninstalls plugins its
bundle names, and never touches stock plugins or ones it did not install.

## Why not only YAML fields on `sandbox.yaml`?

Declarative enablement lists are necessary but insufficient. Skills and tools are
**artifacts** (skill directories, plugin code, MCP specs). They must be versioned content in the BOM,
delivered to the guest, and applied by a harness-specific adapter — otherwise the
harness never sees them and governance cannot reason about what was installed.

## Integration mechanism (the missing VM↔harness link)

| Step | Component | Behavior |
|---|---|---|
| 1. Package | saw-bom Helm | Glob `harness/**` into ConfigMap (or split CM if >1MiB); emit digests |
| 2. Mount | guest mounts | Same virtiofs path as profiles — no new transport required |
| 3. Resolve | `profiles.py` / `inputs` | Validate `harnessRef` + tree digest; attach to sandbox desired state |
| 4. Plan | `reconcile.plan` | New resource key `harness/<ws>/<sb>`; create \| update \| revoke actions |
| 5. Apply | `HarnessAdapter` | Stage files into the managed harness dir; copy skills; `openclaw plugins install` each plugin and `uninstall` removed ones; `openclaw mcp set` each MCP server and `mcp unset` removed ones; restart if required |
| 6. Verify | adapter + status | Read back effective skills, plugins and registered tool names; record `appliedRevision`; fail closed on mismatch |
| 7. Govern | OpenShell + interceptor | Require `governanceProfile` for each networked plugin or MCP server; reject bundle if policy not enrolled |

## Ownership & enforcement boundary

| Concern | Owner | Enforcement |
|---|---|---|
| Skill text / prompts (`SKILL.md`) | SAW-BOM `HarnessBundle` | Harness loads only staged, digest-matched files |
| Tool code (`plugins/<id>/*.mjs`) | SAW-BOM `HarnessBundle` | Adapter installs from the digest-matched staged dir; runs inside the sandbox under OpenShell Landlock/network policy |
| MCP endpoint declaration | SAW-BOM `tools/*.yaml` | Adapter runs `openclaw mcp set` / `unset`; OpenShell policy must allow hosts |
| Network / credential injection | OpenShell + Vault/ESO | Interceptor + provider secrets — unchanged |
| Revoke tool | Remove plugin / MCP entry from bundle + bump digest | Adapter runs `plugins uninstall` / `mcp unset`; policy deny remains belt-and-suspenders |
| User local drift in sandbox | Platform policy (decide) | Recommend: managed paths overwritten each reconcile |

## Implementation plan

### P0 — Contract

Define `HarnessBundle` schema (`skills`, `plugins`, `tools`), `sandbox.harnessRef`,
packaging rules (plugin = `package.json` + `openclaw.plugin.json` + ESM entry,
no dependencies to fetch), and content-address digests.

**Exit:** Schema and examples reviewed; digest algorithm specified.

### P1 — Chart packaging

Add `charts/saw-bom/harness/` with `skills/`, `plugins/` and `tools/`; extend ConfigMap keys;
size/digest validation in Helm.

**Exit:** Chart renders ConfigMap(s) containing bundle + digest index.

### P2 — Parser / guest inputs

Extend `profiles.py` and `saw_guest/inputs` to resolve harness bundles from
virtiofs mounts.

**Exit:** Unit tests parse a bundle, reject digest mismatch; mount bytes appear
under `/run/saw/profiles`.

### P3 — OpenClaw adapter (first harness)

Materialize skills into the sandbox, install/uninstall tool plugins with the
OpenClaw CLI, register MCP servers with `openclaw mcp set`, define restart policy, verify effective set.

**Exit:** Adapter apply/verify APIs covered by offline tests against the OpenClaw
CLI contract, and `saw_echo` is callable in a live sandbox after a BOM-only change.

### P4 — Reconcile loop

Treat harness revision as a first-class resource; idempotent apply/revoke;
status + `appliedRevision`.

**Exit:** Same revision is a no-op; digest change updates effective set; removal
revokes.

### P5 — Governance bridge

Map tool endpoints → OpenShell policy profiles; fail closed when policy is missing.

**Exit:** Bundle with missing `governanceProfile` enrollment is rejected before
mutation.

### P6 — Qualification

Add/update/revoke a skill on a live sandbox via BOM-only change; prove no
SSH/setup Job.

**Exit:** Git → Argo → virtiofs → guest reconcile → harness effective state;
restart matrix documented (live vs harness restart vs sandbox replace).

## Decisions to lock before coding

| Decision | Recommendation | Why |
|---|---|---|
| First harness | OpenClaw only | Enabled `notebook` sandbox; NemoClaw can reuse adapter later |
| Artifact location | `charts/saw-bom/harness/<bundle>/` | Versioned with profiles; shared across workspaces |
| Delivery | Reuse virtiofs ConfigMaps | Guest path already exists; avoid new transport |
| Large skills trees | Split ConfigMaps + digest index | 1MiB CM limit; keep index small and validated |
| Tool format | OpenClaw plugins (`.mjs`), not YAML | OpenClaw registers tools only from plugin code or MCP servers |
| Plugin install | `plugins install --force --accept-capabilities` from the staged dir | Non-interactive; the digest-pinned Git review replaces the prompt |
| Drift policy | Overwrite managed paths | Admin intent wins; user state stays on `/sandbox/persist` outside managed dirs |
| Sandbox disabled path | Defer NemoClaw until OpenClaw qualified | Mounted-path sandbox lifecycle is still incomplete — sequence after/with that work |

## Sequencing note

Mounted-path sandbox create/replace is still unfinished in the current installer
path (`SandboxApplyNotImplemented`). Harness reconcile can be built and
unit-tested against the adapter contract in parallel, but end-to-end
qualification depends on sandbox lifecycle working on the guest-mounted BOM
path (or using the legacy `--profiles-dir` path as an interim proving ground).

Keep executable reconciler/adapter code in the versioned installer artifact
(`installer/apply_bom.py` / guest bundle). Do not ship new installer logic
inside the BOM ConfigMap. Tool plugins are the one kind of code a bundle
carries, and they are different: they run inside the sandbox as the agent,
under OpenShell policy, not as root in the guest. The adapter only copies and
installs them; it never executes them itself.

## Sources

- `docs/astra-saw-deployment-discovery.md` §5
- `docs/bom-architecture.md`
- `charts/saw-bom/`
- `guest/saw_guest/{inputs,reconcile,mounts}.py`
- `installer/apply_bom.py`
- `cli/src/openshell_saw/profiles.py`
- `e2e/samples/saw-echo/` (sample tool plugin) and `e2e/pr53-review.md` §1b
