# Workflow Learner

A local application that learns demonstrated routines and replays them with your guidance. Use it to connect browser tasks such as invoices, spreadsheets, email drafts, and accounting screens into a visible, editable sequence.

**This is a working v0.1, not a universal autonomous employee.** It learns ordered actions and repeated subsequences; it does not train an AI model or infer business rules. Browser controls and supported Linux accessibility controls can be replayed. Apps without accessible controls need manual steps or a dedicated adapter.

![Dashboard after the automated practice test](docs/dashboard.png)

## Start

Requires Python 3.11+ and Brave, Chrome, or another Chromium browser. Linux desktop integration additionally requires system Python with PyGObject and AT-SPI.

```bash
cd "/home/abdul-aleem-arshad-mohammed/Work/Workflow Learner"
./start.sh
```

For another checkout, run `./start.sh` from its directory. The first launch creates a virtual environment and installs dependencies. The dashboard opens at **http://127.0.0.1:8765**. Keep its terminal open while using the app; Ctrl+C stops the service. No recording starts automatically.

To add an application-menu launcher:

```bash
./scripts/install-desktop.sh
```

## Connect your everyday Brave browser

1. Open `brave://extensions` (Chrome: `chrome://extensions`).
2. Enable **Developer mode**, click **Load unpacked**, and select the repository's `extension` folder.
3. In the dashboard, open **Setup & privacy** and click **Copy pairing key**.
4. Open the Workflow Learner extension, paste the key, and click **Connect to local app**.
5. Reload the websites you want to record. Sign in before recording.

