"""Staff password rules (Phase 7, task 7.3.5), following NIST SP 800-63B.

Length does most of the work: at least 12 characters, no composition rules, no expiry. On top,
refuse passwords an attacker tries first: common long passwords, the usual "word + 123 / @2026"
patterns built on common, Indian and hospital words, keyboard and digit runs, too few distinct
characters, and passwords built from the person's own name, email or hospital. The check
compares lowercase letters and digits only, so "Password@1234" and "password1234" are the same.
"""

import re
from functools import lru_cache

MIN_LENGTH = 12
MAX_LENGTH = 1024

# Common passwords of 12+ characters seen in public breach lists.
_COMMON_LONG = """
123456789012 1234567890123 12345678901234 123456123456 111111111111 000000000000
123123123123 112233445566 147258369147 987654321012 121212121212 qwertyuiop12
qwertyuiopasdf qwertyuiop123 qwertyuiopasdfghjkl 1qaz2wsx3edc 1q2w3e4r5t6y 1q2w3e4r5t6y7u
zaq12wsxcde3 asdfghjkl123 zxcvbnm12345 abcdefghijkl abcd12345678 password1234
password12345 passwordpassword iloveyou1234 letmein12345 welcome12345 changeme1234
administrator 1234qwerasdf qwerty123456 football1234 baseball1234 sunshine1234
princess1234 superman1234 starwars1234 trustno11234 whatever1234 mypassword123
""".split()
# Words people build passwords from: common, Indian, and this product's own world.
_BASES = """
password passw0rd welcome letmein iloveyou qwerty admin administrator sunshine princess
football cricket monkey dragon master shadow superman batman india bharat hindustan jaihind
mumbai delhi nagpur pune nashik aurangabad maharashtra krishna ganesh shivaji hanuman
saibaba omsairam jaishriram hospital doctor nurse patient medical health clinic opd
opdsaathi opdsathi saathi sathi kiosk government sarkari changeme default
""".split()
_SUFFIXES = ("", "1", "12", "123", "1234", "12345", "123456", "1234567", "12345678",
             "123456789", "2024", "2025", "2026", "2027", "111", "000", "007", "786", "01", "11")
_RUNS = ("0123456789", "9876543210", "qwertyuiop", "asdfghjkl", "zxcvbnm", "abcdefghijklmnop")


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    words = set(_COMMON_LONG)
    for base in _BASES:
        for suffix in _SUFFIXES:
            words.add(base + suffix)
            words.add(base + base + suffix)
            words.add(suffix + base)
    return frozenset(word for word in words if len(word) >= 8)


def password_problem(password: str, *, personal: tuple[str, ...] = ()) -> str | None:
    """Why this password is refused (a short sentence for the person), or None if it is fine.

    ``personal`` holds the person's name, email and hospital name: a password built from
    them is refused.
    """
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    compact = _normalize(password)
    if len(set(password)) < 5:
        return "Use more different characters."
    if compact in _common() or compact.rstrip("0123456789") in _common():
        return "This password is too common. Choose another."
    for run in _RUNS:
        if len(compact) >= MIN_LENGTH and compact in (run * 3) and len(set(compact)) > 1:
            return "Avoid keyboard or number sequences."
    for item in personal:
        for part in re.split(r"[^a-z0-9]+", item.lower()):
            if len(part) >= 4 and part in compact and len(compact) - len(part) < 8:
                return "Do not build the password from your name, email or hospital."
    return None
