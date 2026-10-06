#!/usr/bin/env bash
# Seed the cluster's Vault with the SAW secret values.
#
# Companion to values-secret.yaml: reads the same file the Validated Patterns
# flow loads (make load-secrets) and writes every entry into the Vault the
# gitops deployment manages. Use this with the GitOps path of install, where
# there is no pattern utility container to run Ansible.
#
# The Vault root token is fetched from the `vault-init` Secret (created by the
# vault-config Job) and the seed runs via `oc exec` inside the vault pod —
# no local vault CLI, no port-forward, and key material never appears in an
# argument vector (it is piped to `vault kv put -` as JSON on stdin).
#
# Usage:
#   ./scripts/seed-vault.sh                       # seed the shared prefix
#   VAULT_PREFIX=secret/hub/saw-alice ./scripts/seed-vault.sh
#
# Env overrides (all optional):
#   VALUES_SECRET   values file (default ~/values-secret.yaml)
#   VAULT_PREFIX    target KV path prefix (default secret/hub; per-user
#                   prefixes look like secret/hub/saw-<name> and match the
#                   saw-users chart's per-user vaultPrefix)
#   VAULT_POD       vault pod (default vault-0)
#
# Requires: oc (logged in, tree converged so vault-init exists), jq,
# python3 with PyYAML, ssh-keygen.
set -euo pipefail

VALUES_SECRET="${VALUES_SECRET:-$HOME/values-secret.yaml}"
VAULT_PREFIX="${VAULT_PREFIX:-secret/hub}"
VAULT_POD="${VAULT_POD:-vault-0}"
VAULT_NS="${VAULT_NS:-vault}"

for tool in oc jq python3 ssh-keygen; do
  command -v "$tool" >/dev/null 2>&1 || { echo "required tool missing: $tool" >&2; exit 1; }
done
[[ -f "$VALUES_SECRET" ]] || {
  echo "Values file not found: $VALUES_SECRET" >&2
  echo "Copy values-secret.yaml.template to ~/values-secret.yaml and fill it in first." >&2
  exit 1
}

# --- resolve the secret entries out of values-secret.yaml --------------------
# Each entry: {name, fields: [{name, value|path}], vaultPrefixes?}. Fields with
# a path are read from the control node; fields with a value are inline. The
# output is one JSON document per secret: {name, prefixes: [...], data: {...}}.
PY_EXTRACT="$(mktemp "${TMPDIR:-/tmp}/seed-vault.XXXXXX.py")"
SECRETS_FILE="$(mktemp "${TMPDIR:-/tmp}/seed-vault.XXXXXX.json")"
trap 'rm -f "$PY_EXTRACT" "$SECRETS_FILE"' EXIT
cat > "$PY_EXTRACT" <<'PY'
import json, os, sys, yaml

doc = yaml.safe_load(open(sys.argv[1])) or {}
secrets = []

for entry in doc.get("secrets") or []:
    name = entry.get("name")
    if not name:
        continue
    data = {}
    for field in entry.get("fields") or []:
        fname = field.get("name")
        if not fname:
            continue
        if "path" in field and field["path"]:
            path = os.path.expanduser(field["path"])
            if not os.path.isfile(path):
                print(f"missing file for {name}/{fname}: {path}", file=sys.stderr)
                sys.exit(1)
            data[fname] = open(path).read().strip()
        else:
            data[fname] = str(field.get("value", ""))
    prefixes = entry.get("vaultPrefixes") or []
    secrets.append({"name": name, "prefixes": prefixes, "data": data})

for s in secrets:
    print(json.dumps(s))
PY
python3 "$PY_EXTRACT" "$VALUES_SECRET" > "$SECRETS_FILE" || exit 1

SECRETS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && SECRETS+=("$line")
done < "$SECRETS_FILE"

[[ ${#SECRETS[@]} -gt 0 ]] || { echo "No secrets in $VALUES_SECRET — nothing to seed." >&2; exit 1; }

# --- vault readiness: root token + unsealed ----------------------------------
ROOT_TOKEN="$(oc -n "$VAULT_NS" get secret vault-init -o jsonpath='{.data.root_token}' 2>/dev/null | base64 -d || true)"
[[ -n "$ROOT_TOKEN" ]] || {
  echo "Could not read the root token from secret/vault-init -n $VAULT_NS." >&2
  echo "Is the SAW gitops tree converged (the vault-config Job creates it)?" >&2
  exit 1
}

STATUS="$(oc -n "$VAULT_NS" exec "$VAULT_POD" -- env VAULT_ADDR=http://127.0.0.1:8200 \
  VAULT_TOKEN="$ROOT_TOKEN" vault status -format=json 2>/dev/null || true)"
echo "$STATUS" | jq -e '.initialized == true and .sealed == false' >/dev/null || {
  echo "Vault is not ready (initialized+unsealed). The vault-config Job owns init/unseal —" >&2
  echo "re-run this script once it completes." >&2
  exit 1
}

# --- seed --------------------------------------------------------------------
echo "Seeding vault at $VAULT_PREFIX/* (via $VAULT_POD)"

for secret in "${SECRETS[@]}"; do
  name="$(echo "$secret" | jq -r '.name')"
  data="$(echo "$secret" | jq -c '.data')"
  # vaultPrefixes in values-secret.yaml are relative to secret/data (the VP
  # convention); this script's VAULT_PREFIX is the full KV path.
  prefixes="$(echo "$secret" | jq -r '.prefixes | if length > 0 then .[] else empty end' \
    | sed 's|^|secret/|')"
  if [[ -z "$prefixes" ]]; then
    targets=("$VAULT_PREFIX")
  else
    targets=()
    while IFS= read -r prefix; do
      [[ -n "$prefix" ]] && targets+=("$prefix")
    done <<< "$prefixes"
  fi

  for prefix in "${targets[@]}"; do
    printf '%s' "$data" | oc -n "$VAULT_NS" exec -i "$VAULT_POD" -- \
      env VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN="$ROOT_TOKEN" \
      vault kv put "$prefix/$name" - >/dev/null
    echo "  seeded $prefix/$name"
  done
done

echo "Done. The ExternalSecrets (pattern-secrets) pull these on their next refresh."
echo "Force one if needed: oc -n openshell-agents annotate externalsecurity <name> \\"
echo "  external-secrets.io/force-sync=\"true\" --overwrite"
