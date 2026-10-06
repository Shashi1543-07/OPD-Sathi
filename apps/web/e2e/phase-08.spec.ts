import { expect, test } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-000000000801";
const patientSession = {
  visit_id: visitId,
  patient_access_token: "synthetic-patient-token",
  consent: {
    language: "en",
    version: "synthetic-consent-v1",
    sha256: "a".repeat(64),
    text: "Synthetic consent text.",
  },
  stage: "interview",
};

async function seedInterview(page: import("@playwright/test").Page) {
  await page.addInitScript((session) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(session));
  }, patientSession);
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({
      json: {
        question: {
          id: "Q_CC",
          input: "text",
          optional: false,
          prompt: "What would you like to tell the doctor?",
        },
        answered: [],
      },
    }),
  );
  await page.route("**/intake/sessions/*/interview/answers", (route) =>
    route.fulfill({
      json: {
        question: {
          id: "Q_DURATION",
          input: "text",
          optional: true,
          prompt: "How long has this been happening?",
        },
        answered: [],
      },
    }),
  );
}

test("streams fake PCM, shows the transcript, and permits editing", async ({
  page,
}) => {
  await seedInterview(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
      },
    });
    class FakeAudioContext {
      state = "running";
      destination = {};
      audioWorklet = { addModule: async () => undefined };
      createMediaStreamSource() {
        return { connect() {}, disconnect() {} };
      }
      createGain() {
        return { gain: { value: 1 }, connect() {}, disconnect() {} };
      }
      async resume() {}
      async close() {
        this.state = "closed";
      }
    }
    class FakeAudioWorkletNode {
      port = {
        onmessage: null as ((event: MessageEvent) => void) | null,
        close() {},
      };
      connect() {}
      disconnect() {}
      constructor() {
        window.setTimeout(() => {
          this.port.onmessage?.({ data: { type: "speech" } } as MessageEvent);
          this.port.onmessage?.({
            data: { type: "frame", data: new Int16Array(1600).buffer },
          } as MessageEvent);
        }, 20);
      }
    }
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      value: FakeAudioContext,
    });
    Object.defineProperty(window, "AudioWorkletNode", {
      configurable: true,
      value: FakeAudioWorkletNode,
    });
  });

  let audioSocket: import("@playwright/test").WebSocketRoute | undefined;
  await page.routeWebSocket(/\/ws\/intake\/.+\/audio/, (socket) => {
    audioSocket = socket;
    socket.onMessage((data) => {
      if (typeof data !== "string") {
        socket.send(
          JSON.stringify({ type: "partial", text: "Synthetic spoken answer" }),
        );
        return;
      }
      const message = JSON.parse(data) as { type?: string };
      if (message.type === "auth")
        socket.send(JSON.stringify({ type: "ready" }));
      if (message.type === "end") {
        socket.send(
          JSON.stringify({
            type: "final",
            text: "Synthetic spoken answer",
            confidence: 0.9,
          }),
        );
      }
    });
  });

  await page.goto("/kiosk/intake");
  await expect(
    page.getByText("What would you like to tell the doctor?"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Speak your answer" }).click();
  const answer = page.getByRole("textbox", { name: "Type your answer" });
  await expect(answer).toHaveValue("Synthetic spoken answer");
  await expect(page.getByText("Listening. Speak now.")).toBeVisible();
  await expect.poll(() => audioSocket).toBeDefined();

  await page.getByRole("button", { name: "Stop recording" }).click();
  await expect(answer).toHaveValue("Synthetic spoken answer");
  await expect(
    page.getByRole("button", { name: "Speak your answer" }),
  ).toBeEnabled();
  await answer.fill("Edited synthetic answer");
  await expect(answer).toHaveValue("Edited synthetic answer");
});

test("permission denial leaves the typed answer path available", async ({
  page,
}) => {
  await seedInterview(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException("Permission denied", "NotAllowedError");
        },
      },
    });
  });
  await page.goto("/kiosk/intake");
  await page.getByRole("button", { name: "Speak your answer" }).click();
  await expect(
    page.getByText(
      "Microphone access was denied. You can type or tap your answer.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Type your answer" }),
  ).toBeEnabled();
});
