import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { QRCodeSVG } from "qrcode.react";
import "../locales/staff";
import { Button, Toast } from "../components/ui";
import { accountRequest } from "../lib/account";
import { staffAuth, type AuthResponse } from "../lib/auth";

/** Choose a new password: after an administrator's reset, on a new account, or any time. */
export function PasswordChangeForm({ onDone }: { onDone?: () => void }) {
  const { t } = useTranslation("staff");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (next !== repeat) {
      setError(t("passwordsDoNotMatch"));
      return;
    }
    setBusy(true);
    try {
      const session = await accountRequest<AuthResponse>("/auth/password", {
        current_password: current,
        new_password: next,
      });
      setCurrent("");
      setNext("");
      setRepeat("");
      staffAuth.adopt(session);
      onDone?.();
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="acct-form" onSubmit={submit}>
      <label htmlFor="acct-current">{t("currentPassword")}</label>
      <input
        id="acct-current"
        type="password"
        autoComplete="current-password"
        required
        value={current}
        onChange={(event) => setCurrent(event.target.value)}
      />
      <label htmlFor="acct-new">{t("newPassword")}</label>
      <input
        id="acct-new"
        type="password"
        autoComplete="new-password"
        minLength={12}
        required
        aria-describedby="acct-new-hint"
        value={next}
        onChange={(event) => setNext(event.target.value)}
      />
      <small id="acct-new-hint">{t("newPasswordHint")}</small>
      <label htmlFor="acct-repeat">{t("repeatPassword")}</label>
      <input
        id="acct-repeat"
        type="password"
        autoComplete="new-password"
        required
        value={repeat}
        onChange={(event) => setRepeat(event.target.value)}
      />
      {error && (
        <p className="acct-error" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" busy={busy}>
        {t("savePassword")}
      </Button>
    </form>
  );
}

/** Ten one-time recovery codes, shown once, to print or write down. */
export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation("staff");
  const [saved, setSaved] = useState(false);
  return (
    <section className="acct-codes" aria-labelledby="acct-codes-title">
      <h3 id="acct-codes-title">{t("recoveryCodesTitle")}</h3>
      <p>{t("recoveryCodesHint")}</p>
      <ol className="acct-code-list">
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ol>
      <div className="acct-actions">
        <Button variant="secondary" type="button" onClick={() => window.print()}>
          {t("printCodes")}
        </Button>
        <label className="acct-check">
          <input type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} />
          {t("recoveryCodesSaved")}
        </label>
        <Button type="button" disabled={!saved} onClick={onDone}>
          {t("continueLabel")}
        </Button>
      </div>
    </section>
  );
}

/** Set up two-step sign-in: scan the QR code, type the first code, keep the recovery codes. */
export function MfaEnrollment({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation("staff");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [pendingSession, setPendingSession] = useState<AuthResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    setError("");
    try {
      setSetup(await accountRequest("/auth/mfa/setup"));
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await accountRequest<AuthResponse & { recovery_codes: string[] }>(
        "/auth/mfa/enable",
        { code: code.replace(/\s/g, "") },
      );
      setCodes(result.recovery_codes);
      // Use the new session only after the codes are saved: it lifts the set-up screen.
      setPendingSession(result);
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : t("adminRequestError"));
    } finally {
      setBusy(false);
    }
  }

  if (codes) {
    return (
      <RecoveryCodes
        codes={codes}
        onDone={() => {
          if (pendingSession) staffAuth.adopt(pendingSession);
          onDone();
        }}
      />
    );
  }
  if (!setup) {
    return (
      <div className="acct-form">
        <p>{t("mfaIntro")}</p>
        {error && (
          <p className="acct-error" role="alert">
            {error}
          </p>
        )}
        <Button type="button" busy={busy} onClick={() => void start()}>
          {t("mfaStart")}
        </Button>
      </div>
    );
  }
  return (
    <form className="acct-form" onSubmit={confirm}>
      <p>{t("mfaScan")}</p>
      <div className="acct-qr">
        <QRCodeSVG value={setup.otpauth_uri} size={176} marginSize={2} title={t("mfaQrTitle")} />
      </div>
      <p className="acct-secret">
        {t("mfaSecretLabel")} <code>{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
      </p>
      <label htmlFor="acct-mfa-code">{t("mfaCode")}</label>
      <input
        id="acct-mfa-code"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9 ]{6,7}"
        required
        value={code}
        onChange={(event) => setCode(event.target.value)}
      />
      {error && (
        <p className="acct-error" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" busy={busy}>
        {t("mfaConfirm")}
      </Button>
    </form>
  );
}

/** Shown instead of the staff screens until a restricted session finishes its step. */
export function AccountSetupGate() {
  const { t } = useTranslation("staff");
  const restriction = staffAuth.restrictions[0];
  return (
    <section className="staff-login-card acct-setup" aria-labelledby="acct-setup-title">
      <h2 id="acct-setup-title">
        {restriction === "password_change" ? t("choosePasswordTitle") : t("mfaSetupTitle")}
      </h2>
      <Toast tone="info">
        {restriction === "password_change" ? t("choosePasswordWhy") : t("mfaSetupWhy")}
      </Toast>
      {restriction === "password_change" ? <PasswordChangeForm /> : <MfaEnrollment onDone={() => undefined} />}
    </section>
  );
}
