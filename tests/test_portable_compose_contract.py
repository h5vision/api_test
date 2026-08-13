from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_bootstraps_a_fresh_clone_without_host_specific_defaults() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "C:/Users/PC2412" not in compose
    assert "192.168.0.7" not in compose
    assert 'source: "${PROJECT_PATH:-.}"' in compose
    assert "migrate:" in compose
    assert "command:\n      - -m\n      - alembic\n      - upgrade\n      - head" in compose
    assert "migrate:\n        condition: service_completed_successfully" in compose
    assert "QDRANT_API_KEY_FILE: /run/vision-secrets/qdrant_api_key" in compose
    assert "REDIS_PASSWORD_FILE: /run/vision-secrets/redis_password" in compose
    assert "SNAPSHOT_HYDRATION_TOKEN_FILE: /run/vision-secrets/snapshot_hydration_token" in compose
    assert "ipam:" not in compose


def test_admin_proxy_uses_a_private_network_and_public_header_stripping() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy" / "nginx" / "admin.conf").read_text(encoding="utf-8")
    security = (
        ROOT / "deploy" / "traefik" / "dynamic" / "security.yml"
    ).read_text(encoding="utf-8")

    assert "admin-internal:" in compose
    assert "api-admin" in compose
    assert "http://api-admin:8000" in nginx
    assert "strip-admin-proxy-header" in security
    assert 'X-Vision-Admin-Proxy: ""' in security
