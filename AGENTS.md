# IncidentLab

IncidentLab is an AI investigator for one Docker application on a Linux test VM, built for the Meta Global AI Developer Hackathon (Indie track). Submissions close Wednesday 4 November 2026 at 9 PM ET.

When uploads stop working, IncidentLab uses Muse Spark through Meta Model API to choose read-only diagnostic checks and report the likely cause, citing the evidence it collected. After a person approves, it runs one predefined repair and verifies recovery: it uploads a new file, downloads it and compares SHA-256 hashes.

## How to work in this repo

- Do one step at a time. When a step is done, stop and report what changed, the commands you ran and their output.
- Leave a way to re-run every check you do (a script or a test), not only a one-off command.
- Don't commit. The user reviews the diff and commits.
- Don't use sudo; the user has done the root setup below. If something needs root, or the sandbox blocks a command, stop and say so instead of working around it.
- If the environment doesn't match this file (names, paths, ports), stop and ask rather than guess.
- Keep it small: Python 3.12, few dependencies, no extra services.
- Use synthetic data only. Never add real logs, hostnames, credentials or code from other projects.
- The Meta API key is in `MODEL_API_KEY`. Never print, log or commit it.

## Environment (set up by the user)

An Ubuntu VM with rootful Docker. The VM user is in the `docker` group. The setup, for reference:

```bash
# Rootful Docker. Log out and back in afterwards so the group change applies.
curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh
sudo usermod -aG docker "$USER"

# 512 MB ext4 test filesystem for the app's data, mounted at boot
sudo fallocate -l 512M /srv/lab-data.img
sudo mkfs.ext4 -q -F /srv/lab-data.img
sudo mkdir -p /srv/lab-data
echo '/srv/lab-data.img /srv/lab-data ext4 loop 0 0' | sudo tee -a /etc/fstab
sudo mount /srv/lab-data
sudo mkdir -p /srv/lab-data/uploads /srv/lab-data/tmp
sudo chown -R 1000:1000 /srv/lab-data

# Python environment for investigate.py (run in the project folder)
sudo apt-get update && sudo apt-get install -y python3-venv
python3 -m venv .venv && .venv/bin/pip install openai
```

## Repo layout (target)

- `investigate.py`: the investigator. It exists and has been tested against a mock API only. Read it before changing anything near it.
- `lab/compose.yaml` and `lab/app/`: the upload app and Postgres.
- `lab/faults.py`: `inject`, `reset` and `status` for each fault. The only code that knows which fault is active.
- `repair.py`: the approval prompt, the predefined repairs and the verifier.
- `eval/`: cases, the runner, the rule-based baseline and results.
- `README.md`: architecture, how to run, measured results and limitations.

## The lab

- `lab/compose.yaml` sets `name: incidentlab` with services `app` and `db`, so the containers are `incidentlab-app-1` and `incidentlab-db-1`. investigate.py expects these names.
- `db`: `postgres:16` with a `pg_isready` healthcheck.
- `app`: FastAPI with uvicorn and psycopg 3 on `python:3.12-slim`, running as UID and GID 1000. Never run it as root: root ignores permission bits and would hide the permissions fault. Publish container port 8000 on `localhost:8080`.
- Mount `/srv/lab-data` at `/data`. Each upload is written to `/data/tmp/<random>.part`, hashed, moved into `/data/uploads/` and recorded in Postgres (id, name, size, SHA-256). If the database insert fails, remove the moved file.
- Endpoints: `POST /upload` (returns the id and SHA-256), `GET /files/{id}` and `GET /health`.
- `/health` checks the database and that `/data/uploads` is writable. It returns only 200 `ok` or 503 `unhealthy`, never the reason. Failures go to the app's log as ordinary exceptions, the way a real app logs them.

## Faults, repairs and verification

| Cause | First variant to inject | Repair (`REPAIR_FOR_CAUSE` in investigate.py) |
|---|---|---|
| `volume_full` | Fill `/srv/lab-data` with `.part` files in `tmp/` dated two days ago, like orphaned partial uploads | `clear_stale_temp_files`: delete `.part` files older than one hour in `tmp/` |
| `upload_dir_not_writable` | Make `uploads/` owned by root with mode 755 | `restore_upload_dir_access`: owner 1000:1000, mode 755 |
| `database_unavailable` | Stop the `db` container | `restore_database`: start, unpause or reconnect `db` as needed |

- Inject faults and run repairs through `docker` (for example `docker run --rm -v /srv/lab-data:/d alpine ...`), not sudo.
- A repair touches only its own component. Resetting the whole lab is not a repair.
- Verifier: upload a new random 1 MB file, download it and compare SHA-256 hashes. Recovery counts only when this passes. A running container is not recovery.

## Rules that keep the results honest

- The investigator never learns which fault was injected. Nothing its checks can read may name the fault: file names, container names or labels (including helper containers), environment variables, logs or health output. Ground truth stays in `lab/faults.py` and the eval runner's records.
- The model never chooses actions. Each diagnosis maps to one repair in code.
- Keep investigate.py's checks read-only, allowlisted and shell-free. Keep `validate_report`, which checks every citation against the raw evidence. Add a check only when an eval case needs it.
- Use `muse-spark-1.3` in Standard mode through the Responses API. Never use a `-contributor` model.
- Report results as measured, including misses. Never edit result files by hand.

## Milestones

1. **One complete path for `upload_dir_not_writable`, by Thursday 29 October**
   1. Build the lab app and Postgres. Upload a random file, download it and check that the hashes match.
   2. Write `lab/faults.py` for this fault. Uploads fail after `inject` and work again after `reset`.
   3. Write `repair.py`. After the report, if the diagnosis maps to a repair, ask for approval, apply it, run the verifier and print the result.
   4. The user runs the whole path against the real API.
2. **The other two faults with their repairs, end to end, by Saturday 31 October.**
3. **Evaluation, by Monday 2 November: 15 cases, 3 runs each**
   - 9 fault cases, three variants of each, chosen so that simple rules are not enough. Examples: inode exhaustion with bytes still free; `db` paused; `db` disconnected from the app's network while it still shows as running and healthy.
   - 3 healthy cases with red herrings, such as a `db` restart that has already recovered, or harmless errors in the app log.
   - 3 missing-evidence cases, using `INCIDENTLAB_UNAVAILABLE_CHECKS`.
   - Write down each case's expected outcome before running it. During evaluation, repairs run without the approval prompt; say so in the results.
   - A rule-based baseline that runs the same checks in a fixed order with thresholds, on the same cases.
   - Record for each run: diagnosis against expected outcome, false alarm, citations supported out of total, recovery verified, cost and time. Save results in `eval/results/`.
4. **Demo page and README, by Tuesday 3 November.** A small web page showing the evidence trail as it is collected, the report with supported and unsupported citations, an approve button and the verification result.
