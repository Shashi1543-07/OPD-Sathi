"""Strict, versioned clinical intake schema; this is history, never a diagnosis."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = Annotated[str, Field(max_length=500)]
Confidence = Annotated[float, Field(ge=0, le=1)]
ShortList = Annotated[list[Text], Field(max_length=30)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TextPair(StrictModel):
    text_original: Text
    text_en: Text
    confidence: Confidence | None = None


class Duration(StrictModel):
    value: Annotated[float, Field(gt=0, le=100_000)]
    unit: Literal["minute", "hour", "day", "week", "month", "year"]


class HPI(StrictModel):
    onset: TextPair | None = None
    duration: Duration | None = None
    location: TextPair | None = None
    character: TextPair | None = None
    severity_0_10: Annotated[int, Field(ge=0, le=10)] | None = None
    aggravating: Annotated[list[TextPair], Field(max_length=30)] = Field(default_factory=list)
    relieving: Annotated[list[TextPair], Field(max_length=30)] = Field(default_factory=list)
    radiation: TextPair | None = None
    timing: TextPair | None = None
    associated_symptoms: Annotated[list[TextPair], Field(max_length=30)] = Field(
        default_factory=list
    )
    pertinent_negatives: Annotated[list[TextPair], Field(max_length=30)] = Field(
        default_factory=list
    )


class PastHistory(StrictModel):
    conditions: ShortList = Field(default_factory=list)
    surgeries: ShortList = Field(default_factory=list)


class Medication(StrictModel):
    name: Text
    dose: Text | None = None
    source: Literal["patient_stated", "document", "nurse", "doctor"]


class SocialHistory(StrictModel):
    tobacco: TextPair | None = None
    alcohol: TextPair | None = None
    # Anything else the patient said about their habits, in their own words.
    other: Annotated[list[TextPair], Field(max_length=10)] = Field(default_factory=list)


class Unanswered(StrictModel):
    """A history question the patient could not or chose not to answer; not a denial."""

    question_id: Annotated[str, Field(min_length=1, max_length=80)]
    status: Literal["unknown", "declined"]
    text_en: Text


# Ashtavidha Pariksha (eight-fold examination) and Dashavidha Pariksha (ten-fold assessment).
AyushExamField = Literal[
    "nadi",
    "mutra",
    "mala",
    "jihva",
    "shabda",
    "sparsha",
    "drik",
    "akriti",
    "prakriti",
    "vikriti",
    "sara",
    "samhanana",
    "pramana",
    "satmya",
    "sattva",
    "vaya",
]


class AyushHistory(StrictModel):
    """Ayurveda history; a draft for the vaidya to review.

    The patient reports appetite, digestion, bowel habit, sleep, capacity for exertion and
    diet at the kiosk. Examination findings are entered by the clinician only and are never
    asked of the patient.
    """

    appetite: TextPair | None = None
    digestion: TextPair | None = None
    bowel: TextPair | None = None
    sleep: TextPair | None = None
    exercise: TextPair | None = None
    diet: TextPair | None = None
    examination: dict[AyushExamField, Text] = Field(default_factory=dict)


class WomenHealth(StrictModel):
    pregnant: bool | None = None
    lmp: Text | None = None


class RedFlag(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Z0-9_]+$")]
    level: Literal["priority", "urgent"]
    source: Literal["tile", "rule", "llm", "nurse"]
    evidence: Text
    reason_en: Text | None = None


class Routing(StrictModel):
    department_code: Annotated[str, Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_]+$")]
    confidence: Confidence
    reason_en: Text
    needs_nurse_confirmation: bool


class PatientConfirmation(StrictModel):
    confirmed_by_patient: bool = False
    corrections: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)
    needs_assistance: bool = False


class FieldMeta(StrictModel):
    source: Literal["patient_voice", "patient_touch", "patient_text", "nurse", "doctor", "document"]
    confidence: Confidence
    confirmed: bool
    ai_assisted: bool = False


QuestionId = Annotated[str, Field(min_length=1, max_length=80)]


class SummaryStatement(StrictModel):
    """One sentence of the AI brief and the asked questions whose answers support it."""

    text_en: Annotated[str, Field(min_length=1, max_length=300)]
    sources: Annotated[list[QuestionId], Field(min_length=1, max_length=10)]


class SummaryReview(StrictModel):
    """A doctor's decision on the AI draft; the latest decision replaces an earlier one."""

    status: Literal["accepted", "edited", "rejected"]
    by_name: Annotated[str, Field(max_length=200)] = ""
    at: Annotated[str, Field(max_length=40)]
    # The model's wording, kept when a doctor rewrites the brief.
    original_brief_en: Annotated[str, Field(max_length=600)] | None = None


class AISummary(StrictModel):
    """Doctor-facing English translation produced by the language model; history only.

    Without a ``review`` it is an unreviewed draft. ``statements`` split the brief into
    sentences, each citing the asked questions it rests on, so the doctor can check them.
    """

    brief_en: Annotated[str, Field(max_length=600)]
    answers_en: dict[Annotated[str, Field(max_length=80)], Text] = Field(default_factory=dict)
    model: Annotated[str, Field(max_length=80)] = ""
    generated_at: Annotated[str, Field(max_length=40)] = ""
    statements: Annotated[list[SummaryStatement], Field(max_length=8)] = Field(
        default_factory=list
    )
    review: SummaryReview | None = None


class CaseSheet(StrictModel):
    schema_version: Literal["1.0"]
    language: Literal["en", "hi", "mr"]
    chief_complaint: TextPair
    hpi: HPI
    past_history: PastHistory = Field(default_factory=PastHistory)
    medications: Annotated[list[Medication], Field(max_length=30)] = Field(default_factory=list)
    allergies: Annotated[list[TextPair], Field(max_length=30)] = Field(default_factory=list)
    family_history: Annotated[list[TextPair], Field(max_length=30)] = Field(default_factory=list)
    social: SocialHistory = Field(default_factory=SocialHistory)
    women_health: WomenHealth | None = None
    # Questions answered "don't know" or "prefer not to say": still open for the doctor.
    unanswered: Annotated[list[Unanswered], Field(max_length=30)] = Field(default_factory=list)
    ayush: AyushHistory | None = None
    red_flags: Annotated[list[RedFlag], Field(max_length=30)] = Field(default_factory=list)
    routing: Routing
    field_meta: dict[str, FieldMeta] = Field(default_factory=dict)
    patient_confirmation: PatientConfirmation = Field(default_factory=PatientConfirmation)
    ai_summary: AISummary | None = None
    # English text of the AI follow-up questions this patient was asked, keyed by id.
    followup_questions: dict[Annotated[str, Field(max_length=80)], Text] = Field(
        default_factory=dict
    )
    disclaimer: Text = (
        "Collected by OPD Saathi from the patient's own statements. History only. Not a diagnosis."
    )

    @field_validator("field_meta")
    @classmethod
    def validate_json_pointers(cls, value: dict[str, FieldMeta]) -> dict[str, FieldMeta]:
        for pointer in value:
            if len(pointer) > 500 or not re.fullmatch(
                r"/(?:[^~/]|~[01])+(?:/(?:[^~/]|~[01])+)*", pointer
            ):
                raise ValueError("field_meta keys must be valid absolute JSON Pointers")
        return value
