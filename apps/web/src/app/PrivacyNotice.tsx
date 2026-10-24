import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import type { Language } from "../i18n";
import { useLanguageStore } from "../languageStore";
import { apiOrigin } from "../lib/api";

type Notice = {
  hospital_name: string;
  grievance_contact: string | null;
  notice: {
    language: Language;
    version: string;
    review_status: string;
    text: string;
  };
};

const LANGUAGES: { code: Language; label: string }[] = [
  { code: "hi", label: "हिन्दी" },
  { code: "mr", label: "मराठी" },
  { code: "en", label: "English" },
];

/** The notice's own small format: "## " headings, "- " lists, and paragraphs between. */
function noticeBlocks(text: string): ReactNode[] {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length > 0) {
      blocks.push(
        <ul key={`list-${blocks.length}`}>
          {list.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const line of text.split("\n").map((value) => value.trim())) {
    if (line.startsWith("- ")) {
      list.push(line.slice(2));
      continue;
    }
    flush();
    if (line.startsWith("## ")) blocks.push(<h2 key={`h-${blocks.length}`}>{line.slice(3)}</h2>);
    else if (line) blocks.push(<p key={`p-${blocks.length}`}>{line}</p>);
  }
  flush();
  return blocks;
}

/**
 * A hospital's full privacy notice, for patients: the waiting-area poster's QR, the token
 * page and phone check-in link here. No sign-in, and nothing about any patient.
 */
export function PrivacyNotice() {
  const { hospitalId = "" } = useParams<{ hospitalId: string }>();
  const { t } = useTranslation(["common", "status"]);
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const [notice, setNotice] = useState<Notice>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    fetch(
      `${apiOrigin}/privacy-notice/${encodeURIComponent(hospitalId)}?language=${encodeURIComponent(language)}`,
    )
      .then(async (response) => {
        if (!response.ok) throw new Error("privacy notice unavailable");
        const body = (await response.json()) as Notice;
        if (active) {
          setNotice(body);
          setFailed(false);
        }
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [hospitalId, language]);

  useEffect(() => {
    document.title = t("privacyTitle", { ns: "status" });
  }, [t]);

  return (
    <main className="status-page privacy-page">
      <article className="status-card">
        <header className="status-head">
          <div>
            <p className="eyebrow">{t("appName", { ns: "common" })}</p>
            {notice && <p className="status-hospital">{notice.hospital_name}</p>}
          </div>
          <div className="status-lang" role="group" aria-label={t("language", { ns: "status" })}>
            {LANGUAGES.map((item) => (
              <button
                type="button"
                key={item.code}
                lang={item.code}
                aria-pressed={language === item.code}
                onClick={() => setLanguage(item.code)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </header>
        <h1>
          <ShieldCheck aria-hidden="true" size={26} /> {t("privacyTitle", { ns: "status" })}
        </h1>
        {failed ? (
          <p role="alert">{t("privacyUnavailable", { ns: "status" })}</p>
        ) : !notice ? (
          <p role="status">{t("loading", { ns: "common" })}…</p>
        ) : (
          <>
            {notice.notice.review_status !== "approved" && (
              <p className="privacy-draft" role="note">
                {t("privacyDraft", { ns: "status" })}
              </p>
            )}
            <div className="privacy-text" lang={notice.notice.language}>
              {noticeBlocks(notice.notice.text)}
            </div>
            <section className="privacy-contact" aria-labelledby="privacy-contact-title">
              <h2 id="privacy-contact-title">{t("privacyContactTitle", { ns: "status" })}</h2>
              <p>{notice.grievance_contact ?? t("privacyContactMissing", { ns: "status" })}</p>
            </section>
            <p className="status-foot">
              {t("privacyVersion", { ns: "status", version: notice.notice.version })}
            </p>
          </>
        )}
      </article>
    </main>
  );
}
