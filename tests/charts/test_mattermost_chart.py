"""charts/mattermost: on-cluster chat provider + web UI for the GTC demo."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "mattermost"
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="helm is not installed")


def render(*args):
    out = subprocess.run([HELM, "template", "mattermost", str(CHART), "-n", "openshell-agents", *args],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def test_default_renders_deployments_services_and_route():
    docs = render()
    by_kind = {}
    for d in docs:
        by_kind.setdefault(d["kind"], []).append(d)
    assert {"Deployment", "Service", "Route"} <= set(by_kind)
    assert len(by_kind["Deployment"]) == 2  # mattermost + postgres
    assert len(by_kind["Service"]) == 2  # mattermost + mattermost-postgres
    names = {d["metadata"]["name"] for d in docs}
    assert {"mattermost", "mattermost-postgres", "mattermost-ui"} <= names
    (mm_svc,) = [d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "mattermost"]
    ports = {p["name"]: p["port"] for p in mm_svc["spec"]["ports"]}
    assert ports == {"http": 8065}
    (pg_svc,) = [d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "mattermost-postgres"]
    ports = {p["name"]: p["port"] for p in pg_svc["spec"]["ports"]}
    assert ports == {"postgres": 5432}
    (route,) = [d for d in docs if d["kind"] == "Route"]
    assert route["spec"]["to"]["name"] == "mattermost"
    assert route["spec"]["port"]["targetPort"] == "http"
    assert route["spec"]["tls"]["termination"] == "edge"
    assert [d["metadata"]["name"] for d in docs if d["kind"] == "Route"] == ["mattermost-ui"]


def test_mattermost_deployment_config_and_probes():
    (mm,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "mattermost"]
    (ctr,) = mm["spec"]["template"]["spec"]["containers"]
    assert ctr["image"].startswith("mattermost/mattermost-enterprise-edition:")
    env = {e["name"]: e["value"] for e in ctr["env"]}
    assert env["MM_SQLSETTINGS_DRIVERNAME"] == "postgres"
    assert env["MM_SQLSETTINGS_DATASOURCE"].startswith("postgres://mmuser:")
    assert "mattermost-postgres:5432/mattermost" in env["MM_SQLSETTINGS_DATASOURCE"]
    assert env["MM_SERVICESETTINGS_LISTENADDRESS"] == ":8065"
    assert env["MM_SERVICESETTINGS_SITEURL"].startswith("https://mattermost-ui-")
    assert env["MM_SERVICESETTINGS_SITEURL"].endswith(".apps.openshift.local")
    for probe in ("livenessProbe", "readinessProbe"):
        p = ctr[probe]["httpGet"]
        assert p["path"] == "/api/v4/system/ping"
        assert p["port"] == "http"


def test_postgres_deployment_present():
    (pg,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "mattermost-postgres"]
    (ctr,) = pg["spec"]["template"]["spec"]["containers"]
    assert ctr["image"].startswith("postgres:")
    env = {e["name"]: e["value"] for e in ctr["env"]}
    assert env["POSTGRES_DB"] == "mattermost"
    assert env["POSTGRES_USER"] == "mmuser"


def test_route_flag_off_drops_only_the_route():
    docs = render("--set", "route.enabled=false")
    kinds = {d["kind"] for d in docs}
    assert kinds == {"Deployment", "Service"}
    # Without the route there is no auto-derived site URL.
    (mm,) = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "mattermost"]
    (ctr,) = mm["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e["value"] for e in ctr["env"]}
    assert "MM_SERVICESETTINGS_SITEURL" not in env


def test_storage_can_be_disabled():
    docs = render("--set", "storage.mattermostSize=", "--set", "storage.postgresSize=")
    for d in docs:
        if d["kind"] == "Deployment":
            assert "volumes" not in d["spec"]["template"]["spec"]
    (mm,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "mattermost"]
    assert mm["spec"]["template"]["spec"]["volumes"][0]["emptyDir"]["sizeLimit"] == "1Gi"
    (pg,) = [d for d in render() if d["kind"] == "Deployment" and d["metadata"]["name"] == "mattermost-postgres"]
    assert pg["spec"]["template"]["spec"]["volumes"][0]["emptyDir"]["sizeLimit"] == "1Gi"


def test_resources_are_light():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    requests = values["resources"]["requests"]
    assert int(requests["cpu"].removesuffix("m")) <= 250
    assert int(requests["memory"].removesuffix("Mi")) <= 512
    pg_requests = values["postgresResources"]["requests"]
    assert int(pg_requests["cpu"].removesuffix("m")) <= 50
    assert int(pg_requests["memory"].removesuffix("Mi")) <= 128
