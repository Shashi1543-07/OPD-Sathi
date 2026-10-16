import { useState } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { useLanguageStore } from "../languageStore";
import "./gallery.css";
import {
  Button,
  ChoiceButton,
  ChipGroup,
  ConfidenceChip,
  ConnectionIndicator,
  DangerTile,
  EmptyState,
  ErrorState,
  FacesScale,
  HelpButton,
  IdleWarningDialog,
  LanguageSelector,
  NumberPad,
  OfflineBanner,
  ProgressDots,
  ReadAloudButton,
  Skeleton,
  StatCard,
  Toast,
  UrgencyBadge,
} from "../components/ui";

export function ComponentGallery() {
  const { t } = useTranslation();
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const [selected, setSelected] = useState<string[]>([]);
  const [number, setNumber] = useState("12");
  const [face, setFace] = useState(4);
  const [danger, setDanger] = useState(false);
  const [dialog, setDialog] = useState(false);

  return (
    <main className="gallery-page">
      <header className="gallery-header">
        <div>
          <p className="eyebrow">OPD Saathi</p>
          <h1>{t("galleryTitle")}</h1>
          <p>{t("gallerySubtitle")}</p>
        </div>
        <div
          className="gallery-language-controls"
          role="group"
          aria-label={t("chooseLanguage", { ns: "kiosk" })}
        >
          {(["en", "hi", "mr", "xa"] as const).map((code) => (
            <Button
              key={code}
              size="sm"
              variant={language === code ? "primary" : "secondary"}
              onClick={() => setLanguage(code)}
            >
              {code.toUpperCase()}
            </Button>
          ))}
        </div>
      </header>
      <section className="gallery-section">
        <h2>{t("gallerySectionLanguages")}</h2>
        <LanguageSelector />
      </section>
      <section className="gallery-section">
        <h2>{t("gallerySectionButtons")}</h2>
        <div className="gallery-row">
          <Button>{t("primary")}</Button>
          <Button variant="secondary">{t("secondary")}</Button>
          <Button variant="danger">{t("danger")}</Button>
          <Button variant="ghost">{t("ghost")}</Button>
          <Button busy>{t("busy")}</Button>
          <Button disabled>{t("disabled")}</Button>
          <HelpButton />
          <ReadAloudButton />
        </div>
        <div className="gallery-row">
          <ChoiceButton selected>{t("selectedChoice")}</ChoiceButton>
          <ChoiceButton>{t("unselectedChoice")}</ChoiceButton>
          <DangerTile
            label={t("exampleWarning")}
            selected={danger}
            onClick={() => setDanger(!danger)}
          />
        </div>
      </section>
      <section className="gallery-section">
        <h2>{t("gallerySectionSelection")}</h2>
        <ChipGroup
          options={[
            t("choiceOne", { ns: "kiosk" }),
            t("choiceTwo", { ns: "kiosk" }),
            t("choiceThree", { ns: "kiosk" }),
          ]}
          value={selected}
          onChange={setSelected}
          label={t("sampleChoices")}
          multiple
        />
        <p>{t("selectedValue", { value: selected.join(", ") || "—" })}</p>
        <NumberPad value={number} onChange={setNumber} maxLength={4} />
        <FacesScale value={face} onChange={setFace} />
      </section>
      <section className="gallery-section">
        <h2>{t("gallerySectionIndicators")}</h2>
        <ProgressDots current={4} total={7} />
        <div className="gallery-row">
          <UrgencyBadge level="urgent" />
          <UrgencyBadge level="priority" />
          <UrgencyBadge level="routine" />
          <ConfidenceChip value={0.52} />
          <ConfidenceChip value={0.91} />
          <ConnectionIndicator state="live" />
          <ConnectionIndicator state="reconnecting" />
          <ConnectionIndicator state="offline" />
        </div>
        <div className="gallery-row">
          <StatCard
            label={t("sampleCount")}
            value="24"
            detail={t("sampleUpdated")}
          />
          <StatCard label={t("sampleWait")} value="12 min" />
        </div>
      </section>
      <section className="gallery-section">
        <h2>{t("gallerySectionMessages")}</h2>
        <OfflineBanner />
        <div className="gallery-row">
          <Toast>{t("sampleInformation")}</Toast>
          <Toast tone="success">{t("sampleSuccess")}</Toast>
          <Toast tone="error">{t("sampleError")}</Toast>
        </div>
        <Skeleton style={{ width: 220 }} />
        <EmptyState>{t("sampleDescription")}</EmptyState>
        <ErrorState onRetry={() => undefined}>
          {t("sampleDescription")}
        </ErrorState>
        <Button variant="secondary" onClick={() => setDialog(true)}>
          {t("openIdlePreview")}
        </Button>
        <IdleWarningDialog
          open={dialog}
          onContinue={() => setDialog(false)}
          onEnd={() => setDialog(false)}
        />
      </section>
    </main>
  );
}
