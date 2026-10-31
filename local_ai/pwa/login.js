import { authenticatePasskey } from "/pwa/auth.js";

const login = document.querySelector("#login");
const form = document.querySelector("#register-form");
const status = document.querySelector("#login-status");
const modeSwitch = document.querySelector("#auth-switch");
let controller;
const supported = isSecureContext && !!window.PublicKeyCredential && !!navigator.credentials;
function message(text, error = false) { status.textContent = text; status.classList.toggle("error", error); }
function busy(value) {
  for (const control of document.querySelectorAll("button,input")) control.disabled = value;
  modeSwitch.hidden = value;
}
function showMode() {
  controller?.abort();
  const setup = location.hash === "#setup";
  login.hidden = setup;
  document.querySelector("#login-hint").hidden = setup;
  document.querySelector("#register-panel").hidden = !setup;
  document.querySelector("#auth-title").textContent = setup ? "创建通行密钥" : "登录";
  document.querySelector("#auth-intro").textContent = setup
    ? "输入 Mac 上的注册码，在此设备保存你的通行密钥。"
    : "使用已保存的通行密钥，继续会话与任务。";
  modeSwitch.textContent = setup ? "已有通行密钥？登录" : "首次使用 · 创建通行密钥";
  modeSwitch.href = setup ? "#login" : "#setup";
  document.querySelector("#invite-code").value = "";
  document.title = setup ? "首次设置 · Friday" : "登录 · Friday";
  message(supported ? "" : "当前浏览器无法使用通行密钥。请用 Safari 等支持的浏览器通过 HTTPS 打开。", !supported);
}
async function perform(registering) {
  if (controller) return;
  if (!supported) { message("请在支持通行密钥的浏览器中，通过 HTTPS 打开此页面。", true); return; }
  controller = new AbortController(); busy(true); message("等待设备验证…");
  const codeInput = document.querySelector("#invite-code");
  try {
    await authenticatePasskey({ registering, code: codeInput.value.trim(), label: document.querySelector("#device-label").value.trim(), signal: controller.signal });
    codeInput.value = ""; message("验证完成，正在进入…"); const nativeFlow = new URL(location.href).searchParams.get("native");
    location.replace(nativeFlow && /^[A-Za-z0-9_-]{43}$/.test(nativeFlow) ? `/native/connect?flow=${nativeFlow}` : "/app");
  } catch (error) {
    const cancelled = ["NotAllowedError", "AbortError"].includes(error.name);
    if (registering !== (location.hash === "#setup")) {
      codeInput.value = "";
    } else if (registering) {
      codeInput.value = "";
      const reason = cancelled ? "创建通行密钥已取消或超时。" : error.message || "创建通行密钥未完成。";
      message(`${reason} 注册码已在开始验证时失效，请在 Mac 生成新码后重试。`, !cancelled);
    } else {
      message(cancelled ? "尚未创建通行密钥？请点下方「首次使用」。已有密钥可重新登录。" : error.message || "登录验证未完成，请重试。", !cancelled);
    }
    busy(false);
  } finally { controller = null; }
}
login.addEventListener("click", () => perform(false));
form.addEventListener("submit", event => { event.preventDefault(); if (form.reportValidity()) perform(true); });
window.addEventListener("hashchange", showMode);
showMode();
window.addEventListener("pagehide", () => { controller?.abort(); document.querySelector("#invite-code").value = ""; });
if (!supported) { busy(true); message("当前浏览器无法使用通行密钥。请用 Safari 等支持的浏览器通过 HTTPS 打开。", true); }
if ("serviceWorker" in navigator && isSecureContext) navigator.serviceWorker.register("/pwa/sw.js", { scope: "/" }).catch(() => {});
