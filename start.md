# How to start OPD Saathi

You only need **Docker Desktop** and one command. The app keeps running after you close VS Code or the terminal, until you stop it or shut down the PC.

## Every time you want to run it

1. **Start Docker Desktop** from the Start menu and wait until it says "Engine running" (the whale icon in the taskbar stops animating).
2. **Open PowerShell in the project folder:**
   ```powershell
   cd C:\Users\lenovo\Downloads\OPD-Sathi
   ```
3. **Run the start script** (two lines, in the same PowerShell window):
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\start-demo.ps1
   ```
   - The first line only allows scripts in this one window; it changes no system setting.
   - The first run after a restart takes about 1–3 minutes. If you haven't changed any code, use `.\start-demo.ps1 -NoBuild` to start faster.
   - When it finishes, it prints everything you need and opens the patient kiosk in your browser.

**What the script prints:**

- **Patient kiosk link:** `http://localhost:5173/kiosk?activate=...`. Open it once to activate the kiosk; after that, plain `http://localhost:5173/kiosk` works in that browser.
- **Staff workspace:** `http://localhost:5173/staff`
- **Logins:** the admin, doctor (`doctor01@demo.opdsathi.test`) and nurse (`nurse01@demo.opdsathi.test`) accounts with their passwords. These stay the same every run because they're stored in your `.env` file.
- **AI status:** confirms Gemini and Sarvam are ON when their keys are in `.env`.

If something else is already using port 5173, the script switches to **8080** and tells you, so use `http://localhost:8080` instead.

## For the demo

- Use **Chrome or Edge**; voice doesn't work properly in other browsers.
- Keep two windows side by side: the kiosk (press **F11** for full screen) and the staff workspace logged in as **doctor01**.

## To stop it

```powershell
cd C:\Users\lenovo\Downloads\OPD-Sathi
docker compose down
```

Your patients and data are kept. Only `docker compose down -v` wipes the database, so don't use that one before a demo.

## If something goes wrong

| Problem | Fix |
|---|---|
| "The term 'powershell' is not recognized" | You're already in PowerShell; don't type `powershell`. Run the two lines from step 3 instead. |
| "running scripts is disabled on this system" | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, then `.\start-demo.ps1`. |
| "Cannot connect to the Docker daemon" | Docker Desktop isn't running yet. Start it, wait, then run the script again. |
| Page won't load | Wait 20 seconds and refresh. If it still fails, run the script again. |
| Forgot the passwords | Run the script again (it prints them), or look in `.env` for `DEMO_ADMIN_PASSWORD` and `DEMO_STAFF_PASSWORD`. |
| Kiosk asks for an activation code | Run the script again and open the new activation link it prints. |
