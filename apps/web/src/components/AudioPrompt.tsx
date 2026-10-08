import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Language = string;

const speechLanguage: Record<string, string> = {
  en: "en-IN",
  hi: "hi-IN",
  mr: "mr-IN",
};

function useSpeech() {
  const utterance = useRef<SpeechSynthesisUtterance | null>(null);
  const [volume, setVolume] = useState(0.8);
  const [playing, setPlaying] = useState(false);
  const [playbackMessage, setPlaybackMessage] = useState("");
  const { t } = useTranslation("common");

  useEffect(
    () => () => {
      if (utterance.current) window.speechSynthesis?.cancel();
    },
    [],
  );

  function speak(text: string, language: Language) {
    if (!text.trim()) return;
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      setPlaybackMessage(t("readAloudUnavailable"));
      return;
    }
    window.speechSynthesis.cancel();
    const next = new SpeechSynthesisUtterance(text);
    next.lang = speechLanguage[language] ?? language;
    next.volume = volume;
    next.onstart = () => {
      setPlaying(true);
      setPlaybackMessage(t("readingAloud"));
    };
    next.onend = () => {
      setPlaying(false);
      setPlaybackMessage(t("readAloudFinished"));
    };
    next.onerror = () => {
      setPlaying(false);
      setPlaybackMessage(t("readAloudUnavailable"));
    };
    utterance.current = next;
    setPlaybackMessage(t("readingAloud"));
    window.speechSynthesis.speak(next);
  }

  function volumeControl(label: string) {
    return (
      <label className="audio-volume">
        <span>{label}</span>
        <input
          aria-label={label}
          type="range"
          min="0"
          max="1"
          step="0.1"
          value={volume}
          onChange={(event) => setVolume(Number(event.target.value))}
        />
      </label>
    );
  }

  return { speak, playing, volumeControl, playbackMessage };
}

export function AudioPrompt({
  id: _id,
  language,
  label,
  volumeLabel,
  text,
}: {
  id: string;
  language: Language;
  label: string;
  volumeLabel: string;
  text: string;
}) {
  const player = useSpeech();
  return (
    <div className="audio-prompt">
      <button
        type="button"
        className="ui-button ui-button--secondary"
        onClick={() => player.speak(text, language)}
      >
        {label}
      </button>
      {player.volumeControl(volumeLabel)}
      {player.playing && (
        <span className="sr-only" role="status">
          {label}
        </span>
      )}
      {player.playbackMessage && (
        <span className="audio-playback-status" role="status">
          {player.playbackMessage}
        </span>
      )}
    </div>
  );
}

export function ReadBackAudioButton({
  label,
  volumeLabel,
  text,
  language,
}: {
  label: string;
  volumeLabel: string;
  text: string;
  language: Language;
}) {
  const player = useSpeech();
  return (
    <div className="audio-prompt">
      <button
        type="button"
        className="ui-button ui-button--secondary"
        onClick={() => player.speak(text, language)}
      >
        {label}
      </button>
      {player.volumeControl(volumeLabel)}
      {player.playing && (
        <span className="sr-only" role="status">
          {label}
        </span>
      )}
      {player.playbackMessage && (
        <span className="audio-playback-status" role="status">
          {player.playbackMessage}
        </span>
      )}
    </div>
  );
}
