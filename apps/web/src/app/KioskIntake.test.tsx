import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { useLanguageStore } from "../languageStore";
import { useKioskPrefs } from "../lib/kioskPrefs";
import { KioskIntake } from "./KioskIntake";

type Handlers = {
  onPartial?: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (code: string) => void;
  onProcessing?: () => void;
};

const { intakeMock, voiceMock, IntakeRequestErrorMock, captured } = vi.hoisted(() => {
  class IntakeRequestErrorMock extends Error {
    constructor(
      readonly code: string,
      message: string,
      readonly status: number,
    ) {
      super(message);
    }
  }
  const captured: { handlers?: Handlers; stop: () => void } = {
    stop: () => undefined,
  };
  return {
    IntakeRequestErrorMock,
    captured,
    intakeMock: {
      session: vi.fn(),
      setStage: vi.fn(),
      nextQuestion: vi.fn(),
      answer: vi.fn(),
      correct: vi.fn(),
      identity: vi.fn(),
      linkOpdCard: vi.fn(),
      markFollowUp: vi.fn(),
      dangerTiles: vi.fn(),
      capabilities: vi.fn(),
      transcribe: vi.fn(),
      speechAudio: vi.fn(),
      changeLanguage: vi.fn(),
      readPrescription: vi.fn(),
      consent: vi.fn(),
      aiConsent: vi.fn(),
    },
    voiceMock: {
      listenWithServer: vi.fn((_transcribe: unknown, handlers: Handlers) => {
        captured.handlers = handlers;
        return { stop: () => captured.stop(), cancel: vi.fn() };
      }),
      listenInBrowser: vi.fn((_language: string, handlers: Handlers) => {
        captured.handlers = handlers;
        return { stop: () => captured.stop(), cancel: vi.fn() };
      }),
    },
  };
});

vi.mock("../lib/intake", () => ({
  intake: intakeMock,
  IntakeRequestError: IntakeRequestErrorMock,
  useKioskHospitalName: () => "Test Hospital",
}));
vi.mock("../lib/voice", () => ({
  browserRecognitionAvailable: () => false,
  recordingAvailable: () => true,
  secureVoiceAvailable: () => true,
  listenWithServer: voiceMock.listenWithServer,
  listenInBrowser: voiceMock.listenInBrowser,
  speak: vi.fn(async () => undefined),
  stopSpeaking: vi.fn(),
}));

const consent = {
  language: "en",
  version: "v1",
  sha256: "a".repeat(64),
  text: "Synthetic consent copy",
};

const aiConsent = {
  language: "en",
  version: "ai-consent-1.0",
  sha256: "b".repeat(64),
  text: "Synthetic notice about optional AI help",
};

function renderIntake(stage: string, extra: Record<string, unknown> = {}) {
  intakeMock.session.mockReturnValue({
    visit_id: "visit-synthetic",
    patient_access_token: "synthetic-token",
    consent,
    stage,
    ...extra,
  });
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <KioskIntake />
      </MemoryRouter>
    </I18nextProvider>,
  );
}

const chiefConcern = {
  id: "chief_concern",
  input: "text",
  optional: false,
  multi: true,
  prompt: "What is the main problem that brought you here today?",
  options: [
    { id: "fever", label: "Fever", none: false },
    { id: "cough", label: "Cough", none: false },
  ],
};

beforeEach(() => {
  useKioskPrefs.getState().setVoiceGuidance(false);
  intakeMock.capabilities.mockResolvedValue({ stt: "gemini", tts: "browser", ai: false });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useLanguageStore.getState().setLanguage("en");
});

