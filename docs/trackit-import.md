# Moving from Track-It!

Layer 8 can take over your Track-It! (Ivanti/BMC) history in two steps:

1. **Export, on the Track-It! server:** `tools/trackit-export/trackit-export.ps1` reads the database read-only and writes one .zip bundle.
2. **Import, on the Layer 8 appliance:** `python -m app.cli import-trackit bundle.zip` does a dry run. Add `--apply` to import.

The bundle holds your real data. Copy it across on site, by USB or a share, not by email. Delete it once the import is done. It's never part of the Layer 8 ISO or the code.

## 1. Export

On the Track-It! server, or any Windows machine that can reach its SQL Server, in PowerShell:

```powershell
# Everything (takes a few minutes; attachments are inside the database)
.\trackit-export.ps1 -Server 'TRACKITSRV\TRACKIT' -Out D:\trackit.zip

# A trial with the last 10 days
.\trackit-export.ps1 -Server 'TRACKITSRV\TRACKIT' -Out D:\trial.zip -Since (Get-Date).AddDays(-10)
```

- **Connection:** it signs in as you (Windows authentication); you need read access to the Track-It! database. Use `-Credential (Get-Credential)` for a SQL login, and `-TrustServerCertificate` if SQL Server uses its own certificate.
- **Nothing to install:** it uses the SQL Server client that comes with Windows.
- **What's left out:** password, salt, SID and token columns are never read. Track-It!'s automatic system notes are left out unless you add `-SystemNotes`; there are usually hundreds of thousands, and Layer 8 keeps them only as one internal summary per ticket.
- **`-Since`:** exports only tickets opened or changed since that date, with their notes, work orders and attachments. The reference tables come in full every time: requesters, technicians, categories, solutions, assets.

## 2. Import

Copy the .zip to the appliance, then (as the helpdesk user, in /opt/helpdesk/backend):

```sh
.venv/bin/python -m app.cli import-trackit /path/trackit.zip            # dry run: what would happen
.venv/bin/python -m app.cli import-trackit /path/trackit.zip --apply    # import
```

Or go through it step by step, which is best for the real thing:

```sh
.venv/bin/python -m app.cli import-trackit /path/trackit.zip --interactive
```

It lists the fields it found on your tickets: how full each is, how many different values, and the commonest ones. For each field you choose:

- **Track-It! fields Layer 8 has no field for** (Custom Look Up 1–8, Custom Text, Custom Date, Location, Ticket Source): keep under Track-It!'s name, keep under a new name ("Building"), or skip.
- **Departments:** `match` (default) uses only the department choices you already have under Settings > Ticket fields, by name and ignoring case; others are kept as an imported detail. `add` adds the missing ones as choices. `skip` leaves them out.
- **Opened group:** which queue each Track-It! group's tickets go to.

It then shows the dry run and asks before importing.

**Kept fields don't touch your setup.** They're stored on the imported tickets only and shown there as "Imported details". They never become form fields, list columns or dropdown choices. Placeholder values ("N/A", "-", "none", "0") are dropped, and extra spaces are tidied. Track-It! data is rarely clean.

Without `--interactive`, the same choices are options: `--label HD_LOOKUP01=Building`, `--skip HD_LOOKUP04`, `--departments add`, `--group "NETWORK=Network team"` (all but `--departments` can be repeated). `--queue "Name"` sets the queue for everything else (default: the default queue). The dry run prints:
- people matched and new
- tickets, notes, attachments, solutions and devices
- how Track-It!'s priorities and statuses were mapped
- any problems, such as a ticket whose requester is missing

### What comes across

| Track-It! | Layer 8 |
|---|---|
| Requesters | Matched to existing users (from AD) by email or Windows login. Others are added as requesters with no password; inactive ones stay inactive. Their Track-It! location becomes their office. |
| Technicians | Matched the same way. Others are added as **inactive** technician accounts, so their names stay on old notes. |
| Tickets | Ticket title, description, status, priority, category path, requester, assignee (sent to, else responded by, else closed by), phone, department, due date, parent ticket, linked asset, queue (from the opened group), source (email, portal or technician, from Ticket Source), and opened, responded and closed dates. The description ends with "(Track-It! ticket 12345)", so searching for the old number finds it. Other fields become imported details, as chosen above. |
| Notes | Technician notes (private ones as internal notes) and resolutions, with their authors, dates and time spent, as plain text (Track-It! stores them as HTML). System notes become one internal summary, if exported. |
| Work orders | Child tickets of their ticket, with their notes. |
| Attachments | Files on their tickets. |
| Solutions | Knowledge base articles in their topic: private ones for staff only, inactive ones archived. |
| Assets | Merged into Devices by name or serial (empty fields filled: serial, maker, purchase date, warranty end), others added. |

- **Gaps in Track-It!'s data:** older tickets, especially ones Track-It! itself migrated, often have no customer, and notes often have no technician.
  - A ticket without a customer takes the customer whose email matches the ticket's, or whose phone number matches, but only if no other customer shares that number.
  - A note without a technician is credited to the technician whose login last saved it.
  - Whatever is still unknown shows as "Track-It! import". That includes notes last saved by Track-It!'s own system account.
- **Passwords:** nobody's Track-It! password comes across. People sign in with AD, or set a new local password.
- **No side effects:** nothing is emailed, and routing rules, SLA timers and surveys don't run on imported tickets.
- **Statuses:** "C" becomes closed. Open tickets become open, or "waiting on user" when their Track-It! status mentions waiting, pending or hold.
- **Priorities:** mapped by their names (critical/urgent, high, low, otherwise medium). The dry run shows the mapping.
- **Not imported:** purchase orders, changes, surveys and Track-It! reports. Layer 8 has its own; ask if you need the old ones.

### Running it again (cut-over)

Every imported record is remembered (table `import_refs`), so you can import a trial first and the full history later. A last top-up on cut-over day, with `-Since` set to the day of the full export, updates the tickets that changed and adds new notes and attachments without duplicating anything.
