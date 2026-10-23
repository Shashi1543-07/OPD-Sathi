# Runbook: when OPD Saathi is down during OPD hours

**Alerts:** `ApiDown`, `WebDown`, `DatabaseDown` · **Owner:** OPD desk (paper), hospital IT
(server) · **Goal:** no patient waits longer because the system is down

The kiosk's offline short form covers a **network** problem between a kiosk and the server. This
runbook covers the **server** being unreachable or broken, when no kiosk, staff screen or status
page works. Patients must still be registered, seen in order, and recognised if very unwell.

## At the OPD desk (first 5 minutes)

1. **Switch to paper.** Take out the paper pack kept at the desk (see "The paper pack" below).
   Put the "Check-in is on paper today" sign on each kiosk.
2. **Danger signs first.** The desk nurse asks every arriving patient the six danger-sign
   questions printed on the front of the intake form. Any "yes" goes straight to the nurse, as
   the kiosk would have sent them.
3. **Tokens from the manual roll.** Give each patient the next token from the department's
   numbered roll (P-001, P-002, ...; "P" marks a paper token so it can never clash with a kiosk
   token). Write the token on the patient's intake form.
4. **Call in roll order**, urgent patients first, as the nurse decides.
5. Tell hospital IT (number on the wall of the server room and on the desk card).

## Hospital IT (in parallel)

1. Check the server is powered and reachable. On a UPS alarm, see `docs/runbooks/power.md`.
2. On the server:

   ```sh
   cd /opt/opd-saathi
   docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
   docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 200 api worker
   ```

3. Follow the runbook for what you find: database (`database-down.md`), disk full (`disk-full.md`),
   Redis lost (`redis-loss.md`), certificate expired (`certificate.md`). If the server itself is
   lost, restore to the standby or spare (`restore.md`).
4. Tell the desk when it is back, and the expected time while it is not.

## When the system is back

1. Kiosks and staff screens reconnect by themselves; reload a screen that does not within a minute.
2. **Enter the paper visits** in arrival order, from Staff → Queue → Nurse-assisted registration,
   one per paper form, writing the system token on the form beside the paper token. A patient
   already seen is entered and then completed, so the record is whole.
3. Patients still waiting keep their paper token; the desk calls paper tokens before new kiosk
   tokens of the same urgency until the paper queue is empty.
4. File the paper forms with the day's OPD records (they are medical records).
5. Record the outage in the incident log: start, end, cause, number of paper visits
   (`docs/INCIDENT_RESPONSE.md`; an outage of patient data systems may need a CERT-In report).

## The paper pack (kept at every OPD desk)

- Printed intake forms in English, Hindi and Marathi (from `docs/paper/`), at least one day's
  patients for the department.
- The six danger-sign questions on a laminated card.
- A numbered token roll per department, starting at P-001.
- "Check-in is on paper today" signs for the kiosks.
- This runbook, and IT's phone numbers.

The pack is checked every month by the OPD in-charge, and rehearsed once before go-live and
once a quarter (task 7.7.7).
