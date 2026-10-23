import { FormEvent, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { Button, ConnectionIndicator, Toast } from "../components/ui";
import { apiOrigin } from "../lib/api";
import { staffAuth, StaffUser, type MfaChallenge } from "../lib/auth";
import { AccountSetupGate } from "./AccountForms";
import { navigationByRole, type StaffPageId } from "./staffAccess";
import {
  Activity,
  BarChart3,
  ClipboardList,
  FileSearch,
  HeartPulse,
  LogOut,
  Settings,
  ShieldCheck,
  Stethoscope,
  UserPlus,
  Users,
  type LucideIcon,
} from "lucide-react";

const NAV_ICONS: Record<StaffPageId, LucideIcon> = {
  queue: Users,
  consultations: Stethoscope,
  patients: ClipboardList,
  reports: BarChart3,
  settings: Settings,
  audit: FileSearch,
  system: Activity,
};

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

const lastHospitalKey = "osa.staff.hospital";
type Hospital = { id: string; name: string };

function rememberedHospital() {
  try {
    return localStorage.getItem(lastHospitalKey) ?? "";
  } catch {
    return "";
  }
}

export function StaffShell() {
  const location = useLocation();
  const judgeMode = new URLSearchParams(location.search).get("demo") === "1";
  const { t } = useTranslation(["common", "staff"]);
  const [user, setUser] = useState<StaffUser | null>(staffAuth.user);
  const [checkingSession, setCheckingSession] = useState(true);
  const [hospitalId, setHospitalId] = useState(rememberedHospital);
  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // After a right password on an account with two-step sign-in: waiting for the code.
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
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

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiOrigin}/auth/hospitals`)
      .then((response) => (response.ok ? response.json() : { items: [] }))
      .then((body: { items?: Hospital[] }) => {
        if (cancelled) return;
        const items = body.items ?? [];
        setHospitals(items);
        setHospitalId((current) =>
          items.some((item) => item.id === current) ? current : (items[0]?.id ?? current),
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  function signInError(code: string) {
    return code === "ACCOUNT_LOCKED"
      ? t("accountLocked", { ns: "staff" })
      : code === "RATE_LIMITED"
        ? t("rateLimited", { ns: "staff" })
        : code === "DEPENDENCY_UNAVAILABLE"
          ? t("serviceUnavailable", { ns: "staff" })
          : code === "INVALID_MFA_CODE"
            ? t("mfaCodeRejected", { ns: "staff" })
            : code === "MFA_CHALLENGE_EXPIRED"
              ? t("mfaChallengeExpired", { ns: "staff" })
              : t("invalidCredentials", { ns: "staff" });
  }

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const pending = await staffAuth.login(hospitalId.trim(), email.trim(), password);
      try {
        localStorage.setItem(lastHospitalKey, hospitalId.trim());
      } catch {
        // Convenience only.
      }
      setPassword("");
      setError("");
      setChallenge(pending);
    } catch (cause) {
      setError(signInError(cause instanceof Error ? cause.message : "SIGN_IN_FAILED"));
    } finally {
      setSubmitting(false);
    }
  }

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challenge) return;
    setError("");
    setSubmitting(true);
    try {
      const answer = mfaCode.trim();
      await staffAuth.verifyMfa(
        challenge,
        useRecovery ? { recoveryCode: answer } : { code: answer.replace(/\s/g, "") },
      );
      setChallenge(null);
      setMfaCode("");
      setUseRecovery(false);
    } catch (cause) {
      const code = cause instanceof Error ? cause.message : "SIGN_IN_FAILED";
      if (code === "MFA_CHALLENGE_EXPIRED" || code === "ACCOUNT_LOCKED") {
        setChallenge(null);
        setMfaCode("");
      }
      setError(signInError(code));
    } finally {
      setSubmitting(false);
    }
  }

  function switchAccount() {
    signingOut.current = true;
    try {
      staffAuth.switchAccount();
      setError("");
      setEmail("");
      setPassword("");
    } finally {
      hadSession.current = false;
      signingOut.current = false;
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

  const isStaffRoot =
    location.pathname === "/staff" || location.pathname === "/staff/";

  return (
    <div
      className="staff-app"
      data-layout="staff"
      data-authenticated={user ? "true" : "false"}
    >
      {user ? (
        <aside className="staff-sidebar">
          <a className="staff-brand" href="/staff">
            <span className="staff-brand-mark" aria-hidden="true">
              <HeartPulse size={20} />
            </span>
            <span className="staff-brand-text">
              {t("appName", { ns: "common" })}
              <small>OPD Management</small>
            </span>
          </a>
          <nav aria-label={t("staffNavigation", { ns: "staff" })}>
            {navigationByRole[user.role].map((page) => (
              <NavLink
                to={{
                  pathname: `/staff/${page}`,
                  search: judgeMode ? location.search : "",
                }}
                key={page}
              >
                {(() => {
                  const Icon = NAV_ICONS[page];
                  return <Icon aria-hidden="true" size={18} />;
                })()}
                <span>{t(page, { ns: "staff" })}</span>
              </NavLink>
            ))}
          </nav>
          <div className="staff-sidebar-footer">
            <div className="staff-user">
              <span className="staff-avatar" aria-hidden="true">
                {initials(user.name)}
              </span>
              <div className="staff-user-meta">
                <span className="staff-demo-label">{user.role}</span>
                <span className="staff-user-name">{user.name}</span>
                <span className="staff-user-email" title={user.email}>
                  {user.email}
                </span>
              </div>
            </div>
            <Button
              variant="secondary"
              size="sm"
              icon={<LogOut aria-hidden="true" size={16} />}
              onClick={() => void signOut()}
            >
              {t("signOut", { ns: "staff" })}
            </Button>
            <NavLink className="staff-switch-account" to="/staff/account">
              <ShieldCheck aria-hidden="true" size={14} />
              {t("myAccount", { ns: "staff" })}
            </NavLink>
            <button
              type="button"
              className="staff-switch-account"
              title={t("useAnotherAccountHint", { ns: "staff" })}
              onClick={switchAccount}
            >
              <UserPlus aria-hidden="true" size={14} />
              {t("useAnotherAccount", { ns: "staff" })}
            </button>
          </div>
        </aside>
      ) : null}
      <div className="staff-content">
        <header className="staff-topbar">
          <div className="staff-topbar-info">
            <p className="eyebrow">{t("staffTitle", { ns: "staff" })}</p>
            <h1>{user ? user.name : t("signIn", { ns: "staff" })}</h1>
          </div>
          {user ? (
            <div className="staff-topbar-meta">
              <span className="staff-role-chip">{user.role}</span>
              <ConnectionIndicator
                state={checkingSession ? "reconnecting" : "live"}
              />
            </div>
          ) : null}
        </header>
        <main
          className="staff-main"
          data-page={user ? "dashboard" : "login"}
        >
          {checkingSession ? (
            <p role="status">{t("checkingSession", { ns: "staff" })}</p>
          ) : user && staffAuth.restrictions.length > 0 ? (
            <AccountSetupGate />
          ) : user ? (
            <>
              {isStaffRoot && (
                <section
                  className="staff-login-card"
                  aria-label={t("staffWelcome", { ns: "staff" })}
                >
                  <h2>{t("signedInAs", { ns: "staff", role: user.role })}</h2>
                  <p>{user.email}</p>
                  <p>{t("secureSession", { ns: "staff" })}</p>
                </section>
              )}
              {judgeMode && (
                <details className="staff-demo-banner">
                  <summary>{t("howItWorks", { ns: "staff" })}</summary>
                  <p>{t("howItWorksCollection", { ns: "staff" })}</p>
                  <p>{t("howItWorksReview", { ns: "staff" })}</p>
                </details>
              )}
              <Outlet />
            </>
          ) : (
            <section className="staff-login-card">
              <h2>{t("staffWelcome", { ns: "staff" })}</h2>
              <p>{t("staffSubtitle", { ns: "staff" })}</p>
              {challenge ? (
                <form onSubmit={submitCode}>
                  <p>{useRecovery ? t("mfaRecoveryPrompt", { ns: "staff" }) : t("mfaPrompt", { ns: "staff" })}</p>
                  <label htmlFor="staff-mfa-code">
                    {useRecovery ? t("recoveryCode", { ns: "staff" }) : t("mfaCode", { ns: "staff" })}
                  </label>
                  <input
                    id="staff-mfa-code"
                    inputMode={useRecovery ? "text" : "numeric"}
                    autoComplete="one-time-code"
                    autoFocus
                    required
                    value={mfaCode}
                    onChange={(event) => setMfaCode(event.target.value)}
                  />
                  <Button type="submit" disabled={submitting}>
                    {submitting ? t("signingIn", { ns: "staff" }) : t("verifyCode", { ns: "staff" })}
                  </Button>
                  <button
                    type="button"
                    className="staff-switch-account"
                    onClick={() => {
                      setUseRecovery((current) => !current);
                      setMfaCode("");
                    }}
                  >
                    {useRecovery ? t("useAuthenticator", { ns: "staff" }) : t("useRecoveryCode", { ns: "staff" })}
                  </button>
                  <button
                    type="button"
                    className="staff-switch-account"
                    onClick={() => {
                      setChallenge(null);
                      setMfaCode("");
                      setError("");
                    }}
                  >
                    {t("backToSignIn", { ns: "staff" })}
                  </button>
                </form>
              ) : (
              <form onSubmit={submitLogin}>
                <label htmlFor="hospital-id">
                  {t("hospitalId", { ns: "staff" })}
                </label>
                {hospitals.length > 0 ? (
                  <select
                    id="hospital-id"
                    required
                    value={hospitalId}
                    onChange={(event) => setHospitalId(event.target.value)}
                  >
                    {hospitals.map((hospital) => (
                      <option key={hospital.id} value={hospital.id}>
                        {hospital.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="hospital-id"
                    type="text"
                    autoComplete="organization"
                    required
                    value={hospitalId}
                    onChange={(event) => setHospitalId(event.target.value)}
                  />
                )}
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
              )}
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
