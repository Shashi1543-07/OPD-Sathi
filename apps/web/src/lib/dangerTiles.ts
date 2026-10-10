/** Danger-sign tiles are stored by code; staff screens show what the patient tapped. */
export const DANGER_TILE_LABELS: Record<string, string> = {
  DS_CHEST: "Chest discomfort",
  DS_BREATH: "Trouble breathing",
  DS_BLEED: "Heavy bleeding",
  DS_FIT: "Fainting or a seizure",
  DS_STROKE: "Sudden weakness or difficulty speaking",
};

/** Replace any tile code inside a sentence with its label. */
export function withTileLabels(text: string): string {
  return text.replace(/\bDS_[A-Z]+\b/g, (code) => DANGER_TILE_LABELS[code] ?? code);
}
