"""Validate multilingual interview and consent content for CI."""

from app.domain.interview.bank import validate_question_bank
from app.domain.interview.consent import get_consent_copy


def main() -> None:
    result = validate_question_bank()
    for language in ("en", "hi", "mr"):
        get_consent_copy(language)  # type: ignore[arg-type]
    print(f"Validated {result['questions']} questions across {result['modules']} modules.")


if __name__ == "__main__":
    main()
