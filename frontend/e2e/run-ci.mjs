/**
 * CI wrapper: build, start a backend on a throwaway database, run the UI smoke
 * test against it, then shut the backend down.
 *
 * Two things this guards against, both of which produced false passes during
 * development:
 *
 *  1. Stale bundle. The smoke test reads `dist/`, so if the build fails the test
 *     silently runs against yesterday's code and passes. The build is therefore
 *     run here, and a non-zero exit stops the run.
 *  2. A different server. Playwright-free harnesses are easy to point at
 *     whatever is already listening. This one starts its own backend on its own
 *     port with a throwaway DB, so a leftover dev process cannot mask a failure.
 */
import { spawn } from 'child_process'
import { existsSync, mkdtempSync } from 'fs'
import { tmpdir } from 'os'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND = join(HERE, '..')
const BACKEND = join(FRONTEND, '..', 'backend')
const PORT = process.env.E2E_PORT || '8123'
const BASE = `http://127.0.0.1:${PORT}`

const scratch = mkdtempSync(join(tmpdir(), 'leaseguard-e2e-'))
const env = {
  ...process.env,
  PYTHONPATH: '.',
  DB_PATH: join(scratch, 'e2e.db'),
  UPLOAD_DIR: join(scratch, 'uploads'),
  USE_FIXTURES: 'true', // deterministic extraction, no network
  // Never let a CI run talk to Groq even if a key leaked into the environment.
  GROQ_API_KEY: '',
}

function waitForServer(url, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tick = async () => {
      if (Date.now() > deadline) return reject(new Error(`server did not start: ${url}`))
      try {
        const res = await fetch(`${url}/api/health`)
        if (res.ok) return resolve()
      } catch {
        /* not up yet */
      }
      setTimeout(tick, 250)
    }
    tick()
  })
}

function runStep(cmd, args, opts) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { stdio: 'inherit', shell: process.platform === 'win32', ...opts })
    child.on('exit', (code) => resolve(code ?? 1))
  })
}

// 1. Build. A failed type-check must fail the E2E job, not be skipped.
console.log('building the frontend bundle...')
const buildCode = await runStep('npm', ['run', 'build'], { cwd: FRONTEND })
if (buildCode !== 0) {
  console.error('build failed; refusing to run the smoke test against a stale bundle')
  process.exit(1)
}

const server = spawn(
  'python3',
  ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', PORT, '--log-level', 'warning'],
  { cwd: BACKEND, env, stdio: 'inherit' },
)

let exitCode = 1
try {
  await waitForServer(BASE)
  console.log(`backend up on ${BASE}`)
  exitCode = await runStep('node', [join(HERE, 'smoke.mjs'), BASE], { cwd: FRONTEND, env })
} catch (err) {
  console.error('E2E harness failed:', err.message)
  exitCode = 1
} finally {
  server.kill('SIGTERM')
  // Give uvicorn a moment to close its socket before the process exits.
  await new Promise((r) => setTimeout(r, 500))
}

process.exit(exitCode)
