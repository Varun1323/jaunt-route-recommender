import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(frontendDir, '..');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level warning',
      cwd: repositoryRoot,
      env: {
        OPENROUTER_API_KEY: '',
        JAUNT_DISABLE_PREFERENCE_ROUTE_GENERATION: '1',
      },
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1',
      cwd: frontendDir,
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
