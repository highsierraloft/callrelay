import { useEffect, useRef, useState } from "react";
import { Play, Square } from "lucide-react";
import { message } from "./api";

let releaseCurrent: (() => void) | undefined;
export function stopVoicePreview() {
  releaseCurrent?.();
}

export default function VoicePreview({
  voice,
  model,
}: {
  voice: string;
  model: string;
}) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [error, setError] = useState("");
  const release = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    setError("");
    return () => release.current?.();
  }, [voice, model]);
  async function play() {
    stopVoicePreview();
    setError("");
    setState("loading");
    const controller = new AbortController();
    const audio = new Audio();
    let url: string | undefined;
    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      controller.abort();
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      if (url) URL.revokeObjectURL(url);
      if (releaseCurrent === stop) releaseCurrent = undefined;
      setState("idle");
    };
    releaseCurrent = stop;
    release.current = stop;
    audio.onended = stop;
    audio.onerror = () => {
      if (!stopped) {
        setError("This sample could not be played. Try again.");
        stop();
      }
    };
    try {
      const response = await fetch(
        "/admin/api/voice-preview?" + new URLSearchParams({ voice, model }),
        { credentials: "same-origin", signal: controller.signal },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : "Sample unavailable. Try again later.",
        );
      }
      const blob = await response.blob();
      if (stopped) return;
      url = URL.createObjectURL(blob);
      audio.src = url;
      await audio.play();
      if (!stopped) setState("playing");
    } catch (e) {
      if (!stopped) {
        setError(message(e));
        stop();
      }
    }
  }
  return (
    <div className="voice-preview">
      <button
        type="button"
        onClick={state === "idle" ? play : () => release.current?.()}
      >
        {state === "idle" ? <Play size={16} /> : <Square size={16} />}
        {state === "idle"
          ? "Play Sample"
          : state === "loading"
            ? "Cancel Sample"
            : "Stop Sample"}
      </button>
      <p className="muted caption">
        Free provider sample; task, personality, and expression aren’t applied.
      </p>
      {state === "loading" && <p role="status">Loading sample…</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </div>
  );
}
