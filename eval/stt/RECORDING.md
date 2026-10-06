# Volunteer speech recording protocol

This script is for optional, local evaluation only. **Do not record patients.** Phase 8 CI uses the text-only Mock check and does not need audio or a provider key. No live speech accuracy is claimed until consenting volunteers and a configured provider are available.

## Script size and language mix

Read each synthetic sentence in [`fixtures.jsonl`](fixtures.jsonl) once, using its `text` exactly as the reference transcript. The file contains 80 prompts: 30 Hindi (`hi-IN`), 30 Marathi (`mr-IN`), and 20 English (`en-IN`). They describe fictional symptoms and contain no real patient details.

Use three adult volunteers who each give informed consent. Distribute the 80 clips as evenly as possible across speakers, with all three languages represented for each speaker. Record 40 clips in a quiet room and 40 with controlled background noise. Do not record bystanders or add intelligible background speech. If a volunteer declines, stop; do not replace the person with a patient or collect extra personal details.

## Consent and privacy steps

1. Explain the purpose, the microphone and transcription provider, the fact that voice can identify a speaker, who can access the files, and the deletion date. For a live-provider run, explicitly explain that audio is sent to that external provider.
2. Obtain and retain written consent outside this repository before recording. Participation is optional and can be stopped at any time without consequence.
3. Use only the scripted synthetic text. Do not ask volunteers to mention names, contact details, addresses, medical history, or other personal information.
4. Store temporary WAV/PCM clips only under `eval/stt/audio/` on encrypted local storage. Keep references, language, speaker code (S1–S3), noise condition, and temporary clip filename in the local manifest; never put a person's name or consent form in the manifest.
5. Use the text-only command `make eval-stt` for CI. A live command is intentionally not provided yet; do not send recordings to a provider until a live evaluator is implemented and consent covers that provider.
6. Delete clips, temporary manifests, exports, and backups immediately after the evaluation is reviewed. Record the deletion date and aggregate measurements only. The audio directory is Git-ignored as an additional safeguard; do not force-add recordings.

## Evaluation limits

The CI report evaluates fixture wiring, Unicode token error calculations, keyword recall calculations, and Mock adapter latency. Its WER of zero is expected because Mock returns the configured fixture text exactly; it is **not** a speech-recognition accuracy result. Live WER, symptom-keyword recall, and latency must be measured from the consenting-speaker recordings before reporting those metrics.
