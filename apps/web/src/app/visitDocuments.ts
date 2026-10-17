import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";

export type VisitDocuments = components["schemas"]["VisitDocumentsResponse"];
export type StaffDocument = components["schemas"]["StaffDocument"];

/** While AI reads a paper (up to 25 seconds), look again every few seconds for a while. */
const POLL_MS = 3000;
const MAX_POLLS = 40;

/** The visit's papers, refreshed while AI is still reading one; a failure never blocks the sheet. */
export function useVisitDocuments(visitId: string | null) {
  const [state, setState] = useState<{ visitId: string; data: VisitDocuments }>();
  const [failed, setFailed] = useState(false);
  const polls = useRef(0);
  const load = useCallback(async () => {
    if (!visitId) return;
    try {
      const response = await staffAuth.request(`/visits/${visitId}/documents`);
      if (!response.ok) throw new Error("papers unavailable");
      const data = (await response.json()) as VisitDocuments;
      if (!Array.isArray(data.documents) || !Array.isArray(data.earlier)) {
        throw new Error("unexpected papers");
      }
      setState({ visitId, data });
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [visitId]);
  useEffect(() => {
    polls.current = 0;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  const data = state?.visitId === visitId ? state.data : undefined;
  const reading = Boolean(data?.documents.some((paper) => paper.reading_status === "pending"));
  useEffect(() => {
    if (!reading || polls.current >= MAX_POLLS) return;
    const timer = window.setTimeout(() => {
      polls.current += 1;
      void load();
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [reading, data, load]);
  return { data, failed, reload: load };
}
