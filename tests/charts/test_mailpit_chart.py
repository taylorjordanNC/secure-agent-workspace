"""charts/mailpit: on-cluster SMTP sink + web UI for the recorded demo."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "mailpit"
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="helm is not installed")


def render(*args):
    out = subprocess.run([HELM, "template", "mailpit", str(CHART), "-n", "openshell-agents", *args],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def test_default_renders_deployment_service_and_route():
    docs = render()
    by_kind = {d["kind"]: d for d in docs}
    assert {"Deployment", "Service", "Route"} <= set(by_kind)
    (svc,) = [d for d in docs if d["kind"] == "Service"]
    ports = {p["name"]: p["port"] for p in svc["spec"]["ports"]}
    assert ports == {"smtp": 1025, "ui": 8025}
    (dep,) = [d for d in docs if d["kind"] == "Deployment"]
    (ctr,) = dep["spec"]["template"]["spec"]["containers"]
    assert ctr["image"].startswith("axllent/mailpit:")
    assert dep["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] == "mailpit"
    (route,) = [d for d in docs if d["kind"] == "Route"]
    assert route["spec"]["to"]["name"] == "mailpit"
    assert route["spec"]["port"]["targetPort"] == "ui"
    # SMTP is never routed: the single Route is the UI.
    assert [d["metadata"]["name"] for d in docs if d["kind"] == "Route"] == ["mailpit-ui"]


def test_route_flag_off_drops_only_the_route():
    docs = render("--set", "route.enabled=false")
    assert {d["kind"] for d in docs} == {"Deployment", "Service"}


def test_storage_can_be_disabled():
    docs = render("--set", "storage.size=")
    (dep,) = [d for d in docs if d["kind"] == "Deployment"]
    assert "volumes" not in dep["spec"]["template"]["spec"]
    (dep,) = [d for d in render() if d["kind"] == "Deployment"]
    assert dep["spec"]["template"]["spec"]["volumes"][0]["emptyDir"]["sizeLimit"] == "64Mi"


def test_resources_are_light():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    requests = values["resources"]["requests"]
    assert requests["cpu"] <= "50m" or int(requests["cpu"].removesuffix("m")) <= 50
    assert int(requests["memory"].removesuffix("Mi")) <= 64
