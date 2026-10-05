import { ReadAloudButton } from "./index";

export function AudioPrompt({
  language,
  promptId,
}: {
  language: string;
  promptId: string;
}) {
  const play = () => {
    const audio = new Audio(`/audio/${language}/${promptId}.mp3`);
    void audio.play().catch(() => undefined);
  };
  return <ReadAloudButton onClick={play} />;
}
