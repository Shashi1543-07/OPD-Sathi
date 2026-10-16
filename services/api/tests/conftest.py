"""Suite-wide safety: the tests never call live AI or speech providers.

A developer's .env may hold real Gemini or Sarvam keys (the demo uses them), and Settings read
.env, so without this the suite could send synthetic data to a provider and run up costs.
Environment variables take precedence over .env, so blanking them here is enough. CI has no
keys at all, so this only changes local runs. Tests that need a provider configure one
explicitly with Settings(...).
"""

import os

for _name in ("GEMINI_API_KEY", "SARVAM_API_KEY"):
    os.environ[_name] = ""
