import { FormEvent, useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button, ConnectionIndicator, Toast } from "../components/ui";
import { staffAuth, StaffUser } from "../lib/auth";
import { navigationByRole } from "./staffAccess";

export function StaffShell() {
  const { t } = useTranslation(["common", "staff"]);
  const [user, setUser] = useState<StaffUser | null>(staffAuth.user);
  const [checkingSession, setCheckingSession] = useState(true);
  const [hospitalId, setHospitalId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const hadSession = useRef(false);
  const signingOut = useRef(false);

  useEffect(() => {
    const unsubscribe = staffAuth.subscribe((nextUser) => {
      if (nextUser) hadSession.current = true;
      else if (hadSession.current && !signingOut.current) {
        setError(t("sessionExpired", { ns: "staff" }));
        hadSession.current = false;
      }
      setUser(nextUser);
    });
    void staffAuth.refresh().finally(() => setCheckingSession(false));
    return unsubscribe;
  }, [t]);

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await staffAuth.login(hospitalId.trim(), email.trim(), password);
      setPassword("");
      setError("");
    } catch (cause) {
      const code = cause instanceof Error ? cause.message : "SIGN_IN_FAILED";
      setError(
        code === "ACCOUNT_LOCKED"
          ? t("accountLocked", { ns: "staff" })
          : code === "RATE_LIMITED"
            ? t("rateLimited", { ns: "staff" })
            : code === "DEPENDENCY_UNAVAILABLE"
              ? t("serviceUnavailable", { ns: "staff" })
              : t("invalidCredentials", { ns: "staff" }),
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function signOut() {
    signingOut.current = true;
    try {
      await staffAuth.logout();
      setError("");
    } finally {
      hadSession.current = false;
      signingOut.current = false;
    }
  }

  return (
    <div
      className="staff-app"
      data-layout="staff"
      data-authenticated={user ? "true" : "false"}
    >
      {user ? (
        <aside className="staff-sidebar">
          <a className="staff-brand" href="/staff">
            {t("appName", { ns: "common" })}
          </a>
          <nav aria-label={t("staffNavigation", { ns: "staff" })}>
            {navigationByRole[user.role].map((page) => (
              <NavLink to={`/staff/${page}`} key={page}>
                {t(page, { ns: "staff" })}
              </NavLink>
            ))}
          </nav>
          <span className="staff-demo-label">{user.role}</span>
          <Button onClick={() => void signOut()}>
            {t("signOut", { ns: "staff" })}
          </Button>
        </aside>
      ) : null}
      <div className="staff-content">
        <header className="staff-topbar">
          <div>
            <p className="eyebrow">{t("staffTitle", { ns: "staff" })}</p>
            <h1>{user ? user.name : t("signIn", { ns: "staff" })}</h1>
          </div>
          {user ? (
            <ConnectionIndicator
              state={checkingSession ? "reconnecting" : "live"}
            />
          ) : null}
        </header>
        <main className="staff-main">
          {checkingSession ? (
            <p role="status">{t("checkingSession", { ns: "staff" })}</p>
          ) : user ? (
            <>
              <section
                className="staff-login-card"
                aria-label={t("staffWelcome", { ns: "staff" })}
              >
                <h2>{t("signedInAs", { ns: "staff", role: user.role })}</h2>
                <p>{user.email}</p>
                <p>{t("secureSession", { ns: "staff" })}</p>
              </section>
              <Outlet />
            </>
          ) : (
            <section className="staff-login-card">
              <h2>{t("staffWelcome", { ns: "staff" })}</h2>
              <p>{t("staffSubtitle", { ns: "staff" })}</p>
              <form onSubmit={submitLogin}>
                <label htmlFor="hospital-id">
                  {t("hospitalId", { ns: "staff" })}
                </label>
                <input
                  id="hospital-id"
                  type="text"
                  autoComplete="organization"
                  required
                  value={hospitalId}
                  onChange={(event) => setHospitalId(event.target.value)}
                />
                <label htmlFor="staff-email">
                  {t("email", { ns: "staff" })}
                </label>
                <input
                  id="staff-email"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
                <label htmlFor="staff-password">
                  {t("password", { ns: "staff" })}
                </label>
                <input
                  id="staff-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <Button type="submit" disabled={submitting}>
                  {submitting
                    ? t("signingIn", { ns: "staff" })
                    : t("signIn", { ns: "staff" })}
                </Button>
              </form>
              <div className="staff-notice" aria-live="polite">
                {error ? (
                  <Toast tone="info">{error}</Toast>
                ) : (
                  <p>{t("loginPrivacy", { ns: "staff" })}</p>
                )}
              </div>
              <p className="staff-lockout">
                {t("lockoutNotice", { ns: "staff" })}
              </p>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
