import { useState } from "react";
import { Volume2 } from "lucide-react";
import { useTranslation } from "react-i18next";

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
  const [playbackMessage, setPlaybackMessage] = useState("");
  const play = () => {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      setPlaybackMessage(t("readAloudUnavailable"));
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang =
      ({ en: "en-IN", hi: "hi-IN", mr: "mr-IN" } as Record<string, string>)[
        language
      ] ?? language;
    utterance.onstart = () => setPlaybackMessage(t("readingAloud"));
    utterance.onend = () => setPlaybackMessage(t("readAloudFinished"));
    utterance.onerror = () => setPlaybackMessage(t("readAloudUnavailable"));
    setPlaybackMessage(t("readingAloud"));
    window.speechSynthesis.speak(utterance);
  };

  const label = tooltipTitle ?? t("readAloud");
  const fullTitle = tooltipDesc ? `${label} — ${tooltipDesc}` : label;

  if (variant === "tool") {
    return (
      <div className="kiosk-tool-item">
        <button
          type="button"
          className="ui-tool-button"
          onClick={play}
          data-prompt-id={promptId}
          aria-label={label}
          title={fullTitle}
        >
          <Volume2 aria-hidden="true" />
        </button>
        <div className="ui-tooltip" role="tooltip">
          <strong>{label}</strong>
          {tooltipDesc && <span>{tooltipDesc}</span>}
        </div>
        {playbackMessage && (
          <span className="audio-playback-status" role="status">
            {playbackMessage}
          </span>
        )}
      </div>
    );
  }

  return (
    <span className="audio-prompt-control">
      <button
        type="button"
        className="ui-button ui-button--ghost"
        onClick={play}
        data-prompt-id={promptId}
      >
        <Volume2 aria-hidden="true" />
        <span>{label}</span>
      </button>
      {playbackMessage && (
        <span className="audio-playback-status" role="status">
          {playbackMessage}
        </span>
      )}
    </span>
  );
}
