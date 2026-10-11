# Setting up OPD Saathi at a hospital

For the engineer installing OPD Saathi on site. The demo (`start-demo.ps1`) is separate and must not be used for a real hospital.

## How hospitals are kept apart

Each hospital gets its **own installation**: a server (or PC) inside the hospital running its own PostgreSQL database, Redis, API, worker and web app with Docker. Patient data never leaves that hospital's server, and each installation has its own secrets and encryption keys in its `.env`.

Inside one installation, everything belongs to a hospital record:

- Departments, staff accounts, kiosks, patients, visits and the queue all carry the hospital's ID.
- PostgreSQL row-level security makes the database itself refuse any read or write outside the signed-in hospital, so one hospital's staff can never see another's patients, even through a bug in the app.
- Every kiosk is registered by that hospital's administrator, so a kiosk can only add patients to that hospital's queue. One hospital can run any number of kiosks; all of them feed the same department queues.

This also allows **several hospitals on one server** (for example a district hosting its PHCs centrally). They share the machine but stay isolated by the same rules. Use `setup-hospital.ps1 -AddHospital` for each extra hospital.

## Install at a new hospital

Needs: a Windows PC or server on the hospital network with Docker Desktop, and this repository.

```powershell
cd C:\OPD-Saathi
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup-hospital.ps1
```

The script:

1. Creates `.env` with fresh random passwords and keys for this hospital.
2. Builds and starts the stack, and applies database migrations.
3. Asks for the hospital's name (shown on its kiosks and token slips), a short unique code, and the first administrator's name, email and password.
4. Creates the hospital, that administrator, and the 8 standard departments: General Medicine, Orthopaedics, ENT, Obstetrics and Gynaecology, Paediatrics, Dermatology, Ophthalmology and General Surgery.
5. Prints the address staff PCs and kiosks use on the hospital network.

Run it again at any time to restart or update. It never replaces existing database passwords or encryption keys.

### Departments

The standard departments use the codes the automatic routing understands (`GENMED`, `ORTHO`, `ENT`, `OBG`, `PAED`, `DERM`, `OPHTH`, `SURG`). Kiosks send a patient to General Medicine first, then the routing moves them to the matching department. The administrator can rename, deactivate or delete departments the hospital does not have. A new department with its own code works for manual routing; patients reach it through a nurse or doctor rather than automatically.

## The hospital administrator's first day

1. Sign in at `/staff` and open **Settings**.
2. **Users**: add each doctor and nurse with their department.
3. **Kiosks**: register each kiosk by its location ("OPD entrance", "Paediatrics wing"). Copy its setup link and open it once on that kiosk. The kiosk then stays signed in by itself.
4. If a kiosk device is replaced or its browser is reset, choose **New activation code** for that kiosk. It keeps the same kiosk record and history.

## Before going live

- **HTTPS.** Browsers only allow the microphone on `https://` or `localhost`. Until the hospital address has a certificate, kiosks on other machines can be tapped and typed on but cannot use voice. Either put a reverse proxy with a certificate in front of the web port, or for managed kiosk PCs start Chrome with `--unsafely-treat-insecure-origin-as-secure=http://<server-address>:5173`.
- **AI and voice keys.** Put `GEMINI_API_KEY` (and optionally `SARVAM_API_KEY`) in `.env` and run the script again. Without them OPD Saathi uses its rules and the browser's voice.
- **Backups.** Back up the database and the `.env` file together (`scripts/backup.sh`). Without the `.env`, the encrypted patient fields cannot be read.
- **Keep `.env` private.** It holds this hospital's encryption and signing keys.
