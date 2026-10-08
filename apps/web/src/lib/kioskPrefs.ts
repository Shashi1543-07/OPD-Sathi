import { create } from "zustand";

const key = "osa.kiosk.voiceGuidance";

function initialGuidance() {
  try {
    return localStorage.getItem(key) !== "off";
  } catch {
    return true;
  }
}

type KioskPrefs = {
  /** Read each question aloud automatically. */
  voiceGuidance: boolean;
  setVoiceGuidance: (on: boolean) => void;
};

export const useKioskPrefs = create<KioskPrefs>((set) => ({
  voiceGuidance: initialGuidance(),
  setVoiceGuidance: (on) => {
    try {
      localStorage.setItem(key, on ? "on" : "off");
    } catch {
      // Preference only; ignore storage failures.
    }
    set({ voiceGuidance: on });
  },
}));
