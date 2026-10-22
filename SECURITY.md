# Security policy

OPD Saathi handles health information about patients in Indian public hospitals. We take
reports about its security seriously and are grateful for them.

## Reporting a vulnerability

Report privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue, pull request or discussion about
a vulnerability before it is fixed.

Include what you found, how to reproduce it, and what an attacker could do with it. Use only
synthetic data: never real patient information, and never test against a hospital's
installation. Testing against your own local installation (`start-demo.ps1`) is welcome.

## What to expect

| Step | Time |
|---|---|
| We acknowledge your report | within 3 working days |
| We confirm whether it is a vulnerability and how severe it is | within 10 working days |
| A fix for a critical or high finding is released | within 30 days of confirmation |
| A fix for a medium or low finding is released | in a scheduled release |

We will tell you when the fix is released and credit you in the release notes if you wish.

Hospitals running OPD Saathi receive security releases through their support channel
(`docs/DEPLOYMENT.md`, "Upgrades"). A hospital that suffers an incident follows its breach and
incident procedure (`docs/INCIDENT_RESPONSE.md`), which includes the CERT-In 6-hour report.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope: the API (`services/api`), the web app (`apps/web`), the container images, the
deployment scripts and configuration in this repository.

Out of scope: denial of service by volume, findings that need a compromised server or
administrator account, and third-party services (Google Gemini, Sarvam AI, SMS providers),
which you should report to those providers.
