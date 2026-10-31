export function decodeBase64URL(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("通行密钥参数无效。");
  return Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)).buffer;
}
export function encodeBase64URL(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
export function credentialOptions(options, registering) {
  const converted = { ...options, challenge: decodeBase64URL(options.challenge) };
  if (registering) converted.user = { ...options.user, id: decodeBase64URL(options.user.id) };
  for (const key of ["allowCredentials", "excludeCredentials"]) {
    if (options[key]) converted[key] = options[key].map(item => ({ ...item, id: decodeBase64URL(item.id) }));
  }
  return converted;
}
export function serializeCredential(credential) {
  if (!credential || credential.type !== "public-key") throw new Error("未收到通行密钥。");
  const response = { clientDataJSON: encodeBase64URL(credential.response.clientDataJSON) };
  for (const key of ["attestationObject", "authenticatorData", "signature", "userHandle"]) {
    if (credential.response[key]) response[key] = encodeBase64URL(credential.response[key]);
  }
  if (typeof credential.response.getTransports === "function") response.transports = credential.response.getTransports();
  return { id: credential.id, rawId: encodeBase64URL(credential.rawId), type: credential.type, response,
    authenticatorAttachment: credential.authenticatorAttachment || undefined,
    clientExtensionResults: credential.getClientExtensionResults?.() || {} };
}

export async function authenticatePasskey({ registering = false, code, label, fetcher = fetch, credentials = navigator.credentials, signal } = {}) {
  const flow = registering ? "register" : "login";
  const post = async (stage, body) => {
    const response = await fetcher(`/v1/web/auth/${flow}/${stage}`, {
      method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", signal,
      headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error?.message || "验证未完成，请重试。");
    return data;
  };
  const options = await post("options", registering ? { code, label } : {});
  const publicKey = credentialOptions(options.publicKey, registering);
  const credential = await credentials[registering ? "create" : "get"]({ publicKey, signal });
  return post("verify", { challenge_id: options.challenge_id, credential: serializeCredential(credential) });
}
