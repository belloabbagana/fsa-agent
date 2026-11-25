# Dashboard: two modes

Status: v0.1 built 25 November 2026 · Streamlit · `app/streamlit_app.py`

## Run it
```bash
./fsa app                 # analyst mode: full access, local, uses reports/ and data/
./fsa app --mode viewer   # viewer mode: read-only, reads app/snapshot/ only
./fsa export-viewer       # rebuild the viewer snapshot from the current reports
```

## The two modes
| | Analyst (Bello, local) | Viewer (anyone sent the link or CV) |
|---|---|---|
| Companies | Core companies, plus peers on the Peers screen | The two core companies only |
| Screens | Company · Findings · Peers · Sources and data quality · Ask the agent | Company · Findings · Peers (explains why it is not shown) · Sources and data quality |
| Findings | Full review notes, and the **answer form** that records an answer (append-only, version log) | Findings, evidence and the review verdict; no answer form |
| Ask the agent | Haiku, answers only from the open report, every number checked by code, fixed reply to advice questions | Not offered (decided 25 Nov 2026: the viewer cannot use Bello's Pro sign-in) |
| Data | Reports on this Mac | `app/snapshot/*.json` |

## What the viewer snapshot leaves out (checked by code, fails closed)
- Section K findings and any finding that uses a peer or comparison-basis figure.
- Every data note that names a peer or the NGN Market data.
- The data-quality inputs (they mention peers).
- If any peer ticker, peer name or "NGN Market" is still in a snapshot file, **nothing is written**.

## Publishing the snapshot
`app/snapshot/` is git-ignored. Publishing it (and hosting a public viewer) is a deliberate step after the legal check and employer-policy check in `docs/plan.md`. Source PDFs are linked, never copied.

## Later
Answer form re-run (an answer rewrites the finding as a new version), more NGX companies, a viewer-side Ask once there is a funded API key.
