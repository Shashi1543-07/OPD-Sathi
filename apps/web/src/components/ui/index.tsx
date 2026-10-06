import {
  AlertTriangle,
  Check,
  CircleHelp,
  Clock3,
  Headphones,
  LoaderCircle,
  Mic,
  Volume2,
} from "lucide-react";
import {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type RefObject,
  type ReactNode,
  useEffect,
  useRef,
} from "react";
import { useTranslation } from "react-i18next";
import { useLanguageStore } from "../../languageStore";
import type { Language } from "../../i18n";
import "./ui.css";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "kiosk" | "md" | "sm";
  busy?: boolean;
  icon?: ReactNode;
};

export function Button({
  variant = "primary",
  size = "md",
  busy = false,
  disabled,
  icon,
  children,
  className = "",
  ...props
}: ButtonProps) {
  const { t } = useTranslation("common");
  return (
    <button
      className={`ui-button ui-button--${variant} ui-button--${size} ${className}`.trim()}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy ? <LoaderCircle className="ui-spin" aria-hidden="true" /> : icon}
      {children}
      {busy && <span className="ui-sr-only">{t("busy")}</span>}
    </button>
  );
}

const languageNames: { code: Language; label: string }[] = [
  { code: "hi", label: "हिन्दी" },
  { code: "mr", label: "मराठी" },
  { code: "en", label: "English" },
];

export function LanguageTile({
  language,
  label,
}: {
  language: Language;
  label?: string;
}) {
  const active = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const entry = languageNames.find((item) => item.code === language);
  return (
    <button
      className="ui-language-tile"
      type="button"
      aria-pressed={active === language}
      onClick={() => setLanguage(language)}
    >
      <span
        className="ui-language-script"
        lang={language === "xa" ? "en" : language}
      >
        {label ?? entry?.label ?? "English"}
      </span>
      {active === language && <Check aria-hidden="true" />}
    </button>
  );
}

export function DangerTile({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      className="ui-danger-tile"
      type="button"
      aria-pressed={selected}
      onClick={onClick}
    >
      <AlertTriangle aria-hidden="true" />
      <span>{label}</span>
      {selected && <Check aria-hidden="true" />}
    </button>
  );
}

export function ChoiceButton({
  children,
  selected = false,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean }) {
  return (
    <button
      className={`ui-choice-button ${className}`.trim()}
      type="button"
      aria-pressed={selected}
      {...props}
    >
      {children}
    </button>
  );
}