describe("kiosk intake", () => {
  it("links a returning patient's OPD card and asks whether it is the same problem", async () => {
    const user = userEvent.setup();
    intakeMock.identity.mockResolvedValue({ next: "danger_signs" });
    intakeMock.linkOpdCard.mockResolvedValue({
      linked: true,
      opd_number: "26-000123",
      visit_count: 2,
      previous_visit: { id: "visit-earlier", date: "2026-10-03" },
    });
    intakeMock.markFollowUp.mockResolvedValue({ previous_visit_id: "visit-earlier" });
    intakeMock.dangerTiles.mockResolvedValue({ tiles: [] });
    renderIntake("identity");

    await user.type(screen.getByLabelText("How old are you?"), "75");
    await user.click(screen.getByRole("radio", { name: "Female" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(
      await screen.findByRole("heading", { name: "Have you visited this hospital before?" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Yes, I have my OPD card" }));
    await user.type(screen.getByLabelText(/OPD number/), "26-000123");
    const next = screen.getByRole("button", { name: "Next" });
    await user.type(screen.getByLabelText(/PIN/), "1234");
    expect(next).toBeDisabled(); // A PIN has six digits.
    await user.type(screen.getByLabelText(/PIN/), "56");
    await user.click(next);

    expect(intakeMock.linkOpdCard).toHaveBeenCalledWith("26-000123", "123456");
    expect(await screen.findByRole("heading", { name: /same problem/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Yes, the same problem" }));
    expect(intakeMock.markFollowUp).toHaveBeenCalledWith("visit-earlier");
    expect(
      await screen.findByRole("heading", { name: "Do you have any of these right now?" }),
    ).toBeInTheDocument();
  });

  it("explains a card that does not match and lets the patient go on without it", async () => {
    const user = userEvent.setup();
    intakeMock.identity.mockResolvedValue({ next: "danger_signs" });
    intakeMock.linkOpdCard.mockRejectedValue(
      new IntakeRequestErrorMock("CARD_NOT_RECOGNISED", "We could not match this OPD card.", 422),
    );
    intakeMock.dangerTiles.mockResolvedValue({ tiles: [] });
    renderIntake("identity");

    await user.type(screen.getByLabelText("How old are you?"), "40");
    await user.click(screen.getByRole("radio", { name: "Male" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Yes, I have my OPD card" }));
    await user.type(screen.getByLabelText(/OPD number/), "26-000999");
    await user.type(screen.getByLabelText(/PIN/), "000000");
    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("We could not match this OPD card.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue without the card" }));
    expect(
      await screen.findByRole("heading", { name: "Do you have any of these right now?" }),
    ).toBeInTheDocument();
    expect(intakeMock.markFollowUp).not.toHaveBeenCalled();
  });

  it("asks about AI help separately after the consent, and either answer continues", async () => {
    const user = userEvent.setup();
    intakeMock.consent.mockResolvedValue({ next: "identity" });
    intakeMock.aiConsent.mockResolvedValue({ granted: false });
    renderIntake("consent", { ai_consent: aiConsent });

    await user.click(await screen.findByRole("button", { name: "I agree, continue" }));
    expect(
      await screen.findByRole("heading", { name: "Help from AI (optional)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Synthetic notice about optional AI help")).toBeInTheDocument();
    // Two equal choices; nothing is chosen for the patient.
    expect(screen.getByRole("button", { name: "Yes, use AI help" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "No, continue without AI" }));
    await waitFor(() => expect(intakeMock.aiConsent).toHaveBeenCalledWith(aiConsent, false));
    expect(await screen.findByLabelText("How old are you?")).toBeInTheDocument();
    // What the patient may use is fetched again for their answer.
    await waitFor(() => expect(intakeMock.capabilities).toHaveBeenCalledTimes(2));
  });

  it("offers no voice at all to a patient who said no to AI help", async () => {
    intakeMock.capabilities.mockResolvedValue({
      stt: "off",
      tts: "browser",
      ai: false,
      online: false,
    });
    intakeMock.nextQuestion.mockResolvedValue({
      question: { ...chiefConcern, options: [] },
      progress: { current: 1, total: 10 },
      answered: [],
    });
    renderIntake("interview");

    expect(
      await screen.findByText("Without AI help, please tap or type your answers."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tap and speak" })).toBeNull();
  });

  it("takes a first-time patient straight on to the safety questions", async () => {
    const user = userEvent.setup();
    intakeMock.identity.mockResolvedValue({ next: "danger_signs" });
    intakeMock.dangerTiles.mockResolvedValue({ tiles: [] });
    renderIntake("identity");

    await user.type(screen.getByLabelText("How old are you?"), "29");
    await user.click(screen.getByRole("radio", { name: "Female" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "No, or I don't have my card" }));
    expect(
      await screen.findByRole("heading", { name: "Do you have any of these right now?" }),
    ).toBeInTheDocument();
    expect(intakeMock.linkOpdCard).not.toHaveBeenCalled();
  });

  it("builds the answer from tapped options", async () => {
    const user = userEvent.setup();
    intakeMock.nextQuestion.mockResolvedValue({
      question: chiefConcern,
      progress: { current: 1, total: 10 },
      answered: [],
    });
    intakeMock.answer.mockResolvedValue({ question: chiefConcern, answered: [] });
    renderIntake("interview");

    expect(await screen.findByText("Question 1 of 10")).toBeInTheDocument();
    const next = screen.getByRole("button", { name: "Next" });
    expect(next).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Fever" }));
    await user.click(screen.getByRole("button", { name: "Cough" }));
    await user.click(next);
    await waitFor(() =>
      expect(intakeMock.answer).toHaveBeenCalledWith(
        "chief_concern",
        "Fever, Cough",
        false,
        "touch",
      ),
    );
  });

  it("asks the patient to tap again when the connection drops while answering", async () => {
    const user = userEvent.setup();
    intakeMock.nextQuestion.mockResolvedValue({
      question: chiefConcern,
      progress: { current: 1, total: 10 },
      answered: [],
    });
    intakeMock.answer
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        question: { ...chiefConcern, id: "duration", prompt: "Since when?", options: [] },
        progress: { current: 2, total: 10 },
        answered: [],
      });
    renderIntake("interview");

    await user.click(await screen.findByRole("button", { name: "Fever" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The connection dropped. Please tap again. Your answers are kept.",
    );
    expect(screen.queryByText("Failed to fetch")).toBeNull();
    // The server recognises an answer it already saved, so tapping again is safe.
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Since when?")).toBeInTheDocument();
    expect(intakeMock.answer).toHaveBeenCalledTimes(2);
  });

  it("says why a prescription photo could not be used", async () => {
    const user = userEvent.setup();
    intakeMock.capabilities.mockResolvedValue({ stt: "gemini", tts: "browser", ai: true });
    intakeMock.nextQuestion.mockResolvedValue({
      question: {
        id: "medications",
        input: "text",
        optional: true,
        multi: false,
        prompt: "Which medicines do you take?",
        options: [],
      },
      progress: { current: 6, total: 10 },
      answered: [],
    });
    intakeMock.readPrescription
      .mockRejectedValueOnce(new IntakeRequestErrorMock("UNSUPPORTED_IMAGE", "Not a photo", 415))
      .mockRejectedValueOnce(new IntakeRequestErrorMock("IMAGE_TOO_LARGE", "Too large", 413));
    const { container } = renderIntake("interview");

    await screen.findByRole("button", { name: "Scan old prescription" });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const photo = new File([new Uint8Array([0xff, 0xd8, 0xff])], "rx.jpg", { type: "image/jpeg" });
    await user.upload(input!, photo);
    expect(
      await screen.findByText("That file is not a photo. Please take a photo of the prescription."),
    ).toBeInTheDocument();
    await user.upload(input!, photo);
    expect(
      await screen.findByText("That photo is too large. Please take the photo again."),
    ).toBeInTheDocument();
  });

  it("treats a 'none of these' option as exclusive", async () => {
    const user = userEvent.setup();
    intakeMock.nextQuestion.mockResolvedValue({
      question: {
        id: "fever_associated",
        input: "text",
        optional: true,
        multi: true,
        prompt: "Along with the fever, do you have any of these?",
        options: [
          { id: "body_ache", label: "Body ache", none: false },
          { id: "none", label: "None of these", none: true },
        ],
      },
      answered: [],
    });
    intakeMock.answer.mockResolvedValue({ complete: false, answered: [] });
    renderIntake("interview");

    await user.click(await screen.findByRole("button", { name: "Body ache" }));
    await user.click(screen.getByRole("button", { name: "None of these" }));
    expect(screen.getByRole("button", { name: "Body ache" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(intakeMock.answer).toHaveBeenCalledWith(
        "fever_associated",
        "None of these",
        false,
        "touch",
      ),
    );
  });

  it("puts the spoken answer in an editable box and submits it as voice input", async () => {
    const user = userEvent.setup();
    intakeMock.nextQuestion.mockResolvedValue({ question: chiefConcern, answered: [] });
    intakeMock.answer.mockResolvedValue({ question: chiefConcern, answered: [] });
    renderIntake("interview");

    await user.click(await screen.findByRole("button", { name: /Tap and speak/ }));
    expect(voiceMock.listenWithServer).toHaveBeenCalled();
    expect(screen.getByText("Listening… speak now")).toBeInTheDocument();
    act(() => {
      captured.handlers?.onProcessing?.();
      captured.handlers?.onFinal("I have a headache since morning");
    });
    const box = screen.getByRole("textbox");
    expect(box).toHaveValue("I have a headache since morning");
    expect(screen.getByText(/We heard you/)).toBeInTheDocument();
    await user.clear(box);
    await user.type(box, "I have a headache");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(intakeMock.answer).toHaveBeenCalledWith(
        "chief_concern",
        "I have a headache",
        false,
        "voice",
      ),
    );
  });

  it("switches language mid-question and keeps the tapped answer", async () => {
    const user = userEvent.setup();
    const marathi = {
      ...chiefConcern,
      prompt: "आज तुम्ही कोणत्या मुख्य त्रासासाठी आला आहात?",
      options: [
        { id: "fever", label: "ताप", none: false },
        { id: "cough", label: "खोकला", none: false },
      ],
    };
    intakeMock.session.mockReturnValue({
      visit_id: "visit-synthetic",
      patient_access_token: "synthetic-token",
      consent: { ...consent, language: "mr" },
      stage: "interview",
    });
    intakeMock.nextQuestion
      .mockResolvedValueOnce({ question: marathi, answered: [] })
      .mockResolvedValueOnce({ question: chiefConcern, answered: [] });
    intakeMock.changeLanguage.mockResolvedValue({ language: "en", consent });
    intakeMock.answer.mockResolvedValue({ question: chiefConcern, answered: [] });
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <KioskIntake />
        </MemoryRouter>
      </I18nextProvider>,
    );
    await user.click(await screen.findByRole("button", { name: "ताप" }));
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(intakeMock.changeLanguage).toHaveBeenCalledWith("en");
    expect(
      await screen.findByRole("heading", { name: chiefConcern.prompt }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fever" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(intakeMock.answer).toHaveBeenCalledWith("chief_concern", "Fever", false, "touch"),
    );
  });

  it("falls back to typing when voice fails", async () => {
    const user = userEvent.setup();
    intakeMock.nextQuestion.mockResolvedValue({ question: chiefConcern, answered: [] });
    renderIntake("interview");

    await user.click(await screen.findByRole("button", { name: /Tap and speak/ }));
    act(() => captured.handlers?.onError("failed"));
    expect(
      screen.getByText("Voice is not working right now. Please tap or type your answer."),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });
});
