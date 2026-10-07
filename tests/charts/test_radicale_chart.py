# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""charts/radicale: on-cluster calendar provider + web UI for the GTC demo."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "radicale"
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="helm is not installed")


def render(*args):
    out = subprocess.run([HELM, "template", "radicale", str(CHART), "-n", "openshell-agents", *args],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def test_default_renders_service_deployment_and_route():
    docs = render()
    by_kind = {}
    for d in docs:
        by_kind.setdefault(d["kind"], []).append(d)
    assert {"Deployment", "Service", "Route", "ConfigMap"} <= set(by_kind)
    assert len(by_kind["Deployment"]) == 1
    (svc,) = by_kind["Service"]
    assert svc["metadata"]["name"] == "radicale"
    assert svc["spec"]["type"] == "ClusterIP"
    ports = {p["name"]: p["port"] for p in svc["spec"]["ports"]}
    assert ports == {"cal": 5232}
    (route,) = by_kind["Route"]
    assert route["metadata"]["name"] == "radicale-ui"
    assert route["spec"]["to"]["name"] == "radicale"
    assert route["spec"]["port"]["targetPort"] == "cal"
    assert route["spec"]["tls"]["termination"] == "edge"


def test_radicale_deployment_config_and_probes():
    (rad,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "radicale"]
    (ctr,) = rad["spec"]["template"]["spec"]["containers"]
    assert ctr["image"].startswith("kozea/radicale:")
    assert ctr["ports"][0]["containerPort"] == 5232
    assert ctr["args"] == ["--config", "/config/config"]
    for probe in ("livenessProbe", "readinessProbe"):
        p = ctr[probe]["httpGet"]
        assert p["path"] == "/.web/"
        assert p["port"] == "cal"
    # Server config: auth-free demo, TLS terminated at the Route.
    (cm,) = [d for d in render() if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "radicale-config"]
    config = cm["data"]["config"]
    assert "type = none" in config
    assert "ssl = false" in config
    assert "filesystem_folder = /var/lib/radicale/collections" in config


def test_security_contexts_and_probe_delays():
    (rad,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "radicale"]
    pod_spec = rad["spec"]["template"]["spec"]
    (ctr,) = pod_spec["containers"]
    # kozea/radicale image files are owned by uid 1000 (user "radicale").
    assert pod_spec["securityContext"]["fsGroup"] == 1000
    assert ctr["securityContext"]["runAsUser"] == 1000
    (init,) = pod_spec["initContainers"]
    assert init["name"] == "seed-calendar"
    assert init["securityContext"]["runAsUser"] == 1000
    # Probe delays are configurable and must not fire during first-start
    # collection creation.
    assert ctr["livenessProbe"]["initialDelaySeconds"] == 15
    assert ctr["readinessProbe"]["initialDelaySeconds"] == 5


def test_seed_events_render_into_seed_configmap():
    (seed,) = [d for d in render() if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "radicale-seed"]
    ics_keys = sorted(k for k in seed["data"] if k.endswith(".ics"))
    assert len(ics_keys) == 4
    assert "props.json" in seed["data"]
    joined = "\n".join(seed["data"][k] for k in ics_keys)
    for summary in ("AI Platform sync", "GTC Berlin rehearsal", "Security review — SAW governance"):
        assert summary in joined
    assert all("BEGIN:VEVENT" in seed["data"][k] for k in ics_keys)
    # The initContainer seeds into the Radicale collection layout.
    (rad,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "radicale"]
    (init,) = rad["spec"]["template"]["spec"]["initContainers"]
    script = "\n".join(init["command"])
    assert "/var/lib/radicale/collections/collection-root/demo/personal" in script
    assert ".Radicale.props" in script
    assert "skipping seed" in script  # never overwrite user-created data


def test_route_flag_off_drops_only_the_route():
    docs = render("--set", "route.enabled=false")
    kinds = {d["kind"] for d in docs}
    assert kinds == {"Deployment", "Service", "ConfigMap"}


def test_seed_can_be_disabled():
    docs = render("--set", "seed.enabled=false")
    assert not [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "radicale-seed"]
    (rad,) = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "radicale"]
    assert rad["spec"]["template"]["spec"].get("initContainers", []) == []


def test_storage_can_be_disabled():
    docs = render("--set", "storage.size=")
    for d in docs:
        if d["kind"] == "Deployment":
            spec = d["spec"]["template"]["spec"]
            assert not [v for v in spec["volumes"] if v["name"] == "data"]
            # Seeding needs the data volume; it must be dropped with it.
            assert spec.get("initContainers", []) == []


def test_resources_are_light():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    requests = values["resources"]["requests"]
    assert int(requests["cpu"].removesuffix("m")) <= 50
    assert int(requests["memory"].removesuffix("Mi")) <= 64
