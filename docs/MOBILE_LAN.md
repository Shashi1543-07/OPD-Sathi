# Phones on the hospital Wi-Fi

How to let patients use OPD Saathi on their own phones while connected to the hospital's Wi-Fi or
LAN, without publishing the app on the internet. Everything below uses synthetic demo data.

## What works where

| | Kiosk PC (localhost) | Phone or second PC over `http://<PC address>:5173` |
|---|---|---|
| Interview by tapping and typing, token, live queue page | ✓ | ✓ |
| Staff console (queue, case sheets, settings) | ✓ | ✓ |
| Speaking answers (microphone) | ✓ | ✗ — browsers only allow the microphone on HTTPS or localhost; the page says so and offers tap/type |
| Encrypted offline short form | ✓ | ✗ (hidden: needs Web Crypto, which also requires HTTPS or localhost) |

No internet is needed for any of this; only the AI summary, Sarvam/Gemini speech and Chrome's own
speech recognition need it, and each falls back to tap/type and the rules-based case sheet.

## Start-up procedure (every time)

1. Connect the server PC and the phones to the **same** Wi-Fi or LAN.
2. Run `.\start-demo.ps1`. It detects this PC's current address, opens the web port to the
   network, and prints the phone check-in link and a phone test link.
3. Run `.\scripts\lan-check.ps1`. Every line should be `[ok]`.
4. On a phone, open the printed test link `http://<PC address>:5173/api/readyz`. It must show
   `{"status":"ready"}`.
5. In Staff → Settings → Kiosks, open **Show QR** on the phone check-in QR and print it.

**Run `start-demo.ps1` again after every reboot or network change.** Docker restarts the
containers by itself after a reboot, but with the address from the last run: token and check-in
QR codes then point at an address that no longer exists, and phones wait and fail. This is what
happened on 16 October 2026 (QR address 172.17.134.95, PC address 172.27.14.36).
`lan-check.ps1` reports exactly this mismatch.

## Phone check-in QR

An administrator creates it in Staff → Settings → Kiosks → Type: *Phone check-in QR*. Patients
scan it, choose a language and answer the same interview as at the kiosk, with the same consent,
safety rules and token. Then:

- Reloading the page or losing Wi-Fi briefly resumes the same visit; it never starts another one.
- After the token is issued, opening the link again shows that token ("You already have token…")
  with a button to the live queue page, and an option to check in someone else.
- Staff see a **Phone** badge on these patients in the queue, so the desk can greet them and check details.
- A phone can start at most 6 check-ins per QR in 10 minutes, and one QR at most 150 in
  10 minutes. Patients only enter the queue once they confirm.
- **Replace QR** makes old posters stop working at once; **Deactivate** pauses phone check-in
  (the page tells patients to use the kiosk or the desk).

## When phones cannot connect

Work down this list; `lan-check.ps1` checks the first four for you.

| Symptom | Cause | Fix |
|---|---|---|
| QR opens a page that loads forever | QR points at an old address | Run `.\start-demo.ps1 -NoBuild` again |
| `http://<address>:5173` refused on the phone | Web app only listens on this PC | Run `.\start-demo.ps1` while connected to the network |
| PC passes every check, phone times out | Wi-Fi isolates devices (campus and guest Wi-Fi do this), or the phone is on another network or subnet | Use a dedicated router, or a phone hotspot that the PC and the phones all join |
| Times out only while the network is *Public* | Windows blocks incoming connections on Public networks | On a trusted hospital network, set it to *Private* (Settings → Network → this network). If that is not allowed, an administrator can run `.\scripts\lan-check.ps1 -AddFirewallRule` (TCP 5173 from the local subnet only) and remove it afterwards with `-RemoveFirewallRule` |
| Page opens, buttons fail | An old build cached on the phone | Reload; the fix for plain-HTTP pages is in this release |

Never turn the Windows Firewall off. On this machine Docker Desktop already has an inbound
*Allow* rule for the Public profile, so no extra rule was needed.

## Voice on phones (optional, not set up by default)

The microphone needs HTTPS. For a demonstration with the team's own phones, the existing nginx
in the web container can serve HTTPS with a certificate from a local certificate authority whose
root certificate is installed on those phones. This adds no new component (see ADR 001), but
installing a root certificate on patients' phones is not acceptable, so a hospital deployment
needs a proper certificate for an internal host name instead. Until then, phones use tap and type.

## Development mode

`npm run dev` listens on all addresses and now sends API calls to `/api` on the same address
(proxied to the API on port 8000), exactly like the Docker build, so a phone can open the dev
server too. Set `VITE_DEV_API_TARGET` to point the proxy elsewhere.