The extension operates in your existing browser profile, so you keep your normal accounts and sessions. It requests access to HTTP/HTTPS sites to support your different websites. The service-worker/content-script design follows [Chrome's isolated content-script model](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts).

Loading an unpacked extension and approving its browser permissions is a one-time browser setup step. The repository being installed does **not** mean the extension is already installed in your personal Brave profile.

## Teach → review → replay

1. Click **Teach a workflow**, give the routine a name, and optionally restrict it to exact origins such as `https://mail.google.com` and `https://docs.google.com`.
2. Optionally enable desktop observation. Recording continues until paused/stopped, with a 1,000-step limit per demonstration.
3. Perform one complete task in your normal browser. A red **REC** badge indicates active browser observation.
4. Click **Stop & save**. Open **Edit steps** and inspect the sequence. Remove incidental clicks or add manual instructions.
5. All captured text and select values become named `{{parameters}}`. Enter fresh values for each run. You can replace a parameter with an explicit fixed value in the editor.
6. Keep **Ask first** enabled for steps that send messages, post financial records, delete items, or otherwise need your review. All recorded clicks, checks, and shortcuts default to asking first; the app does not guess business impact from a button label.
7. Choose **Guided** to approve every step, or mark the revision reviewed and choose **Automatic**. Automatic replay continues between Ask-first/manual steps. It starts when you click **Start replay**; this version has no unattended event trigger or calendar scheduler.
8. Use **Teach again** to capture a corrected demonstration. This creates a new revision, retains the old revision locally, and invalidates the prior review. Recurring sequences of 3–5 steps found in at least two saved demonstrations appear under **Learned patterns**.

**Continuous learning means accumulating demonstrations while recording is on.** It does not mean hidden always-on screen capture. Pattern counts are evidence of repetition, not a safety score. A completed run means adapters acknowledged the recorded actions; verify the business result in the target application.

## Practice without touching your accounts

Click **Try the practice workspace** on the dashboard. While recording:

1. Enter an invoice number, customer, and amount; click **Add invoice to sheet**.
2. Enter a recipient and message; click **Save local draft**.
3. Tick the accounting checkbox and click **Finish practice workflow**.
4. Stop/save, review the steps, and replay with different values.

The practice workspace uses memory only. It never sends email, moves money, or updates external accounts. The screenshot shows test data from this exact flow.

## Desktop apps

The optional adapter uses the [GNOME AT-SPI accessibility interface](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/index.html), including on compatible Wayland applications. It observes accessible focus changes and text-edit locations, without reading the typed text.

- Where an accessible application maps uniquely to an installed desktop launcher, a reviewed `native_launch` step can open it.
- `native_focus` focuses a uniquely matching role/name within the recorded application.
- `native_fill` fills a matching editable control using a parameter.
- To activate an accessible button, change its observed focus step to `native_action`; only controls with exactly one accessible action can run.
- Native button activation is **not** universally observable, so focus events are not automatically promoted to clicks.
- If a control is absent or ambiguous, replay stops. Duplicate role/label pairs need a dedicated adapter or a manual step.

On Debian/Ubuntu, install `python3-gi` and `gir1.2-atspi-2.0` if the adapter reports unavailable. Other Linux distributions may use different package names. macOS/Windows native adapters are not implemented; the browser companion remains the primary capture route.

## Current coverage and limits

| Capability | v0.1 |
|---|---|
| Existing Brave/Chrome profile | Browser extension; one paired browser profile at a time |
| Multi-tab navigation | Captured and recreated in new tabs |
| Standard DOM clicks, inputs, textareas, selects, checkboxes | Record and replay |
| Contenteditable fields such as many email editors | Basic text replay; rich formatting/attachments require review |
| Embedded frames | Exact frame URL matching; ambiguous/missing frames stop replay |
| Repeated demonstrations / workflow revisions | Stored locally and mined for ordered subsequences |
| Guided and automatic replay | Implemented with per-step review gates |
| Linux desktop app focus/text/actions | AT-SPI adapter; application dependent |
| Google Sheets canvas, Excel grids, drag-and-drop, file dialogs, downloads, shadow DOM | Manual steps or custom adapters needed |
| Keyboard shortcuts / Enter submissions | Observed; replace with a button click or manual step for replay |
| Arbitrary app understanding, invoice OCR, local LLM inference, autonomous triggering | Not implemented |
| Gmail/Zoho/Tally business correctness | Must be demonstrated and verified with your own app; no dedicated integration claimed |

Website selectors and labels can change. Replay verifies a unique visible control and its recorded label; it stops instead of choosing a nearby or similar target. Some sites reject synthetic DOM events. Use a manual step when an app requires trusted input or a specialized integration.

## Privacy and storage

- The HTTP server binds only to `127.0.0.1:8765`.
- Workflows, demonstrations, revision history, and execution outcomes live in `~/.local/share/workflow-learner`. Override with `WORKFLOW_LEARNER_DATA` if desired.
- The data directory uses permissions `0700`, and the database/key file uses `0600`. Data is **not encrypted at rest**; use your OS disk encryption if needed.
- No cloud AI, telemetry, screenshots, clipboard capture, or global keystroke logger.
- Password/OTP/payment-card fields identified by type/attributes/labels are excluded. Login/OAuth pages are excluded. URL query keys matching common secret names and token-bearing fragments are removed.
- **URLs, control labels, and explicit fixed values can still contain personal data.** Automatic redaction cannot recognize every custom secret field. Pause recording for sensitive work; use an origin allowlist. Website authors can also mark private areas `data-wl-private`.
- Recorded input values become placeholders before storage; replay parameter values live in memory and are not written to run logs. Values you explicitly save into workflow steps are persisted.
- Browser login sessions remain in your browser's existing profile. The extension stores its scoped pairing token in extension storage.
- The adapter key may submit events and acknowledge commands; it cannot approve a run, edit/delete workflows, or read the full library. The dashboard has a separate admin token.
- Host validation and Fetch Metadata checks restrict access from other websites. This is a single-user local tool, not a security boundary against malicious processes or other users with access to your OS account.
- Delete a workflow to remove its associated demonstrations, revisions, and run history from the live database. This is ordinary deletion, not guaranteed forensic erasure of disk/WAL/backups.
- Runtime data, credentials, virtual environments, and logs are excluded from Git.

## Failure behavior

Only one recording or replay is active at a time. Each command is dispatched at most once. A missing acknowledgement fails the run after 45 seconds; it is not automatically retried because the application might already have performed the action. Stop/pause cannot undo an action already dispatched. Inspect the target app before starting a new run after an error.

Restarting the service marks unfinished work interrupted and never resumes side effects automatically. Nothing is sent to your contacts or posted to accounting software during installation or tests.

## Development and verification

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check workflow_learner tests scripts
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/browser_smoke.py
# Optional Linux desktop test: opens a temporary, isolated GTK window.
.venv/bin/python scripts/native_smoke.py
```

The browser smoke test uses a temporary profile and database. It pairs the real MV3 extension, records two invoice demonstrations, discovers repeated patterns, replays with fresh parameters, verifies the resulting sheet/draft/accounting state, checks the step editor, and checks mobile overflow. It requires port 8765 to be free. Extension testing uses the [Playwright Chromium extension workflow](https://playwright.dev/python/docs/chrome-extensions).

`requirements-lock.txt` records the runtime environment used for this release; `requirements-dev-lock.txt` records test tools. CI runs the backend tests, lint/format checks, JavaScript syntax checks, and browser smoke test. The GTK smoke test requires an accessibility-enabled desktop and is run separately.

Design targets for this personal tool are local API p95 under 150 ms for a small library, dashboard LCP under 1.5 s / INP under 200 ms / CLS under 0.1 on a local connection, and 99% availability while the user-run process and computer are awake. These are targets, not measured production SLOs or guarantees.

## Project layout

```text
workflow_learner/     Local API, SQLite storage, sequence learner, replay engine
  native.py          Optional Linux accessibility adapter
  static/            Dashboard and isolated practice workspace
extension/           Brave/Chrome MV3 companion
scripts/             Launch setup and real browser/desktop smoke tests
tests/               State-machine, privacy, authentication, and API tests
```
