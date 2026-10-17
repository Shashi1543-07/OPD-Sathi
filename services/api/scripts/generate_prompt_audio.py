"""Generate language-specific fixed prompt audio and a text-hash manifest."""

import argparse
import hashlib
import json
import wave
from pathlib import Path
from typing import Any

from app.ai.providers.tts.factory import build_tts_provider
from app.config import Settings
from app.domain.interview.consent import DANGER_TILES, get_consent_copy

ROOT = Path(__file__).parents[3]
WEB = ROOT / "apps" / "web"
OUTPUT = WEB / "public" / "audio"
MANIFEST = OUTPUT / "manifest.json"
WEB_MANIFEST = WEB / "src" / "lib" / "promptAudioManifest.json"
LANGUAGES = ("en", "hi", "mr")


def _walk_questions(path: Path) -> list[tuple[str, dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found: list[tuple[str, dict[str, str]]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            prompt = value.get("prompt")
            item_id = value.get("id")
            if isinstance(prompt, dict) and isinstance(item_id, str):
                found.append((f"question.{item_id}", prompt))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found


def _ui_strings(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            found.extend(_ui_strings(child, child_prefix))
    elif isinstance(value, str) and value.strip():
        found.append((prefix, value))
    return found


def collect_prompts() -> dict[str, dict[str, str]]:
    prompts: dict[str, dict[str, str]] = {}
    bank = ROOT / "services" / "api" / "app" / "domain" / "interview" / "bank"
    for path in sorted(bank.rglob("*.json")):
        if path.name == "danger_tiles.json":
            continue
        for prompt_id, translations in _walk_questions(path):
            prompts[prompt_id] = {lang: translations[lang] for lang in LANGUAGES}
    for language in LANGUAGES:
        prompts.setdefault("consent", {})[language] = get_consent_copy(language)["text"]
        # The separate notice about online AI help is read aloud like the first.
        prompts.setdefault("consent_ai", {})[language] = get_consent_copy(language, "ai")["text"]
    for tile_id, translations in DANGER_TILES.items():
        prompts[f"danger.{tile_id}"] = dict(translations)

    for language in LANGUAGES:
        locale_root = WEB / "src" / "locales" / language
        for namespace in ("common", "kiosk"):
            locale = json.loads((locale_root / f"{namespace}.json").read_text(encoding="utf-8"))
            for key, text in _ui_strings(locale):
                prompts.setdefault(f"ui.{namespace}.{key}", {})[language] = text
    missing = [key for key, texts in prompts.items() if set(texts) != set(LANGUAGES)]
    if missing:
        raise ValueError(f"prompt translations missing in one or more languages: {missing[:10]}")
    return prompts


def _duration_seconds(audio: bytes, suffix: str) -> float:
    if suffix == ".wav":
        from io import BytesIO

        with wave.open(BytesIO(audio), "rb") as wav:
            return round(wav.getnframes() / wav.getframerate(), 3)
    return round(max(0.1, len(audio) * 8 / 64_000), 3)


async def generate(*, check: bool = False, live: bool = False) -> dict[str, int]:
    prompts = collect_prompts()
    prior: dict[tuple[str, str], dict[str, Any]] = {}
    if MANIFEST.exists():
        old = json.loads(MANIFEST.read_text(encoding="utf-8"))
        prior = {(item["id"], item["lang"]): item for item in old.get("items", [])}
    if check:
        missing = []
        for prompt_id, translations in prompts.items():
            for language, text in translations.items():
                item = prior.get((prompt_id, language))
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if (
                    not item
                    or item.get("text_sha256") != digest
                    or not (OUTPUT / item["file"]).is_file()
                ):
                    missing.append(f"{language}:{prompt_id}")
        if missing:
            raise SystemExit(
                f"Prompt audio coverage missing or stale ({len(missing)}): {missing[:10]}"
            )
        return {
            "prompt_ids": len(prompts),
            "language_files": sum(map(len, prompts.values())),
            "generated": 0,
        }

    provider = build_tts_provider(Settings() if live else Settings(tts_provider="mock"))
    items: list[dict[str, Any]] = []
    generated = 0
    mock_clip = None
    for prompt_id, translations in sorted(prompts.items()):
        for language, text in sorted(translations.items()):
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            previous = prior.get((prompt_id, language))
            if previous and previous.get("text_sha256") == digest:
                existing = OUTPUT / previous["file"]
                if existing.is_file():
                    items.append(previous)
                    continue
            clip = (
                mock_clip
                if not live and mock_clip is not None
                else await provider.synthesize(text, language)
            )
            if not live and mock_clip is None:
                mock_clip = clip
            audio = clip.content
            suffix = ".wav" if clip.media_type == "audio/wav" else ".mp3"
            filename = (
                f"mock-tone{suffix}"
                if not live
                else f"{hashlib.sha256(prompt_id.encode()).hexdigest()[:20]}{suffix}"
            )
            relative = f"{language}/{filename}"
            target = OUTPUT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(audio)
            items.append(
                {
                    "id": prompt_id,
                    "lang": language,
                    "text_sha256": digest,
                    "file": relative,
                    "duration_seconds": _duration_seconds(audio, suffix),
                }
            )
            generated += 1
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    WEB_MANIFEST.write_text(
        json.dumps(
            {
                lang: {
                    item["id"]: f"/audio/{item['file']}" for item in items if item["lang"] == lang
                }
                for lang in LANGUAGES
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"prompt_ids": len(prompts), "language_files": len(items), "generated": generated}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="check complete, current audio manifest"
    )
    parser.add_argument("--live", action="store_true", help="use configured paid TTS provider")
    args = parser.parse_args()
    import asyncio

    print(json.dumps(asyncio.run(generate(check=args.check, live=args.live)), indent=2))


if __name__ == "__main__":
    main()
