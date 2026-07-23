# RPATech CRM

A Flask CRM for managing companies, contacts, deals, sales activity, and the complete deal pipeline.

## Features

- Secure login and logout with two roles: managers see all records; sales reps see only the records they own.
- Company and contact create/edit screens with search, validation, empty states, and shared Add/Edit modals.
- Deal create/edit workflow with title, company, contact, value, owner, and pipeline stage.
- Pipeline board for the fixed stages: New, Contacted, Qualified, Proposal, Won, and Lost.
- Stage-change audit trail recording the prior stage, new stage, actor, and timestamp for both board moves and direct deal edits.
- Deal detail screen with account information, current stage, related activities, and stage-history timeline.
- Contact and deal activities: calls, emails, notes, and meetings, displayed newest first.
- Dashboard metrics for visible companies, contacts, deal counts per stage, and total pipeline value.

## Technology

- Python and Flask
- SQLite database (`crm.sqlite3`), initialized and seeded automatically at first run
- Bootstrap 5 and Bootstrap Icons

## Run locally on Windows

Use the included launcher:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000). Stop the server with `Ctrl+C`.

To run manually:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If activation is blocked, use the virtual-environment Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Manager | `mayank@example.com` | `demo123` |
| Sales rep | `uday@example.com` | `demo123` |

## Data and access rules

The initial database includes sample companies, contacts, and a deal in every pipeline stage. The stage list is fixed. A contact belongs to one company, while an activity may be attached to a deal, a contact, or both.

Access control is enforced by the backend: a sales rep cannot access another owner's records through a direct URL, while a manager can view all records.
