# Runbook: power failure and the UPS

**Alerts:** UPS on battery, UPS battery low (from the UPS's own monitoring) ·
**Owner:** hospital IT and electrical maintenance

The server runs on a UPS that gives at least 15 minutes and tells the server to shut down
cleanly before its battery runs out (task 7.1.5). A clean shutdown protects PostgreSQL; a sudden
power loss is survived too (PostgreSQL replays its log, Redis loses at most a second), but it is
the riskiest moment for a disk.

## Setting it up (once, at installation)

1. Connect the UPS's USB or network card to the server and install NUT
   (`apt install nut`), configured so that on "battery low" it runs `shutdown -h now`.
2. The systemd unit `opd-saathi.service` stops the containers cleanly on shutdown and starts
   them at boot (`scripts/setup-hospital.sh`).
3. Test once: pull the UPS's mains plug outside OPD hours and check that the server shuts down
   cleanly and comes back with everything running when power returns.

## When the power fails during OPD hours

1. Kiosks and staff PCs without a UPS go dark; the server keeps running on its UPS. Switch the
   desk to paper if the kiosks are down (`server-down.md`).
2. If power is not back before the UPS warns "battery low", let the server shut itself down.
3. When power returns, the server starts the stack by itself. Check `https://<host>/api/readyz`
   and Staff → Queue, then enter any paper visits.
