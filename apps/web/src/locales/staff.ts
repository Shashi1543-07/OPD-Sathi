/**
 * Staff-screen wording, registered when a staff screen's code loads. It is the largest set of
 * strings and the kiosk and patient pages never show it, so it stays out of their first download.
 * Every module that uses the "staff" namespace imports this file.
 */
import i18n, { pseudoResource } from "../i18n";
import en from "./en/staff.json";
import hi from "./hi/staff.json";
import mr from "./mr/staff.json";

const bundles: Record<string, Record<string, unknown>> = {
  en,
  hi,
  mr,
  xa: pseudoResource(en),
};
for (const [language, bundle] of Object.entries(bundles)) {
  if (!i18n.hasResourceBundle(language, "staff")) {
    i18n.addResourceBundle(language, "staff", bundle);
  }
}
