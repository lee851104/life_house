const {defineConfig} = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 45000,
  expect: {timeout: 15000},
  workers: 1,
  use: {baseURL: 'http://127.0.0.1:8010', headless: true,
    trace: 'retain-on-failure', screenshot: 'only-on-failure'},
  webServer: {
    command: process.env.E2E_SERVER_COMMAND || 'uv run python -m uvicorn src.serving.api:app --host 127.0.0.1 --port 8010',
    url: 'http://127.0.0.1:8010/api/v3/meta', timeout: 180000,
    reuseExistingServer: !process.env.CI
  }
});
