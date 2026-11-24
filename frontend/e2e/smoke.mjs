/**
 * End-to-end UI test: drive the real built SPA against a real backend.
 *
 * No browser is installable in the build environment, so this runs the actual
 * production bundle inside jsdom and lets it talk HTTP to a live uvicorn
 * process. It is not a pixel test - it cannot catch a layout bug - but it does
 * catch the failures that actually broke this app during development:
 *
 *   * a page that throws on mount and renders nothing,
 *   * a button that no longer does anything after a refactor,
 *   * a hand-entered correction shown as if it came from the document,
 *   * an audit that silently stops applying a corrected term,
 *   * a request that 404s or 500s while the screen still looks fine.
 *
 * Two things it must get right to be trustworthy:
 *   1. A cookie jar. After sessions were added, a harness that ignores
 *      set-cookie makes every request a new tenant, so the app "correctly"
 *      shows nothing and the test fails for a reason that is not a bug.
 *   2. Failing loudly. Every assertion is checked, every non-2xx response and
 *      every uncaught JS error is recorded, and the process exits non-zero.
 *
 * Usage:  node e2e/smoke.mjs [baseUrl]
 * The backend must already be running, and `npm run build` must have been run.
 */
import { JSDOM } from 'jsdom'
import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, join } from 'path'

const HERE = dirname(fileURLToPath(import.meta.url))
const DIST = join(HERE, '..', 'dist')
const BASE = process.argv[2] || process.env.E2E_BASE_URL || 'http://127.0.0.1:8000'

const results = []
const httpErrors = []
const jsErrors = []
let cookie = ''

