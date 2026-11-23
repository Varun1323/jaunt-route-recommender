# Jaunt Route Recommender

## About this task

**Jaunt Route Recommender** is a prototype built for the Jaunt technical task. It helps a person plan a walking journey between selected landmarks in Midtown Manhattan. The user chooses a starting point and destination, describes what they want from the walk in natural language, and receives a recommended pedestrian route with its estimated distance, walking time, intermediate landmarks, and an explanation of why it was selected.

The application uses a React frontend, a FastAPI backend, OpenRouter for optional AI-assisted preference interpretation, and Valhalla for pedestrian route generation. The backend evaluates available route candidates against the interpreted preferences and constraints. If OpenRouter is not configured or unavailable, the main recommendation flow can fall back to local preference parsing.

The prototype supports eight selectable landmarks:
- Grand Central Terminal
- Chrysler Building
- Stephen A. Schwarzman Building (New York Public Library)
- Bryant Park
- Times Square
- Rockefeller Center
- St. Patrick's Cathedral
- Museum of Modern Art

**Prototype scope:** the application uses research-derived pedestrian approach points and Valhalla's pedestrian-network output to compare route candidates. Distances and walking times are routing-engine estimates for this task prototype.

## Project documentation

The `docs/` directory contains the research and technical documentation behind the task. Please go through these documents to understand the project beyond the setup instructions:

- [`research.md`](docs/research.md) — the eight-landmark research set, points of interest, and research findings.
- [`data-sources.md`](docs/data-sources.md) — geographic and historical data sources, provenance, and attribution.
- [`project-overview.md`](docs/project-overview.md) — task objectives, application architecture, and API flow.
- [`recommendation-engine.md`](docs/recommendation-engine.md) — preference interpretation, constraints, scoring, and route trade-offs.
- [`route-methodology.md`](docs/route-methodology.md) — pedestrian route generation, alternatives, and route validation.
- [`technical-decisions.md`](docs/technical-decisions.md) — technology choices and implementation rationale.
- [`verification-log.md`](docs/verification-log.md) — recorded geographic, route, backend, and frontend verification results.


## 1. Install the required software (Windows)

Open **PowerShell** and install Git, Python 3.12, and Node.js LTS:

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.12 --exact
winget install --id OpenJS.NodeJS.LTS --exact
```

If Windows asks for confirmation, approve the installations. When they finish, close PowerShell and open a new PowerShell window so the updated PATH is loaded.

Check that all three tools are available:

```powershell
git --version
python --version
node --version
npm --version
```

Python should report version 3.12.x. Node.js and npm should both report version numbers.

If `winget` is not available, install the software from the official websites:
- Git: https://git-scm.com/download/win
- Python: https://www.python.org/downloads/
- Node.js: https://nodejs.org/en/download

During Python installation, enable **Add Python to PATH** if the installer offers that option.

## 2. Download the project

In PowerShell, move to the folder where you want the project, then clone the repository:

```powershell
cd $HOME\Downloads
git clone https://github.com/Varun1323/jaunt-route-recommender.git
cd jaunt-route-recommender
git switch final-working
```

If you have already downloaded and extracted the project as a ZIP file, open PowerShell in the extracted project folder instead. You do not need to clone it again.

**Important:** The commands in the next sections that start the backend must be run from the main project folder (the folder containing `backend` and `frontend`).

## 3. Install and configure the backend

From the main project folder, create and activate a Python virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation scripts, use the virtual environment's Python executable directly in the following commands (for example, `.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt`).

Install the pinned backend dependencies:

```powershell
python -m pip install -r backend\requirements.txt
```

The backend dependencies are pinned in `backend/requirements.txt`:
- FastAPI
- HTTPX
- Pydantic
- Uvicorn

Create the backend environment file:

```powershell
Copy-Item backend\.env.example backend\.env
```

Open `backend/.env` in a text editor. To enable AI-powered preference interpretation, add your own OpenRouter API key:

```dotenv
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

Replace the placeholder with your key. The OpenRouter key is optional: without it, the main recommendation endpoint uses its local preference parser. Do not share or commit your API key.

## 4. Start the backend

Make sure the PowerShell terminal is in the **main project folder**. Start the API:

```powershell
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Keep this terminal open while using the application.

The backend runs at:
- API: http://127.0.0.1:8000
- Interactive API documentation: http://127.0.0.1:8000/docs

## 5. Install and start the frontend

Open a **second PowerShell window**. Change to the project's frontend folder. If you followed the clone instructions and used the default Downloads folder, run:

```powershell
cd $HOME\Downloads\jaunt-route-recommender\frontend
```

If your project is in a different location, change the path accordingly.

Install the exact frontend dependencies recorded in the lockfile:

```powershell
npm.cmd ci
```

Start the frontend development server:

```powershell
npm.cmd run dev
```

Using `npm.cmd` avoids the PowerShell execution-policy error that can occur when running the `npm.ps1` shim. In terminals where the npm PowerShell shim is permitted, `npm ci` and `npm run dev` also work.

Keep this terminal open. Open the local URL printed by Vite, normally:

http://localhost:5173

The frontend sends its `/api` and `/health` requests to the backend at `http://127.0.0.1:8000`. Keep both the backend and frontend terminals running.

## 6. Starting the project again later

After the initial installation, you do not need to reinstall dependencies every time.

1. Open PowerShell in the main project folder, activate the virtual environment, and start the backend:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
   ```

2. Open a second PowerShell window, go to the frontend folder, and start Vite:

   ```powershell
   cd $HOME\Downloads\jaunt-route-recommender\frontend
   npm.cmd run dev
   ```

3. Open http://localhost:5173.

## Dependencies and external services

- **Python:** 3.12.x
- **Backend:** pinned packages in `backend/requirements.txt` (FastAPI, HTTPX, Pydantic, Uvicorn)
- **Node.js:** LTS release
- **Frontend:** dependencies pinned by `frontend/package-lock.json`; install with `npm.cmd ci`
- **OpenRouter:** optional API key for AI-assisted preference interpretation
- **Valhalla:** external pedestrian routing service; live route generation requires network access
- **OpenStreetMap tiles:** map display requires network access

The frontend lockfile and pinned backend requirements help reproduce the software environment. External services, model availability, network access, and map tile availability remain outside the repository's control.
