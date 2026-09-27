/**
 * Dev-полифилл нативной кнопки «Назад» для браузерного превью.
 * Включается ТОЛЬКО при ?dev_user_id= и отсутствии настоящего MAX Bridge —
 * внутри MAX вебвью всегда есть настоящий WebApp.BackButton, полифилл не срабатывает.
 */
export function installDevBackButton(): void {
  if (new URLSearchParams(window.location.search).get("dev_user_id") === null) return;
  if (window.WebApp?.BackButton) return;

  let visible = false;
  let handler: (() => void) | null = null;

  const btn = document.createElement("button");
  btn.textContent = "←";
  btn.type = "button";
  btn.title = "Назад (dev-полифилл WebApp.BackButton)";
  Object.assign(btn.style, {
    position: "fixed",
    top: "12px",
    left: "12px",
    zIndex: "1000",
    width: "40px",
    height: "40px",
    borderRadius: "50%",
    border: "1px solid rgba(128,128,128,0.4)",
    background: "rgba(60,60,60,0.7)",
    color: "#fff",
    fontSize: "18px",
    lineHeight: "1",
    cursor: "pointer",
  } satisfies Partial<CSSStyleDeclaration>);
  btn.onclick = () => handler?.();

  window.WebApp = {
    ...(window.WebApp ?? {}),
    BackButton: {
      get isVisible() {
        return visible;
      },
      show() {
        visible = true;
        document.body.appendChild(btn);
      },
      hide() {
        visible = false;
        btn.remove();
      },
      onClick(cb: () => void) {
        handler = cb;
      },
      offClick(cb: () => void) {
        if (handler === cb) handler = null;
      },
    },
  };
}
