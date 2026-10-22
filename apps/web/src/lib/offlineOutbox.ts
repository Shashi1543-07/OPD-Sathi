export type OfflineSubmission = {
  idempotency_key: string;
  language: "en" | "hi" | "mr";
  consent_granted: boolean;
  consent_version: string;
  consent_sha256: string;
  age: number;
  sex: "female" | "male" | "other" | "unknown";
  chief_complaint: string;
  duration: string;
  severity: number;
  conditions: string;
  medications: string;
  allergies: string;
  danger_tile_ids: string[];
};

type StoredSubmission = {
  id: string;
  token: string;
  iv: ArrayBuffer;
  ciphertext: ArrayBuffer;
  attempts: number;
  nextAttemptAt: number;
};

/**
 * Whether this browser can keep an encrypted offline outbox. Web Crypto's `subtle` exists only
 * in a secure context (HTTPS or localhost), so a page opened over http://<LAN address> cannot.
 */
export function offlineOutboxAvailable(): boolean {
  return typeof globalThis.crypto?.subtle !== "undefined" && typeof indexedDB !== "undefined";
}

const DATABASE = "opd-saathi-offline-v1";
const SUBMISSIONS = "submissions";
const KEYS = "keys";
const META = "meta";
const KEY_ID = "offline-aes-key";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      database.createObjectStore(SUBMISSIONS, { keyPath: "id" });
      database.createObjectStore(KEYS, { keyPath: "id" });
      database.createObjectStore(META, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Offline storage unavailable"));
  });
}

function requestValue<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Offline storage unavailable"));
  });
}

async function encryptionKey(database: IDBDatabase): Promise<CryptoKey> {
  const transaction = database.transaction(KEYS, "readwrite");
  const store = transaction.objectStore(KEYS);
  const existing = await requestValue(store.get(KEY_ID)) as { id: string; key: CryptoKey } | undefined;
  if (existing) return existing.key;
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  store.put({ id: KEY_ID, key });
  await transactionDone(transaction);
  return key;
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = transaction.onerror = () => reject(transaction.error ?? new Error("Offline storage unavailable"));
  });
}

function localPrefix(kioskToken: string): string {
  try {
    const payload = kioskToken.split(".")[1];
    const decoded = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as { sub?: string };
    return (decoded.sub ?? "kiosk").replace(/[^a-z0-9]/gi, "").slice(-4).toUpperCase() || "KIOS";
  } catch {
    return "KIOS";
  }
}

export async function queueOfflineSubmission(
  submission: OfflineSubmission,
  kioskToken: string,
): Promise<{ id: string; token: string }> {
  const database = await openDatabase();
  try {
    const key = await encryptionKey(database);
    const counterTransaction = database.transaction(META, "readwrite");
    const meta = counterTransaction.objectStore(META);
    const current = await requestValue(meta.get("counter")) as { id: string; value: number } | undefined;
    const counter = (current?.value ?? 0) + 1;
    meta.put({ id: "counter", value: counter });
    await transactionDone(counterTransaction);

    const iv = crypto.getRandomValues(new Uint8Array(12));
    const bytes = new TextEncoder().encode(JSON.stringify(submission));
    const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, bytes);
    const id = submission.idempotency_key;
    const token = `L-${localPrefix(kioskToken)}-${String(counter).padStart(3, "0")}`;
    const storeTransaction = database.transaction(SUBMISSIONS, "readwrite");
    storeTransaction.objectStore(SUBMISSIONS).add({
      id, token, iv: iv.buffer, ciphertext, attempts: 0, nextAttemptAt: 0,
    } satisfies StoredSubmission);
    await transactionDone(storeTransaction);
    return { id, token };
  } finally {
    database.close();
  }
}

/**
 * Submissions due for another try. ``ignoreBackoff`` returns every one: used when the network
 * has just come back, which is what the backoff was waiting for.
 */
export async function pendingOfflineSubmissions({ ignoreBackoff = false } = {}): Promise<Array<{
  record: StoredSubmission;
  submission: OfflineSubmission;
}>> {
  const database = await openDatabase();
  try {
    const key = await encryptionKey(database);
    const transaction = database.transaction(SUBMISSIONS, "readonly");
    const stored = await requestValue(transaction.objectStore(SUBMISSIONS).getAll()) as StoredSubmission[];
    const now = Date.now();
    const pending: Array<{ record: StoredSubmission; submission: OfflineSubmission }> = [];
    for (const record of stored) {
      if (!ignoreBackoff && record.nextAttemptAt > now) continue;
      const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv: record.iv }, key, record.ciphertext);
      pending.push({ record, submission: JSON.parse(new TextDecoder().decode(plaintext)) as OfflineSubmission });
    }
    return pending;
  } finally {
    database.close();
  }
}

export async function removeOfflineSubmission(id: string): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(SUBMISSIONS, "readwrite");
    transaction.objectStore(SUBMISSIONS).delete(id);
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}

export async function deferOfflineSubmission(record: StoredSubmission): Promise<void> {
  const attempts = record.attempts + 1;
  const delaySeconds = Math.min(300, 2 ** Math.min(attempts, 8));
  const database = await openDatabase();
  try {
    const transaction = database.transaction(SUBMISSIONS, "readwrite");
    transaction.objectStore(SUBMISSIONS).put({
      ...record,
      attempts,
      nextAttemptAt: Date.now() + delaySeconds * 1000,
    } satisfies StoredSubmission);
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}

export async function offlineSubmissionCount(): Promise<number> {
  const database = await openDatabase();
  try {
    return await requestValue(database.transaction(SUBMISSIONS, "readonly").objectStore(SUBMISSIONS).count());
  } finally {
    database.close();
  }
}
