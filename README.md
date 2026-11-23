# Jaunt Route Recommender

## 1. Install the required software (Windows)

Open **PowerShell** and install Git, Python 3.12, and Node.js LTS:

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.12 --exact
winget install --id OpenJS.NodeJS.LTS --exact
```

If Windows asks for confirmation, approve the installations. When they finish, **close PowerShell and open a new PowerShell window** so the updated PATH is loaded.

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
git switch final-tests
```

If you have already downloaded and extracted the project as a ZIP file, open PowerShell in the extracted project folder instead. You do not need to clone it again.

**Important:** The commands in the next sections that start the backend must be run from the main project folder (the folder containing `backend` and `frontend`), not from inside `backend`.

## 3. Install and configure the backend

From the main project folder, install the Python dependencies:

```powershell
python -m pip install -r backend\requirements.txt
```

This project can be run without a Python virtual environment. The packages will be installed into the selected Python installation.

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

Replace the placeholder with your key. The OpenRouter key is optional: without it, the application uses its local preference parser. Do not share or commit your API key.

## 4. Start the backend

Make sure the PowerShell terminal is still in the **main project folder**. Start the API:

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
npm ci
```

Start the frontend development server:

```powershell
npm run dev
```

Keep this terminal open too. Open the local URL printed by Vite, normally:

http://localhost:5173

The frontend sends its `/api` and `/health` requests to the backend at `http://127.0.0.1:8000`. Therefore, keep both the backend and frontend terminals running.

## 6. Starting the project again later

After the initial installation, you do not need to reinstall dependencies every time.

1. Open PowerShell in the main project folder and run:

   ```powershell
   python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
   ```

2. Open a second PowerShell window, go to the frontend folder, and run:

   ```powershell
   npm run dev
   ```

3. Open http://localhost:5173.

## Dependencies

- Python 3.12
- Backend Python packages pinned in `backend/requirements.txt`
- Node.js and npm
- Frontend packages locked in `frontend/package-lock.json` (installed with `npm ci`)
- Optional: an OpenRouter API key for AI-powered preference interpretation

