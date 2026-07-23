# RPATech Basic CRM

Mayank's foundation half of the capstone. Uday's pipeline, stage-history, activity log, and dashboard-summary work is intentionally left as the integration handoff.

## Decisions

- Multi-user: Sales Reps see records they own; Manager/Admin sees all records. Visibility is enforced in SQL query helpers.
- Fixed stages: New → Contacted → Qualified → Proposal → Won/Lost.
- A contact belongs to one company.
- Activities are structured as call, email, note, or meeting and can attach to a deal/contact.
- Deal value is numeric and ready for currency display in the pipeline half.
- Contacts are created and edited from one shared modal and must always link to an accessible company.

## Theme handoff

Use the [LogicBrats palette maker](https://logicbrats.com/color-palette-maker/) to review the shared palette before implementation. The current 60:30:10 palette is:

- 60% dominant/background: `#F7F5F0`
- 30% secondary/surface: `#243447`
- 10% accent/actions: `#E07A5F`

Bootstrap Icons are the only icon set used.

## Run locally on Windows

Use the included launcher. It creates a project-local `.venv`, installs Flask into that exact environment, and starts the app with the same Python interpreter:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Then open http://127.0.0.1:5000. Keep the PowerShell window open while using the app. Stop the server with `Ctrl+C`.

If you prefer manual commands, use `python -m pip` rather than `pip`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If PowerShell blocks activation, use the virtual-environment interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Demo accounts: `mayank@example.com` / `demo123` (manager), `uday@example.com` / `demo123` (rep).

## Handoff boundary

Uday should build deals and activities using the existing `users`, `companies`, `contacts`, `pipeline_stages`, `deals`, `deal_stage_history`, and `activities` tables. Stage history must be written from both board transitions and direct deal edits. Keep the shared Add/Edit modal pattern and the palette above.