export function ChipGroup({
  options,
  value,
  onChange,
  label,
  multiple = false,
}: {
  options: string[];
  value: string[];
  onChange: (value: string[]) => void;
  label: string;
  multiple?: boolean;
}) {
  const toggle = (option: string) => {
    if (!multiple) {
      onChange(value.includes(option) ? [] : [option]);
      return;
    }
    onChange(
      value.includes(option)
        ? value.filter((item) => item !== option)
        : [...value, option],
    );
  };
  return (
    <div className="ui-chip-group" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          className="ui-chip"
          type="button"
          key={option}
          aria-pressed={value.includes(option)}
          onClick={() => toggle(option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

export function NumberPad({
  value,
  onChange,
  maxLength = 10,
}: {
  value: string;
  onChange: (value: string) => void;
  maxLength?: number;
}) {
  const { t } = useTranslation("common");
  const append = (digit: string) => {
    if (value.length < maxLength) onChange(value + digit);
  };
  return (
    <div
      className="ui-number-pad"
      role="group"
      aria-label={t("numberPadLabel")}
    >
      {"123456789".split("").map((digit) => (
        <button
          className="ui-number-key"
          key={digit}
          type="button"
          onClick={() => append(digit)}
        >
          {digit}
        </button>
      ))}
      <span aria-hidden="true" />
      <button
        className="ui-number-key"
        type="button"
        onClick={() => append("0")}
      >
        0
      </button>
      <button
        className="ui-number-key"
        type="button"
        onClick={() => onChange(value.slice(0, -1))}
        aria-label={t("delete")}
      >
        ⌫
      </button>
    </div>
  );
}

export function FacesScale({
  value,
  onChange,
}: {
  value?: number;
  onChange: (value: number) => void;
}) {
  const { t } = useTranslation("common");
  const faces = [
    "😌",
    "🙂",
    "🙂",
    "😐",
    "😐",
    "🙁",
    "🙁",
    "😟",
    "😟",
    "😣",
    "😣",
  ];
  return (
    <div
      className="ui-faces-scale"
      role="group"
      aria-label={t("facesScaleLabel")}
    >
      {faces.map((face, number) => (
        <button
          className="ui-face-option"
          type="button"
          key={number}
          aria-label={`${number}`}
          aria-pressed={value === number}
          onClick={() => onChange(number)}
        >
          <span aria-hidden="true">{face}</span>
          <span>{number}</span>
        </button>
      ))}
    </div>
  );
}

export function ProgressDots({
  current,
  total,
}: {
  current: number;
  total: number;
}) {
  const { t } = useTranslation("common");
  const bounded = Math.min(Math.max(current, 0), total);
  return (
    <div
      className="ui-progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={bounded}
      aria-label={t("progress", { current, total })}
    >
      <div className="ui-progress-dots" aria-hidden="true">
        {Array.from({ length: total }, (_, index) => (
          <span className={index < bounded ? "is-complete" : ""} key={index} />
        ))}
      </div>
      <span>{t("progress", { current, total })}</span>
    </div>
  );
}

export function UrgencyBadge({
  level,
}: {
  level: "urgent" | "priority" | "routine";
}) {
  const { t } = useTranslation("staff");
  const Icon =
    level === "urgent" ? AlertTriangle : level === "priority" ? Clock3 : Check;
  return (
    <span className={`ui-urgency ui-urgency--${level}`}>
      <Icon aria-hidden="true" /> {t(level)}
    </span>
  );
}

export function ConfidenceChip({ value }: { value: number }) {
  const { t } = useTranslation("staff");
  const low = value < 0.7;
  return (
    <span
      className={`ui-confidence ${low ? "ui-confidence--low" : ""}`}
      title={low ? t("pleaseCheck") : undefined}
    >
      {Math.round(value * 100)}%
      {low && <span className="ui-confidence-tooltip">{t("pleaseCheck")}</span>}
    </span>
  );
}

export function HelpButton({ onClick }: { onClick?: () => void }) {
  const { t } = useTranslation("common");
  return (
    <Button
      variant="secondary"
      size="kiosk"
      icon={<CircleHelp aria-hidden="true" />}
      onClick={onClick}
    >
      {t("callStaff")}
    </Button>
  );
}

export function ReadAloudButton({ onClick }: { onClick?: () => void }) {
  const { t } = useTranslation("common");
  return (
    <Button
      variant="ghost"
      icon={<Volume2 aria-hidden="true" />}
      onClick={onClick}
      aria-label={t("readAloud")}
    >
      {t("readAloud")}
    </Button>
  );
}

export function ConnectionIndicator({
  state,
}: {
  state: "live" | "reconnecting" | "offline";
}) {
  const { t } = useTranslation("common");
  return (
    <span className={`ui-connection ui-connection--${state}`}>
      <span aria-hidden="true" />
      {t(state)}
    </span>
  );
}

export function OfflineBanner() {
  const { t } = useTranslation("common");
  return (
    <div className="ui-offline-banner" role="status">
      <AlertTriangle aria-hidden="true" />
      <span>{t("offlineMessage")}</span>
    </div>
  );
}

export function IdleWarningDialog({
  open,
  onContinue,
  onEnd,
}: {
  open: boolean;
  onContinue: () => void;
  onEnd: () => void;
}) {
  const { t } = useTranslation("common");
  const dialogRef = useRef<HTMLElement>(null);
  useDialogAccessibility(open, onContinue, dialogRef);
  if (!open) return null;
  return (
    <div className="ui-dialog-backdrop">
      <section
        className="ui-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="idle-title"
        aria-describedby="idle-description"
        tabIndex={-1}
        ref={dialogRef}
      >
        <h2 id="idle-title">{t("idleTitle")}</h2>
        <p id="idle-description">{t("idleMessage")}</p>
        <div className="ui-dialog-actions">
          <Button variant="secondary" onClick={onEnd}>
            {t("cancel")}
          </Button>
          <Button onClick={onContinue}>{t("continue")}</Button>
        </div>
      </section>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  children,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("common");
  const dialogRef = useRef<HTMLElement>(null);
  useDialogAccessibility(open, onCancel, dialogRef);
  if (!open) return null;
  return (
    <div className="ui-dialog-backdrop">
      <section
        className="ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        tabIndex={-1}
        ref={dialogRef}
      >
        <h2 id="confirm-title">{title}</h2>
        {children && <div>{children}</div>}
        <div className="ui-dialog-actions">
          <Button variant="secondary" onClick={onCancel}>
            {t("cancel")}
          </Button>
          <Button onClick={onConfirm}>{t("confirm")}</Button>
        </div>
      </section>
    </div>
  );
}

function useDialogAccessibility(
  open: boolean,
  onClose: () => void,
  dialogRef: RefObject<HTMLElement | null>,
) {
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const dialog = dialogRef.current;
    const focusable =
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])';
    const controls = dialog
      ? Array.from(dialog.querySelectorAll<HTMLElement>(focusable))
      : [];
    (controls[0] ?? dialog)?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeRef.current();
      } else if (event.key === "Tab" && dialog) {
        const currentControls = Array.from(
          dialog.querySelectorAll<HTMLElement>(focusable),
        );
        if (currentControls.length === 0) {
          event.preventDefault();
          dialog.focus();
        } else if (
          event.shiftKey &&
          document.activeElement === currentControls[0]
        ) {
          event.preventDefault();
          currentControls.at(-1)?.focus();
        } else if (
          !event.shiftKey &&
          document.activeElement === currentControls.at(-1)
        ) {
          event.preventDefault();
          currentControls[0]?.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      previousFocus?.focus();
    };
  }, [open, dialogRef]);
}

export function Toast({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "info" | "success" | "error";
}) {
  return (
    <div
      className={`ui-toast ui-toast--${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

export function Skeleton({
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`ui-skeleton ${className}`.trim()}
      aria-hidden="true"
      {...props}
    />
  );
}

export function EmptyState({
  title,
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  const { t } = useTranslation("common");
  return (
    <div className="ui-state-card">
      <CircleHelp aria-hidden="true" />
      <h3>{title ?? t("emptyTitle")}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function ErrorState({
  title,
  children,
  onRetry,
}: {
  title?: string;
  children?: ReactNode;
  onRetry?: () => void;
}) {
  const { t } = useTranslation("common");
  return (
    <div className="ui-state-card ui-state-card--error">
      <AlertTriangle aria-hidden="true" />
      <h3>{title ?? t("errorTitle")}</h3>
      {children && <p>{children}</p>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          {t("retry")}
        </Button>
      )}
    </div>
  );
}

export function StatCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: ReactNode;
  detail?: string;
}) {
  return (
    <article className="ui-stat-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  );
}

export function LanguageSelector() {
  const { t } = useTranslation("kiosk");
  return (
    <div
      className="ui-language-selector"
      role="group"
      aria-label={t("chooseLanguage")}
    >
      <LanguageTile language="hi" label={t("languageHindi")} />
      <LanguageTile language="mr" label={t("languageMarathi")} />
      <LanguageTile language="en" label={t("languageEnglish")} />
    </div>
  );
}

export function ReadAloudStubNote() {
  const { t } = useTranslation("kiosk");
  return (
    <span className="ui-sr-only">
      <Headphones aria-hidden="true" />
      {t("readAloudStub")}
    </span>
  );
}

export function VoiceOrb({
  disabled = true,
  active = false,
  label,
  onClick,
}: {
  disabled?: boolean;
  active?: boolean;
  label?: string;
  onClick?: () => void;
}) {
  const { t } = useTranslation("kiosk");
  return (
    <button
      className={`ui-voice-orb${active ? " is-active" : ""}`}
      type="button"
      disabled={disabled}
      aria-label={label ?? t("voiceComing")}
      aria-pressed={active}
      title={label ?? t("voiceComing")}
      onClick={onClick}
    >
      <Mic aria-hidden="true" />
    </button>
  );
}
