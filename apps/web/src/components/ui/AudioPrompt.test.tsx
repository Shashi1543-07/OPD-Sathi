import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { AudioPrompt } from "./AudioPrompt";

const voice = vi.hoisted(() => ({
  speak: vi.fn<(text: string, language: string) => Promise<boolean>>(),
  stopSpeaking: vi.fn(),
}));
vi.mock("../../lib/voice", () => voice);

function renderPrompt(text = "Welcome", language = "en") {
  return render(
    <I18nextProvider i18n={i18n}>
      <AudioPrompt language={language} promptId="ui.welcome" text={text} variant="tool" />
    </I18nextProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  voice.speak.mockReset();
  voice.stopSpeaking.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("the read-aloud button", () => {
  it("shows that it is speaking, and leaves no text behind when it ends", async () => {
    let finish: (spoken: boolean) => void = () => undefined;
    voice.speak.mockReturnValue(new Promise<boolean>((resolve) => (finish = resolve)));
    renderPrompt();
    const button = screen.getByRole("button", { name: "Read aloud" });
    expect(button).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(button);
    expect(voice.speak).toHaveBeenCalledWith("Welcome", "en");
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button).toHaveClass("is-playing");
    expect(button.querySelector(".sound-bars")).not.toBeNull();

    await act(async () => finish(true));
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button.querySelector(".sound-bars")).toBeNull();
    // The old "Read-aloud finished." stayed beside the button for good.
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("stops on a second tap, and a read that ends late changes nothing", async () => {
    let finish: (spoken: boolean) => void = () => undefined;
    voice.speak.mockReturnValue(new Promise<boolean>((resolve) => (finish = resolve)));
    renderPrompt();
    const button = screen.getByRole("button", { name: "Read aloud" });
    await userEvent.click(button);
    await userEvent.click(button);
    expect(voice.stopSpeaking).toHaveBeenCalledTimes(1);
    expect(button).toHaveAttribute("aria-pressed", "false");

    await act(async () => finish(false));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says once that the device has no voice, then takes the notice away", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    voice.speak.mockResolvedValue(false);
    renderPrompt();
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Read-aloud isn't available on this device.",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("stops reading when the text changes language", async () => {
    voice.speak.mockReturnValue(new Promise<boolean>(() => undefined));
    const view = renderPrompt();
    const button = screen.getByRole("button", { name: "Read aloud" });
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");

    view.rerender(
      <I18nextProvider i18n={i18n}>
        <AudioPrompt language="hi" promptId="ui.welcome" text="स्वागत है" variant="tool" />
      </I18nextProvider>,
    );
    expect(voice.stopSpeaking).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Read aloud" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
});
