from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_mounts_artifact_privately_and_readonly() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "127.0.0.1:8765:8765" in compose
    assert "./dist/thread-search-public:/data:ro" in compose
    assert "THREAD_SEARCH_ARTIFACT_MANIFEST: /data/manifest.json" in compose
    assert 'THREAD_SEARCH_PUBLIC_CONTACT: "${THREAD_SEARCH_PUBLIC_CONTACT:?' in compose
    assert 'THREAD_SEARCH_REMOVAL_REQUEST_URL: "${THREAD_SEARCH_REMOVAL_REQUEST_URL:?' in compose
    assert "read_only: true" in compose
    assert "cap_drop:" in compose
    assert "no-new-privileges:true" in compose


def test_dockerfile_requires_launch_and_artifact_manifest() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "THREAD_SEARCH_ARTIFACT_MANIFEST=/data/manifest.json" in dockerfile
    assert "THREAD_SEARCH_PUBLIC_CONTACT=" in dockerfile
    assert "THREAD_SEARCH_REMOVAL_REQUEST_URL=" in dockerfile
    assert "--require-launch-ready" in dockerfile
    assert "--require-artifact-manifest" in dockerfile
    assert "--artifact-manifest" in dockerfile
    assert "THREAD_SEARCH_ARTIFACT_MANIFEST" in dockerfile
    assert "--public-contact" in dockerfile
    assert "THREAD_SEARCH_PUBLIC_CONTACT" in dockerfile
    assert "--removal-request-url" in dockerfile
    assert "THREAD_SEARCH_REMOVAL_REQUEST_URL" in dockerfile


def test_deployment_docs_keep_direct_docker_bound_to_loopback() -> None:
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    assert "-p 127.0.0.1:8765:8765" in deployment
    assert "-p 8765:8765" not in deployment
    assert "deploy/nginx-thread-search.conf.example" in deployment
    assert ".venv/bin/thread-search deploy-bundle" in deployment
    assert ".venv/bin/thread-search deploy-bundle-check" in deployment
    assert "thread-search-app.tar.gz" in deployment
    assert "thread-search-private-artifact.tar.gz" in deployment
    assert "excludes `data/`, `dist/`, `.venv/`, and `.git/`" in deployment
    assert "verifies the bundle manifest, tarball checksums and sizes" in deployment
    assert "--deploy-bundle-manifest dist/deploy-bundles/deploy-bundle-manifest.json" in deployment
    assert "deploy/master-deploy.sh" in deployment
    assert "origin/master" in deployment
    assert "THREAD_SEARCH_PUBLIC_BASE_URL" in deployment
    assert "flyctl deploy --remote-only" in deployment


def test_master_deploy_script_deploys_only_verified_master() -> None:
    script = (ROOT / "deploy" / "master-deploy.sh").read_text(encoding="utf-8")

    assert "THREAD_SEARCH_DEPLOY_BRANCH:-master" in script
    assert 'git status --short' in script
    assert 'refs/remotes/$DEPLOY_REMOTE/$DEPLOY_BRANCH' in script
    assert 'git merge --ff-only "$DEPLOY_REMOTE/$DEPLOY_BRANCH"' in script
    assert 'dist/thread-search-public' in script
    assert 'thread-search.sqlite manifest.json README.deploy.txt' in script
    assert 'python3 -m venv .venv' in script
    assert '"$PYTHON_BIN" -m pytest -q' in script
    assert '"$PYTHON_BIN" -m planquest.cli deploy-bundle' in script
    assert '"$PYTHON_BIN" -m planquest.cli deploy-bundle-check' in script
    assert "--remote-only" in script
    assert 'public-smoke' in script
    assert 'data/deployments' in script


def test_nginx_example_keeps_app_private_and_rate_limited() -> None:
    nginx = (ROOT / "deploy" / "nginx-thread-search.conf.example").read_text(encoding="utf-8")

    assert "proxy_pass http://127.0.0.1:8765" in nginx
    assert "limit_req_zone" in nginx
    assert "limit_req zone=thread_search_api" in nginx
    assert 'X-Robots-Tag "noindex, nofollow"' in nginx
    assert "manifest\\.json" in nginx
    assert "planquest\\.sqlite" in nginx


def test_systemd_example_keeps_app_loopback_and_manifest_gated() -> None:
    service = (ROOT / "deploy" / "systemd" / "thread-search.service.example").read_text(encoding="utf-8")

    assert "--host 127.0.0.1" in service
    assert "--port 8765" in service
    assert "--require-launch-ready" in service
    assert "--require-artifact-manifest" in service
    assert "--artifact-manifest /srv/thread-search/artifact/manifest.json" in service
    assert "--db /srv/thread-search/artifact/thread-search.sqlite" in service
    assert "--public-contact ${THREAD_SEARCH_PUBLIC_CONTACT}" in service
    assert "--removal-request-url ${THREAD_SEARCH_REMOVAL_REQUEST_URL}" in service
    assert "--probe Soviet" in service
    assert "--probe Cuba" in service
    assert "--private-fulltext" not in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ReadOnlyPaths=/srv/thread-search/app /srv/thread-search/artifact" in service
    assert "CapabilityBoundingSet=" in service


def test_systemd_env_example_requires_replacement_contact_values() -> None:
    env = (ROOT / "deploy" / "systemd" / "thread-search.env.example").read_text(encoding="utf-8")

    assert "THREAD_SEARCH_PUBLIC_CONTACT=" in env
    assert "THREAD_SEARCH_REMOVAL_REQUEST_URL=" in env
    assert "placeholder" in env
    assert "your-domain.tld" in env


def test_systemd_readme_keeps_private_artifact_and_public_audit() -> None:
    readme = (ROOT / "deploy" / "systemd" / "README.md").read_text(encoding="utf-8")

    assert "127.0.0.1:8765" in readme
    assert "/srv/thread-search/artifact" in readme
    assert "Do not copy `data/`, raw HTML, extracted JSONL" in readme
    assert "public-smoke --base-url https://your-domain.tld" in readme
    assert "--json --out data/production-audit.json" in readme


def test_dockerignore_excludes_private_corpus_and_artifacts() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert "data" in ignored
    assert "dist/*" in ignored


def test_ci_workflow_runs_public_safe_tests_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v4" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "python-version: \"3.12\"" in workflow
    assert "python -m pip install -e '.[dev]'" in workflow
    assert "pytest -q" in workflow
    assert "data/" not in workflow
    assert "dist/" not in workflow
