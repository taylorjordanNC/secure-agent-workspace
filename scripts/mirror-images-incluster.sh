#!/usr/bin/env bash
# Mirror images from quay.io to the internal registry using in-cluster skopeo Jobs.
# Use this on macOS where podman-machine TCP connections drop mid-upload for large layers.
set -euo pipefail

BUILD_NS="${BUILD_NS:-openshell-agents}"
QUAY_REPO="${QUAY_REPO:-quay.io/rh-ai-quickstart}"
VERSION="${OPENSHELL_VERSION:-v0.0.103}"
IMAGES="${IMAGES:-openshell-gateway openshell-gateway-docker nemoclaw-sandbox nemoclaw-cli}"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Setting up image-mirror ServiceAccount..."
oc apply -n "${BUILD_NS}" -f "${SCRIPTS_DIR}/mirror-images-rbac.yaml"
# nonroot SCC (not anyuid) is sufficient: HOME and XDG_RUNTIME_DIR are redirected
# to /tmp in the Job spec so skopeo never touches /run/containers (the root-only
# path that previously forced anyuid). The namespace's restricted PodSecurity still
# blocks root; nonroot overrides it for this SA without granting broader privileges.
oc adm policy add-scc-to-user nonroot -z image-mirror -n "${BUILD_NS}" 2>/dev/null || true

for IMAGE in ${IMAGES}; do
  export IMAGE BUILD_NS QUAY_REPO VERSION
  # Idempotent skip: a Completed Job means the image is already mirrored
  # (skopeo verified the copy). Deleting a Completed job to re-run wastes
  # minutes; this also lets a GitOps-managed copy and a manual Module 3
  # `make copy-images` coexist — whichever ran first wins, the other is a
  # no-op.
  if oc -n "${BUILD_NS}" get job "mirror-${IMAGE}" \
      -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null | grep -q True; then
    echo "${IMAGE}:${VERSION} already mirrored."
    # The image may have been mirrored by the order's chart without the
    # :latest tag the golden-image DV import pulls — ensure the tag on the
    # skip path too, otherwise the import crash-loops and the VM never
    # provisions.
    if ! oc -n "${BUILD_NS}" get is "${IMAGE}" -o jsonpath='{.spec.tags[*].name}' 2>/dev/null | grep -qw latest; then
      oc tag "${BUILD_NS}/${IMAGE}:${VERSION}" "${BUILD_NS}/${IMAGE}:latest" 2>/dev/null || \
        echo "WARN: could not tag ${IMAGE}:latest on the skip path — the golden-image DV import will crash-loop until it exists" >&2
    fi
    continue
  fi
  echo "Mirroring ${IMAGE}:${VERSION}..."
  oc delete job "mirror-${IMAGE}" -n "${BUILD_NS}" 2>/dev/null || true
  # Only substitute template vars; leave runtime shell vars (e.g. ${TOKEN}) intact
  envsubst '${IMAGE} ${BUILD_NS} ${QUAY_REPO} ${VERSION}' \
    < "${SCRIPTS_DIR}/mirror-images-job.yaml" \
    | oc apply -n "${BUILD_NS}" -f -
  oc -n "${BUILD_NS}" wait --for=condition=complete \
    job/"mirror-${IMAGE}" --timeout=600s
  # Tag :latest — the setup Job's golden-image DV import pulls it; a missing
  # :latest deadlocks the import in a crash loop and the VM never provisions.
  # Retry: the source tag can take a beat to become readable after the job
  # completes. Never swallow this failure silently.
  local_tag_ok=0
  for _ in 1 2 3; do
    if oc tag "${BUILD_NS}/${IMAGE}:${VERSION}" "${BUILD_NS}/${IMAGE}:latest" 2>/dev/null; then
      local_tag_ok=1
      break
    fi
    sleep 10
  done
  if [ "$local_tag_ok" -ne 1 ] || \
     ! oc -n "${BUILD_NS}" get is "${IMAGE}" -o jsonpath='{.spec.tags[*].name}' 2>/dev/null | grep -qw latest; then
    echo "WARN: ${IMAGE}:latest tag missing after retries — the golden-image DV import will crash-loop until it exists" >&2
  fi
  echo "  ${IMAGE} done."
done

echo "All images mirrored (tag: ${VERSION}, also tagged as :latest)."
