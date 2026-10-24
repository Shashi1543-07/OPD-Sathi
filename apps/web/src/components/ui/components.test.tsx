import { cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { useLanguageStore } from "../../languageStore";
import {
  Button,
  ChipGroup,
  ConfirmDialog,
  DangerTile,
  FacesScale,
  LanguageSelector,
  NumberPad,
  ProgressDots,
} from "./index";

function renderWithI18n(ui: React.ReactNode) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

afterEach(() => {
  cleanup();
  useLanguageStore.getState().setLanguage("en");
});

describe("shared UI components", () => {
  it("disables and announces a busy button", () => {
    renderWithI18n(<Button busy>Save</Button>);
    expect(
      screen.getByRole("button", { name: /save working/i }),
    ).toBeDisabled();
    expect(screen.getByRole("button")).toHaveAttribute("aria-busy", "true");
  });

  it("toggles a danger tile with keyboard activation", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    renderWithI18n(<DangerTile label="Example warning" onClick={onClick} />);
    const tile = screen.getByRole("button", { name: /example warning/i });
    tile.focus();
    await user.keyboard("{Enter}");
    expect(tile).toHaveFocus();
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("supports multiselect chips and reports pressed state", async () => {
    const user = userEvent.setup();
    function Example() {
      const [value, setValue] = useState<string[]>([]);
      return (
        <ChipGroup
          options={["One", "Two"]}
          value={value}
          onChange={setValue}
          label="Options"
          multiple
        />
      );
    }
    renderWithI18n(<Example />);
    const one = screen.getByRole("button", { name: "One" });
    await user.click(one);
    expect(one).toHaveAttribute("aria-pressed", "true");
  });

  it("limits number pad input and deletes the final digit", async () => {
    const user = userEvent.setup();
    function Example() {
      const [value, setValue] = useState("1");
      return (
        <>
          <output>{value}</output>
          <NumberPad value={value} onChange={setValue} maxLength={2} />
        </>
      );
    }
    renderWithI18n(<Example />);
    await user.click(screen.getByRole("button", { name: "2" }));
    await user.click(screen.getByRole("button", { name: "3" }));
    expect(screen.getByText("12", { selector: "output" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete last digit" }));
    expect(screen.getByText("1", { selector: "output" })).toBeInTheDocument();
  });

  it("exposes current progress accessibly", () => {
    renderWithI18n(<ProgressDots current={3} total={8} />);
    expect(
      screen.getByRole("progressbar", { name: "Step 3 of 8" }),
    ).toHaveAttribute("aria-valuenow", "3");
  });

  it("supports keyboard selection on the faces scale", async () => {
    const user = userEvent.setup();
    function Example() {
      const [value, setValue] = useState<number>();
      return <FacesScale value={value} onChange={setValue} />;
    }
    renderWithI18n(<Example />);
    const option = screen.getByRole("button", { name: "7" });
    option.focus();
    await user.keyboard("{Enter}");
    expect(option).toHaveAttribute("aria-pressed", "true");
  });

  it("traps dialog focus, closes with Escape, and restores focus", async () => {
    const user = userEvent.setup();
    function Example() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open dialog</button>
          <ConfirmDialog
            open={open}
            title="Confirm sample"
            onConfirm={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          />
        </>
      );
    }
    renderWithI18n(<Example />);
    const opener = screen.getByRole("button", { name: "Open dialog" });
    await user.click(opener);
    const cancel = screen.getByRole("button", { name: "Cancel" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(cancel).toHaveFocus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(confirm).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(opener).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("changes the session language from the language tiles", async () => {
    const user = userEvent.setup();
    renderWithI18n(<LanguageSelector />);
    // The tile's name is everything it shows, script and English name alike.
    await user.click(screen.getByRole("button", { name: "मराठी Marathi" }));
    expect(useLanguageStore.getState().language).toBe("mr");
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
    expect(i18n.language).toBe("mr");
  });

  it("expands the English copy in the xa pseudo-locale and keeps values intact", () => {
    const english = i18n.getFixedT("en", "common")("offlineMessage");
    const pseudo = i18n.getFixedT("xa", "common")("offlineMessage");
    expect(pseudo.length).toBeGreaterThan(english.length * 1.35);
    expect(
      i18n.getFixedT("xa", "common")("selectedValue", { value: "7" }),
    ).toContain("7");
  });
});
