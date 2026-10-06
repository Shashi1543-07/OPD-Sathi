import { useEffect, useRef, useState } from "react";
import audioManifest from "../lib/promptAudioManifest.json";
import { intake } from "../lib/intake";

type Language = string;

function usePromptAudio() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [volume, setVolume] = useState(0.8);
  const [playing, setPlaying] = useState(false);

  async function play(source: string) {
    try {
      audio.current?.pause();
      audio.current = new Audio(source);
      audio.current.volume = volume;
      audio.current.onended = () => setPlaying(false);
      audio.current.onerror = () => setPlaying(false);
      setPlaying(true);
      await audio.current.play();
    } catch {
      setPlaying(false);
    }
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
          onChange={(event) => {
            const next = Number(event.target.value);
            setVolume(next);
            if (audio.current) audio.current.volume = next;
          }}
        />
      </label>
    );
  }

  return { play, playing, volumeControl };
}

export function AudioPrompt({
  id,
  language,
  label,
  volumeLabel,
}: {
  id: string;
  language: Language;
  label: string;
  volumeLabel: string;
}) {
  const player = usePromptAudio();
  const source = (audioManifest as Record<string, Record<string, string>>)[
    language
  ]?.[id];
  if (!source) return null;
  return (
    <div className="audio-prompt">
      <button
        type="button"
        className="ui-button ui-button--secondary"
        onClick={() => void player.play(source)}
      >
        {label}
      </button>
      {player.volumeControl(volumeLabel)}
      {player.playing && <span className="sr-only" role="status">{label}</span>}
    </div>
  );
}

export function ReadBackAudioButton({
  label,
  volumeLabel,
}: {
  label: string;
  volumeLabel: string;
}) {
  const player = usePromptAudio();
  const objectUrl = useRef<string | null>(null);
  useEffect(
    () => () => {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    },
    [],
  );
  async function readBack() {
    try {
      const audioBlob = await intake.readbackAudio();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(audioBlob);
      await player.play(objectUrl.current);
    } catch {
      // The deterministic text remains visible when dynamic speech is unavailable.
    }
  }
  return (
    <div className="audio-prompt">
      <button
        type="button"
        className="ui-button ui-button--secondary"
        onClick={() => void readBack()}
      >
        {label}
      </button>
      {player.volumeControl(volumeLabel)}
    </div>
  );
}
