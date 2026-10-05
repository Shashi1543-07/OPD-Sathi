import { create } from "zustand";
import i18n, { type Language } from "./i18n";

type LanguageState = {
  language: Language;
  setLanguage: (language: Language) => void;
};

export const useLanguageStore = create<LanguageState>((set) => ({
  language: i18n.language as Language,
  setLanguage: (language) => {
    void i18n.changeLanguage(language);
    set({ language });
  },
}));