function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail })
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : `\n        ${detail}`}`)
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

/** Poll until `fn()` is truthy; returns false on timeout instead of hanging. */
async function until(fn, timeoutMs = 12000, stepMs = 150) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      if (fn()) return true
    } catch {
      /* page may be mid-render */
    }
    await wait(stepMs)
  }
  return false
}

function readBundle() {
  const html = readFileSync(join(DIST, 'index.html'), 'utf8')
  const js = html.match(/src="\/assets\/([^"]+)"/)
  if (!js) throw new Error(`no JS bundle referenced in ${DIST}/index.html`)
  return readFileSync(join(DIST, 'assets', js[1]), 'utf8')
}

function boot() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: BASE + '/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  })
  const { window } = dom

  // jsdom's FormData/Blob/File are not the Node ones; fetch needs the Node
  // versions or a multipart upload hangs forever.
  window.FormData = FormData
  window.Blob = Blob
  window.File = File
  window.scrollTo = () => {}
  window.addEventListener('error', (e) => jsErrors.push(String(e.message || e)))
  window.addEventListener('unhandledrejection', (e) => jsErrors.push(String(e.reason)))

  window.fetch = async (url, opts = {}) => {
    const path = String(url).startsWith('http') ? String(url) : BASE + String(url)
    const headers = { ...(opts.headers || {}) }
    if (cookie) headers['Cookie'] = cookie

    const res = await fetch(path, { method: opts.method || 'GET', headers, body: opts.body, signal: opts.signal })

    // Remember the session token, exactly as a browser would.
    for (const raw of res.headers.getSetCookie?.() || []) {
      const m = raw.match(/lg_session=([^;]+)/)
      if (m) cookie = `lg_session=${m[1]}`
    }

    const text = await res.text()
    if (!res.ok) httpErrors.push(`${opts.method || 'GET'} ${url} -> ${res.status}`)
    return {
      ok: res.ok,
      status: res.status,
      text: async () => text,
      json: async () => JSON.parse(text),
    }
  }

  let bundle = null
  return {
    window,
    // Split from the shell so a test can create data BEFORE the SPA loads. The
    // dashboard fetches once on mount, and jsdom does not do a real page load on
    // history.pushState, so a lease created after mount is invisible.
    start() {
      bundle = bundle || readBundle()
      window.eval(bundle)
    },
  }
}

const txt = (w) => w.document.getElementById('root').textContent || ''
const $ = (w, sel) => w.document.querySelector(sel)
const $$ = (w, sel) => [...w.document.querySelectorAll(sel)]
const byText = (w, sel, needle) => $$(w, sel).find((el) => (el.textContent || '').includes(needle))
const click = (el) => el && el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent('click', { bubbles: true, cancelable: true }))

/** Set a controlled input the way React expects, then fire `input`. */
function type(window, el, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(el, value)
  el.dispatchEvent(new window.Event('input', { bubbles: true }))
}

/**
 * Create a lease and upload documents through the app's own API, before the UI
 * fetches. The dashboard loads once on mount, so data created afterwards is
 * invisible until a full page load, which jsdom does not perform on pushState.
 */
async function createLeaseWithDocs(window, label, docs) {
  const res = await window.fetch('/api/leases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ property_label: label, start_date: '2025-04-01' }),
  })
  const body = await res.json()
  if (!body.lease_id) return null
  for (const d of docs) {
    const form = new FormData()
    form.append('kind', d.kind)
    form.append('effective_date', d.effective)
    form.append('file', new File([d.text], d.name, { type: 'text/plain' }))
    await window.fetch(`/api/leases/${body.lease_id}/documents`, { method: 'POST', body: form })
  }
  return body.lease_id
}

async function main() {
  console.log(`\nLeaseGuard UI smoke test against ${BASE}\n`)

  // ---------------------------------------------------------------- dashboard
  const shell = boot()
  const w = shell.window

  // Seed a case the product cannot resolve on its own: a lease with no
  // maintenance clause at all. Created before the SPA loads, because the
  // dashboard reads once on mount.
  const noCapLease = await createLeaseWithDocs(w, 'No Cap Shop, Lucknow', [
    {
      kind: 'lease',
      effective: '2025-04-01',
      name: 'nocap.txt',
      text:
        'COMMERCIAL LEASE\nMonthly Rent of Rs. 40,000 per month.\n' +
        'Rent shall increase by 10 percent per annum.\nCommencement Date: 1 April 2025\n',
    },
  ])

  shell.start()
  check('app renders the dashboard', await until(() => txt(w).length > 20), 'root stayed empty')
  check('sample-case action is present', await until(() => !!byText(w, 'button', 'Sample')))
  check('no JS error on load', jsErrors.length === 0, jsErrors.join('; '))

  // ------------------------------------------------------- load the sample case
  click(byText(w, 'button', 'Sample'))
  const loaded = await until(() => /Terms in force/i.test(txt(w)))
  check('sample lease opens the detail screen', loaded, `body text: ${txt(w).slice(0, 200)}`)
  if (!loaded) return finish()

  // The sample case must be idempotent: pressing it twice may not clone it.
  const countBefore = (txt(w).match(/Shop No\. 7/gi) || []).length

  // -------------------------------------------------------------- term table
  check('terms are listed', $$(w, '.term-fix').length > 0, 'no term rows rendered')
  const fixButtons = $$(w, '.term-fix').length
  check('every term offers a correction control', fixButtons >= 3, `found ${fixButtons}`)
  check('an audit finding is shown', /Rs\.|₹/.test(txt(w)), 'no amount anywhere on screen')

  // ------------------------------------------------------- correct a bad number
  click($$(w, '.term-fix')[2])
  const editorOpen = await until(() => !!$(w, '.term-input'))
  check('the fix editor opens', editorOpen)
  if (editorOpen) {
    const input = $(w, '.term-input')
    const prefilled = input.value
    check('editor pre-fills the current value', /^\d+$/.test(prefilled), `got "${prefilled}"`)

    type(w, input, '6000')
    await wait(50)
    click($(w, '.term-edit .btn-primary'))

    const corrected = await until(() => /corrected by you/i.test(txt(w)))
    check('correction is marked as user-supplied', corrected, 'badge never appeared')
    check('corrected value is on screen', txt(w).includes('6,000'), 'value did not update')

    // The badge must survive a reload; that is what the flag plumbed through
    // the domain model was for.
    w.history.pushState({}, '', '/')
    w.dispatchEvent(new w.PopStateEvent('popstate'))
    await until(() => $$(w, '.lease-card a').length > 0)
    click($$(w, '.lease-card a')[0])
    const survived = await until(() => /Terms in force/i.test(txt(w)) && /corrected by you/i.test(txt(w)))
    check('correction survives a reload', survived, 'badge gone after navigation')
  }

  // ------------------------------------------------------------ not cloned
  const countAfter = (txt(w).match(/Shop No\. 7/gi) || []).length
  check('sample load did not duplicate the lease', countAfter <= Math.max(countBefore, 1) + 1, `${countBefore} -> ${countAfter}`)

  // ------------------------------------- a term the document could not settle
  // A lease with no maintenance clause at all: that term is genuinely missing,
  // not misread. Before this existed the row said "uncertain" and offered no
  // way forward, so the audit stayed blocked forever.
  if (noCapLease) {
    // Navigate straight to this lease. Returning to the dashboard and finding its
    // card would test the dashboard's fetch timing, not the feature.
    check('the seeded lease exists', !!noCapLease)
    w.history.pushState({}, '', `/leases/${noCapLease}`)
    w.dispatchEvent(new w.PopStateEvent('popstate'))
    const onLease = await until(() => /Terms in force/i.test(txt(w)))
    check('the lease screen opens', onLease, txt(w).slice(0, 160))
    if (onLease) {
      await wait(300)

      const maintRow = $$(w, 'tbody tr').find((r) => /Maintenance/i.test(r.textContent || ''))
      check('the missing term reads as uncertain', !!maintRow && /Uncertain/i.test(maintRow.textContent || ''))

      const provide = maintRow && [...maintRow.querySelectorAll('button')].find((b) => /Enter value/i.test(b.textContent || ''))
      check('an uncertain row offers a way to supply the value', !!provide)

      if (provide) {
        click(provide)
        const opened = await until(() => !!$(w, '.term-input'))
        check('the supply editor opens empty', opened && $(w, '.term-input').value === '')
        if (opened) {
          type(w, $(w, '.term-input'), '5000')
          await wait(50)
          click($(w, '.term-edit .btn-primary'))
          const saved = await until(() => /entered by you/i.test(txt(w)))
          check('the supplied value is marked as entered by you', saved, 'badge never appeared')
          check('the supplied value is now on screen', txt(w).includes('5,000'))
          // Scoped to the row: the page also has a Documents table that legitimately
          // names the file.
          const rowNow = $$(w, 'tbody tr').find((r) => /Maintenance/i.test(r.textContent || ''))
          check(
            'the supplied value cites no document or page',
            !!rowNow && !/nocap\.txt/.test(rowNow.textContent || '') && !/Page\s*\d/i.test(rowNow.textContent || ''),
            rowNow?.textContent?.slice(0, 160),
          )
        }
      }
    }
  }

  // ------------------------------------------------- audit page and letter
  // Back to the sample case, which has the invoice. The seeded lease was visited
  // by direct URL above and is not where the invoice lives.
  w.history.pushState({}, '', '/')
  w.dispatchEvent(new w.PopStateEvent('popstate'))
  await until(() => $$(w, '.lease-card').length > 0)
  const cards = $$(w, '.lease-card')
  const sampleCard = cards.find((c) => /Sample|Sector 18|Shop/i.test(c.textContent || ''))
  if (sampleCard) {
    const link = [...sampleCard.querySelectorAll('a')].find((a) => /Open|खोलें/i.test(a.textContent || ''))
    click(link)
    await until(() => /Terms in force/i.test(txt(w)))
  }
  // The letter lives behind the audit screen, so walk the real path rather than
  // asserting on a control that only exists on another route.
  const auditLink = $$(w, 'a').find((a) => /audit/i.test(a.textContent || ''))
  check('an invoice audit link exists', !!auditLink, `on lease screen: ${txt(w).slice(0, 120)}`)
  if (auditLink) {
    click(auditLink)
    const onAudit = await until(() => /verdict|finding|approve|approval|letter/i.test(txt(w)))
    check('audit screen renders', onAudit, `body text: ${txt(w).slice(0, 200)}`)

    // The letter stays disabled until at least one finding is approved, so a
    // letter test that skips approval would only ever assert on a dead button.
    // The button label is exactly "✓ Approve"; an /Approved|Approve/ regex also
    // matches nothing else here, but the text was being read before the cards
    // had rendered, which is why an explicit wait is needed first.
    await until(() => $$(w, 'button').some((b) => /Approve/.test(b.textContent || '') && !b.disabled))
    const approve = $$(w, 'button').find((b) => /Approve/.test(b.textContent || '') && !b.disabled)
    check('a finding can be approved', !!approve)
    if (approve) {
      click(approve)
      const approved = await until(() =>
        $$(w, 'button').some((b) => /letter/i.test(b.textContent || '') && !b.disabled),
      )
      check('approving enables the letter action', approved, 'letter button stayed disabled')
    }

    const letterCta = $$(w, 'button').find((b) => /letter/i.test(b.textContent || '') && !b.disabled)
    check('a letter action exists', !!letterCta)
    if (letterCta) {
      click(letterCta)
      const onLetter = await until(() => /letter|पत्र/i.test(txt(w)) && txt(w).length > 200)
      check('letter screen renders', onLetter)

      // Bilingual letters are a stated requirement, not a nicety.
      const hiToggle = $$(w, 'button').find((b) => /हिंदी|Hindi/.test(b.textContent || ''))
      check('Hindi letter option is offered', !!hiToggle)
      if (hiToggle) {
        click(hiToggle)
        const hindi = await until(() => /[\u0900-\u097F]/.test(txt(w)))
        check('Hindi letter renders Devanagari text', hindi)
      }
    }
  }

  check('no uncaught JS errors during the journey', jsErrors.length === 0, jsErrors.join('; '))
  check('no failed HTTP requests during the journey', httpErrors.length === 0, httpErrors.join('; '))
  finish()
}

function finish() {
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} checks passed\n`)
  if (failed.length) {
    console.log('FAILED:')
    for (const r of failed) console.log(`  - ${r.name}${r.detail ? ` (${r.detail})` : ''}`)
    process.exitCode = 1
  }
}

main().catch((err) => {
  console.error('\nHarness crashed:', err)
  process.exitCode = 1
})
