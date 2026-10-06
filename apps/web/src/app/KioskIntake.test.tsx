import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { useLanguageStore } from "../languageStore";
import { KioskIntake } from "./KioskIntake";

const { intakeMock, audioMock, dispatchAudio, dispatchSpeech } = vi.hoisted(
  () => {
    let listener: ((event: MessageEvent<string>) => void) | undefined;
    let speechCallback: (() => void) | undefined;
    const socket = {
      readyState: 1,
      send: vi.fn(),
      close: vi.fn(),
      addEventListener: vi.fn(),
    } as unknown as WebSocket;
    return {
      intakeMock: {
        session: vi.fn(),
        setStage: vi.fn(),
        nextQuestion: vi.fn(),
        answer: vi.fn(),
        correct: vi.fn(),
        connectAudio: vi.fn(
          (onMessage: (event: MessageEvent<string>) => void) => {
            listener = onMessage;
            onMessage({
              data: JSON.stringify({ type: "ready" }),
            } as MessageEvent<string>);
            return socket;
          },
        ),
      },
      audioMock: {
        startMicrophoneCapture: vi.fn(
          async (_socket: WebSocket, callbacks: { onSpeech?: () => void }) => {
            speechCallback = callbacks.onSpeech;
            return vi.fn(async () => undefined);
          },
        ),
      },
      dispatchAudio: (packet: object) =>
        listener?.({ data: JSON.stringify(packet) } as MessageEvent<string>),
      dispatchSpeech: () => speechCallback?.(),
    };
  },
);

vi.mock("../lib/intake", () => ({ intake: intakeMock }));
vi.mock("../lib/audioCapture", () => ({
  CaptureError: class CaptureError extends Error {
    constructor(readonly code: string) {
      super(code);
    }
  },
  startMicrophoneCapture: audioMock.startMicrophoneCapture,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  useLanguageStore.getState().setLanguage("en");
});

describe("Phase 8 voice interview", () => {
  it("shows live speech text for editing and submits it as voice input", async () => {
    const user = userEvent.setup();
    intakeMock.session.mockReturnValue({
      visit_id: "visit-synthetic",
      patient_access_token: "synthetic-token",
      consent: {
        language: "en",
        version: "v1",
        sha256: "a".repeat(64),
        text: "Synthetic consent copy",
      },
      stage: "interview",
    });
    intakeMock.nextQuestion.mockResolvedValue({
      question: {
        id: "Q_CC",
        input: "text",
        optional: false,
        prompt: "What would you like to tell the doctor?",
      },
      answered: [],
    });
    intakeMock.answer.mockResolvedValue({
      question: {
        id: "Q_DURATION",
        input: "text",
        optional: true,
        prompt: "How long has this been happening?",
      },
      answered: [],
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <KioskIntake />
        </MemoryRouter>
      </I18nextProvider>,
    );

    expect(
      await screen.findByText(
        "Tap the microphone to speak. Your audio is sent for transcription and is not saved.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Speak your answer" }));
    await waitFor(() =>
      expect(screen.getByText("Listening. Speak now.")).toBeInTheDocument(),
    );
    expect(audioMock.startMicrophoneCapture).toHaveBeenCalled();

    act(() => dispatchAudio({ type: "partial", text: "I have a headache" }));
    expect(screen.getByPlaceholderText("Type your answer")).toHaveValue(
      "I have a headache",
    );
    act(() =>
      dispatchAudio({
        type: "final",
        text: "I have a headache",
        confidence: 0.4,
      }),
    );
    expect(
      screen.getByText(
        "Please check and edit the transcript before continuing.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(intakeMock.answer).toHaveBeenCalledWith(
        "Q_CC",
        "I have a headache",
        false,
        "voice",
      ),
    );

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Speak your answer" }),
      );
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("Listening. Speak now.")).toBeInTheDocument();
    act(() => {
      dispatchSpeech();
      vi.advanceTimersByTime(3_000);
    });
    expect(
      screen.getByText(
        "Voice service is unavailable. You can type or tap your answer.",
      ),
    ).toBeInTheDocument();
  });
});
