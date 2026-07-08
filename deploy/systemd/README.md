# Systemd Deployment Example

This is a hosting-neutral VPS layout for the public snippet-search server. It keeps the app bound to `127.0.0.1:8765`; nginx or another HTTPS reverse proxy should be the only internet-facing process.

Expected paths:

- `/srv/thread-search/app`: checked-out app code and `.venv`
- `/srv/thread-search/artifact`: private copy of `dist/thread-search-public/thread-search.sqlite` and `manifest.json`
- `/etc/thread-search/thread-search.env`: public contact and removal-request metadata

Install sketch:

```sh
sudo install -d -o threadsearch -g threadsearch /srv/thread-search/app /srv/thread-search/artifact
sudo install -d -m 0750 /etc/thread-search
sudo install -m 0640 deploy/systemd/thread-search.env.example /etc/thread-search/thread-search.env
sudo install -m 0644 deploy/systemd/thread-search.service.example /etc/systemd/system/thread-search.service
sudo systemctl daemon-reload
sudo systemctl enable --now thread-search.service
```

Before enabling the service, edit `/etc/thread-search/thread-search.env` and copy only the private backend artifact files into `/srv/thread-search/artifact`. Do not copy `data/`, raw HTML, extracted JSONL, or the artifact directory into a public web root.

After the reverse proxy is live, run:

```sh
.venv/bin/thread-search public-smoke --base-url https://your-domain.tld --require-artifact-manifest --probe Soviet --probe Cuba --claim-pair Cuba communist
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --public-base-url https://your-domain.tld --claim-pair Cuba communist --json --out data/production-audit.json
```
