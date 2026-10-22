import { FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { Button, Toast } from "../components/ui";
import { staffAuth, type AuthResponse } from "../lib/auth";
import { accountRequest } from "../lib/account";
import { MfaEnrollment, PasswordChangeForm, RecoveryCodes } from "./AccountForms";

type Me = {
  name: string;
  email: string;
  role: string;
  hospital_name: string | null;
  mfa_enabled: boolean;
  mfa_required: boolean;
  recovery_codes_left: number;
  password_changed_at: string | null;
};
type Session = {
  id: string;
  device: string;
  signed_in_at: string;
  last_used_at: string;
  current: boolean;
};

/** The signed-in person's own account: password, two-step sign-in and where they are signed in. */
export function MyAccount() {
  const { t, i18n } = useTranslation("staff");
  const [me, setMe] = useState<Me | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"idle" | "password" | "enrol" | "codes" | "disable">("idle");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [profile, list] = await Promise.all([
        accountRequest<Me>("/auth/me", undefined, "GET"),
        accountRequest<{ items: Session[] }>("/auth/sessions", undefined, "GET"),
      ]);
      setMe(profile);
      setSessions(list.items);
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    }
  }, [t]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  const when = (value: string | null) =>
    value
      ? new Intl.DateTimeFormat(i18n.language === "en" ? "en-IN" : i18n.language, {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(value))
      : t("never");

  async function withCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "codes") {
        const result = await accountRequest<{ recovery_codes: string[] }>(
          "/auth/mfa/recovery-codes",
          { code: code.replace(/\s/g, "") },
        );
        setCodes(result.recovery_codes);
      } else {
        staffAuth.adopt(
          await accountRequest<AuthResponse>("/auth/mfa/disable", { code: code.replace(/\s/g, "") }),
        );
        setNotice(t("mfaTurnedOff"));
        setMode("idle");
      }
      setCode("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    } finally {
      setBusy(false);
    }
  }

  async function endSession(session: Session) {
    setError("");
    try {
      await accountRequest(`/auth/sessions/${session.id}`, undefined, "DELETE");
      if (session.current) {
        await staffAuth.logout();
        return;
      }
      setNotice(t("sessionEnded"));
      await load();
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    }
  }

  async function endOthers() {
    setError("");
    try {
      const result = await accountRequest<{ ended: number }>("/auth/sessions/end-others");
      setNotice(t("otherSessionsEnded", { count: result.ended }));
      await load();
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    }
  }

  if (!me) {
    return <p role="status">{error || t("loadingAdminRecords")}</p>;
  }
  const others = sessions.filter((session) => !session.current).length;

  return (
    <section className="acct" aria-labelledby="acct-title">
      <h2 id="acct-title">{t("myAccount")}</h2>
      <p className="acct-who">
        {me.name} · {me.email}
        {me.hospital_name ? ` · ${me.hospital_name}` : ""}
      </p>
      <div aria-live="polite">
        {notice && <Toast tone="success">{notice}</Toast>}
        {error && <Toast tone="error">{error}</Toast>}
      </div>

      <section className="acct-card" aria-labelledby="acct-password">
        <h3 id="acct-password">{t("passwordSection")}</h3>
        <p>{t("passwordLastChanged", { when: when(me.password_changed_at) })}</p>
        {mode === "password" ? (
          <PasswordChangeForm
            onDone={() => {
              setMode("idle");
              setNotice(t("passwordChanged"));
              void load();
            }}
          />
        ) : (
          <Button variant="secondary" type="button" onClick={() => setMode("password")}>
            {t("changePassword")}
          </Button>
        )}
      </section>

      <section className="acct-card" aria-labelledby="acct-mfa">
        <h3 id="acct-mfa">{t("mfaSection")}</h3>
        <p>
          {me.mfa_enabled
            ? t("mfaOnStatus", { count: me.recovery_codes_left })
            : me.mfa_required
              ? t("mfaRequiredStatus")
              : t("mfaOffStatus")}
        </p>
        {codes ? (
          <RecoveryCodes
            codes={codes}
            onDone={() => {
              setCodes(null);
              setMode("idle");
            }}
          />
        ) : mode === "enrol" ? (
          <MfaEnrollment
            onDone={() => {
              setMode("idle");
              setNotice(t("mfaTurnedOn"));
              void load();
            }}
          />
        ) : mode === "codes" || mode === "disable" ? (
          <form className="acct-form" onSubmit={withCode}>
            <label htmlFor="acct-confirm-code">{t("mfaCode")}</label>
            <input
              id="acct-confirm-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
            <div className="acct-actions">
              <Button type="submit" busy={busy}>
                {mode === "codes" ? t("newRecoveryCodes") : t("turnOffMfa")}
              </Button>
              <Button variant="secondary" type="button" onClick={() => setMode("idle")}>
                {t("cancelEdit")}
              </Button>
            </div>
          </form>
        ) : me.mfa_enabled ? (
          <div className="acct-actions">
            <Button variant="secondary" type="button" onClick={() => setMode("codes")}>
              {t("newRecoveryCodes")}
            </Button>
            {!me.mfa_required && (
              <Button variant="secondary" type="button" onClick={() => setMode("disable")}>
                {t("turnOffMfa")}
              </Button>
            )}
          </div>
        ) : (
          <Button type="button" onClick={() => setMode("enrol")}>
            {t("mfaStart")}
          </Button>
        )}
      </section>

      <section className="acct-card" aria-labelledby="acct-sessions">
        <h3 id="acct-sessions">{t("sessionsSection")}</h3>
        <ul className="acct-sessions">
          {sessions.map((session) => (
            <li key={session.id}>
              <span>
                <strong>{session.device}</strong>
                {session.current && <span className="adm-you">{t("thisDevice")}</span>}
                <small>
                  {t("sessionTimes", {
                    signedIn: when(session.signed_in_at),
                    lastUsed: when(session.last_used_at),
                  })}
                </small>
              </span>
              <Button variant="secondary" size="sm" type="button" onClick={() => void endSession(session)}>
                {session.current ? t("signOut") : t("endSession")}
              </Button>
            </li>
          ))}
        </ul>
        {others > 0 && (
          <Button variant="secondary" type="button" onClick={() => void endOthers()}>
            {t("endOtherSessions")}
          </Button>
        )}
      </section>
    </section>
  );
}
