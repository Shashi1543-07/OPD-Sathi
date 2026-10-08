import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import enCommon from "./locales/en/common.json";
import enIntake from "./locales/en/intake.json";
import enKiosk from "./locales/en/kiosk.json";
import enStaff from "./locales/en/staff.json";
import enStatus from "./locales/en/status.json";
import hiCommon from "./locales/hi/common.json";
import hiIntake from "./locales/hi/intake.json";
import hiKiosk from "./locales/hi/kiosk.json";
import hiStaff from "./locales/hi/staff.json";
import hiStatus from "./locales/hi/status.json";
import mrCommon from "./locales/mr/common.json";
import mrIntake from "./locales/mr/intake.json";
import mrKiosk from "./locales/mr/kiosk.json";
import mrStaff from "./locales/mr/staff.json";
import mrStatus from "./locales/mr/status.json";

export type Language = "en" | "hi" | "mr" | "xa";
export const namespaces = [
  "common",
  "kiosk",
  "intake",
  "staff",
  "status",
] as const;

function pseudoText(value: string): string {
  return value
    .split(/(\{\{[^}]+\}\})/g)
    .map((part) => {
      if (part.startsWith("{{") && part.endsWith("}}")) return part;
      const chars = Array.from(part);
      let visibleCharacters = 0;
      const expanded = chars
        .map((char) => {
          if (!char.trim()) return char;
          visibleCharacters += 1;
          return `${char}${visibleCharacters % 2 === 0 ? "·" : ""}`;
        })
        .join("");
      return `⟦${expanded}⟧`;
    })
    .join("");
}

function pseudoResource<T extends Record<string, unknown>>(source: T): T {
  return Object.fromEntries(
    Object.entries(source).map(([key, value]) => [
      key,
      typeof value === "string" ? pseudoText(value) : value,
    ]),
  ) as T;
}

const pseudo = {
  common: pseudoResource(enCommon),
  kiosk: pseudoResource(enKiosk),
  intake: pseudoResource(enIntake),
  staff: pseudoResource(enStaff),
  status: pseudoResource(enStatus),
};
const queryLanguage = new URLSearchParams(window.location.search).get("lang");
const initialLanguage: Language =
  queryLanguage === "xa" || queryLanguage === "hi" || queryLanguage === "mr"
    ? queryLanguage
    : "en";

void i18n.use(initReactI18next).init({
  resources: {
    en: {
      common: enCommon,
      kiosk: enKiosk,
      intake: enIntake,
      staff: enStaff,
      status: enStatus,
    },
    hi: {
      common: hiCommon,
      kiosk: hiKiosk,
      intake: hiIntake,
      staff: hiStaff,
      status: hiStatus,
    },
    mr: {
      common: mrCommon,
      kiosk: mrKiosk,
      intake: mrIntake,
      staff: mrStaff,
      status: mrStatus,
    },
    xa: pseudo,
  },
  lng: initialLanguage,
  fallbackLng: "en",
  defaultNS: "common",
  ns: [...namespaces],
  interpolation: { escapeValue: false },
  returnNull: false,
});

const syncDocumentLanguage = (language: string) => {
  if (typeof document !== "undefined") {
    document.documentElement.lang = language === "xa" ? "en" : language;
  }
};
i18n.on("languageChanged", syncDocumentLanguage);
syncDocumentLanguage(i18n.language);

export default i18n;
