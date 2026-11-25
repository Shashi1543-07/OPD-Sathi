import { useEffect, useRef, useState } from "react";
import { Volume2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { speak, stopSpeaking } from "../../lib/voice";

/** How long the "no voice on this device" notice stays on screen. */
const NOTICE_MS = 5_000;

/** Moving bars shown in place of the speaker icon while something is being read aloud. */
export function SoundBars() {
  return (
    <span className="sound-bars" aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}

export function AudioPrompt({
  language,
  promptId,
  text,
  variant = "button",
  tooltipTitle,
  tooltipDesc,
}: {
  language: string;
  promptId: string;
  text: string;
  variant?: "button" | "tool";
  tooltipTitle?: string;
  tooltipDesc?: string;
}) {
  const { t } = useTranslation("common");
  // What is being read: a change of language or text ends "playing" without another render.
  const reading = `${language}\n${text}`;
  const [playingFor, setPlayingFor] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const playing = playingFor === reading;
  // Bumped by every start and stop, so a read that ends late cannot change a newer one.
  const run = useRef(0);
  const active = useRef(false);

  useEffect(
    () => () => {
      // The text changed (another language) or the button left the screen.
      if (!active.current) return;
      active.current = false;
      run.current += 1;
      stopSpeaking();
    },
    [reading],
  );

  useEffect(() => {
    if (!unavailable) return;
    const timer = window.setTimeout(() => setUnavailable(false), NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [unavailable]);

  const toggle = () => {
    run.current += 1;
    if (active.current) {
      active.current = false;
      stopSpeaking();
      setPlayingFor(null);
      return;
    }
    const mine = run.current;
    active.current = true;
    setUnavailable(false);
    setPlayingFor(reading);
    void speak(text, language)
      .catch(() => false)
      .then((spoken) => {
        if (mine !== run.current) return;
        active.current = false;
        setPlayingFor(null);
        setUnavailable(!spoken);
      });
  };

  const label = tooltipTitle ?? t("readAloud");
  const fullTitle = tooltipDesc ? `${label} — ${tooltipDesc}` : label;
  const notice = unavailable && (
    <span className="audio-playback-status" role="status">
      {t("readAloudUnavailable")}
    </span>
  );

  if (variant === "tool") {
    return (
      <div className="kiosk-tool-item">
        <button
          type="button"
          className={`ui-tool-button ${playing ? "is-playing" : ""}`}
          onClick={toggle}
          data-prompt-id={promptId}
          aria-pressed={playing}
          aria-label={label}
          title={fullTitle}
        >
          {playing ? <SoundBars /> : <Volume2 aria-hidden="true" />}
        </button>
        <div className="ui-tooltip" role="tooltip">
          <strong>{label}</strong>
          {tooltipDesc && <span>{tooltipDesc}</span>}
        </div>
        {notice}
      </div>
    );
  }

  return (
    <span className="audio-prompt-control">
      <button
        type="button"
        className={`ui-button ui-button--ghost ${playing ? "is-playing" : ""}`}
        onClick={toggle}
        data-prompt-id={promptId}
        aria-pressed={playing}
      >
        {playing ? <SoundBars /> : <Volume2 aria-hidden="true" />}
        <span>{label}</span>
      </button>
      {notice}
    </span>
  );
}
